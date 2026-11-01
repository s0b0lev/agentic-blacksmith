"""End-to-end test of the browser helper the verify and design stages use on the droplet.

Runs only where Node, a Chrome/Chromium binary and ``agent/tools/node_modules`` exist
(``cd agent/tools && npm ci``); CI without them skips, the droplet has all three.
"""

import base64
import http.server
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BROWSE = ROOT / "tools" / "browse.mjs"
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
BROWSER = next((b for b in BROWSERS if shutil.which(b)), None)
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    not (NODE and BROWSER and (ROOT / "tools" / "node_modules").is_dir()),
    reason="needs node, a Chrome binary and agent/tools/node_modules",
)

LOGIN_FORM = b"""<!doctype html><title>Login</title><h1>Sign in</h1>
<form method="post" action="/login">
<input id="email" name="email"><input id="password" name="password" type="password">
<button type="submit">Go</button></form>"""


class App(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/login":
            self._send(200, LOGIN_FORM)
        elif self.path == "/secret":
            if "session=ok" in self.headers.get("Cookie", ""):
                self._send(200, b"<h1>SECRET OK</h1>")
            else:
                self._send(403, b"<h1>LOGIN REQUIRED</h1>")
        elif self.path == "/basic":
            want = "Basic " + base64.b64encode(b"u:p").decode()
            if self.headers.get("Authorization") == want:
                self._send(200, b"<h1>BASIC OK</h1>")
            else:
                self._send(401, b"nope", [("WWW-Authenticate", 'Basic realm="t"')])
        else:
            self._send(404, b"nope")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode()
        if self.path == "/login" and "email=a%40b.c" in body and "password=pw" in body:
            self._send(303, b"", [("Set-Cookie", "session=ok; Path=/"), ("Location", "/secret")])
        else:
            self._send(400, b"<h1>BAD LOGIN</h1>")


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), App)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def browse(tmp_path: Path, steps: list, *args: str) -> subprocess.CompletedProcess:
    script = tmp_path / f"steps-{len(list(tmp_path.glob('steps-*.json')))}.json"
    script.write_text(json.dumps(steps))
    return subprocess.run(
        [NODE, str(BROWSE), "--profile", str(tmp_path / "profile"), *args, str(script)],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "BLACKSMITH_BROWSER": BROWSER,
             "HOME": str(tmp_path)},
    )


def test_login_then_screenshot_and_dump(server, tmp_path):
    shot, dump = tmp_path / "01-secret.png", tmp_path / "secret.html"
    proc = browse(tmp_path, [
        {"goto": f"{server}/login"},
        {"fill": ["#email", "a@b.c"]},
        {"fill": ["#password", "pw"]},
        {"click": "button[type=submit]"},
        {"wait": "h1"},
        {"screenshot": str(shot)},
        {"dump": str(dump)},
    ])
    assert proc.returncode == 0, proc.stderr
    assert "SECRET OK" in dump.read_text()
    assert shot.stat().st_size > 1000
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    assert events[0]["step"] == 1 and events[0]["url"].endswith("/login")
    assert any(e.get("url", "").endswith("/secret") for e in events)


def test_session_persists_in_the_profile_between_runs(server, tmp_path):
    first = browse(tmp_path, [
        {"goto": f"{server}/login"},
        {"fill": ["#email", "a@b.c"]},
        {"fill": ["#password", "pw"]},
        {"click": "button[type=submit]"},
    ])
    assert first.returncode == 0, first.stderr
    dump = tmp_path / "again.html"
    second = browse(tmp_path, [{"goto": f"{server}/secret"}, {"dump": str(dump)}])
    assert second.returncode == 0, second.stderr
    assert "SECRET OK" in dump.read_text()


def test_basic_auth_and_viewport(server, tmp_path):
    dump, shot = tmp_path / "basic.html", tmp_path / "02-basic-mobile.png"
    proc = browse(tmp_path, [
        {"goto": f"{server}/basic"},
        {"viewport": "390x844"},
        {"screenshot": str(shot)},
        {"dump": str(dump)},
    ], "--auth", "u:p")
    assert proc.returncode == 0, proc.stderr
    assert "BASIC OK" in dump.read_text()
    # PNG header: width and height big-endian at bytes 16..24
    head = shot.read_bytes()[:24]
    assert int.from_bytes(head[16:20], "big") == 390 and int.from_bytes(head[20:24], "big") == 844


def test_failed_step_exits_nonzero_with_the_step_number(server, tmp_path):
    proc = browse(tmp_path, [{"goto": f"{server}/login"}, {"click": "#does-not-exist"}])
    assert proc.returncode != 0
    assert "step 2" in proc.stderr and "#does-not-exist" in proc.stderr
