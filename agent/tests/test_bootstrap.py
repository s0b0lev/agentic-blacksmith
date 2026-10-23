import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
BOOTSTRAP = AGENT_DIR / "bootstrap.sh"
RUN_SH = AGENT_DIR / "systemd" / "run.sh"
PRE_PUSH = AGENT_DIR / "hooks" / "pre-push"
SCRIPTS = (BOOTSTRAP, RUN_SH, PRE_PUSH)
SYSTEMD = AGENT_DIR / "systemd"
SERVICES = ("blacksmith-run.service", "blacksmith-poll.service", "blacksmith-lifetime.service")
TIMERS = ("blacksmith-poll.timer", "blacksmith-lifetime.timer")


def parse_unit(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Sections mapped to their (key, value) lines; systemd allows repeated keys."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        assert current is not None, f"{path.name}: key outside a section: {line}"
        key, sep, value = line.partition("=")
        assert sep, f"{path.name}: not a key=value line: {line}"
        sections[current].append((key, value))
    return sections


def values(section: list[tuple[str, str]], key: str) -> list[str]:
    return [v for k, v in section if k == key]


def test_bootstrap_shellcheck():
    if shutil.which("uvx") is None:
        pytest.skip("uvx not installed")
    proc = subprocess.run(
        ["uvx", "--from", "shellcheck-py", "shellcheck", "-S", "warning", *map(str, SCRIPTS)],
        text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_bootstrap_syntax(script):
    proc = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    head = script.read_text().splitlines()[:3]
    assert head[0] == "#!/usr/bin/env bash" and "set -euo pipefail" in head
    assert os.access(script, os.X_OK), f"{script.name} is not executable"


def test_units_parse():
    for name in SERVICES:
        unit = parse_unit(SYSTEMD / name)
        assert "Unit" in unit and "Service" in unit, name
        svc = unit["Service"]
        assert values(svc, "User") == ["blacksmith"], name
        assert values(svc, "Group") == ["blacksmith"], name
        assert values(svc, "Type") == ["oneshot"], name
        assert values(svc, "WorkingDirectory") == ["/home/blacksmith/repo"], name
        assert values(svc, "EnvironmentFile") == ["/etc/blacksmith/env", "/etc/blacksmith/config"]
        env = " ".join(values(svc, "Environment"))
        assert "PYTHONPATH=/opt/blacksmith/agent" in env and "HOME=/home/blacksmith" in env
        (cmd,) = values(svc, "ExecStart")
        assert cmd.startswith("/opt/blacksmith/agent/systemd/run.sh "), name
    for name in TIMERS:
        unit = parse_unit(SYSTEMD / name)
        assert "Unit" in unit and "Timer" in unit and "Install" in unit, name
        assert values(unit["Install"], "WantedBy") == ["timers.target"], name


def test_unit_commands_and_schedules():
    run = parse_unit(SYSTEMD / "blacksmith-run.service")["Service"]
    assert values(run, "ExecStart") == ["/opt/blacksmith/agent/systemd/run.sh run"]
    assert values(run, "Restart") == ["no"]
    assert values(run, "TimeoutStartSec") == ["infinity"]
    poll = parse_unit(SYSTEMD / "blacksmith-poll.service")["Service"]
    assert values(poll, "ExecStart") == ["/opt/blacksmith/agent/systemd/run.sh poll"]
    life = parse_unit(SYSTEMD / "blacksmith-lifetime.service")["Service"]
    assert values(life, "ExecStart") == ["/opt/blacksmith/agent/systemd/run.sh self-destruct"]
    poll_t = parse_unit(SYSTEMD / "blacksmith-poll.timer")["Timer"]
    assert values(poll_t, "OnBootSec") == ["5min"]
    assert values(poll_t, "OnUnitActiveSec") == ["60s"]
    assert values(poll_t, "Persistent") == ["false"]
    life_t = parse_unit(SYSTEMD / "blacksmith-lifetime.timer")["Timer"]
    assert values(life_t, "OnBootSec") == ["168h"]


def test_bootstrap_renders_lifetime_from_config():
    text = BOOTSTRAP.read_text()
    assert re.search(r"sed .*OnBootSec=.*LIFETIME_HOURS", text)


def test_bootstrap_has_numbered_steps_in_order():
    text = BOOTSTRAP.read_text()
    positions = []
    for n in range(1, 10):
        m = re.search(rf"== step {n}: [^=]+ ==", text)
        assert m, f"step {n} missing"
        positions.append(m.start())
    assert positions == sorted(positions)
    assert "== step 10:" not in text


def test_bootstrap_starts_units_in_order():
    text = BOOTSTRAP.read_text()
    reload_at = text.index("systemctl daemon-reload")
    timers_at = text.index("systemctl enable --now blacksmith-lifetime.timer blacksmith-poll.timer")
    run_at = text.index("systemctl start --no-block blacksmith-run.service")
    assert reload_at < timers_at < run_at


def _fake_python(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "python3"
    fake.write_text('#!/bin/sh\necho "GH_TOKEN=$GH_TOKEN"\necho "ARGS=$*"\n')
    fake.chmod(0o755)
    return bindir


def test_run_sh_exports_gh_token(tmp_path):
    bindir = _fake_python(tmp_path)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "BLACKSMITH_GITHUB_TOKEN": "ghs_abc"}
    proc = subprocess.run(["bash", str(RUN_SH), "poll"], env=env, text=True,
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["GH_TOKEN=ghs_abc", "ARGS=-P -m blacksmith poll"]


def _hook(stdin: str, tmp_path: Path, **env) -> subprocess.CompletedProcess:
    base = {"PATH": "/usr/bin:/bin", "BLACKSMITH_CONFIG_FILE": str(tmp_path / "missing")}
    return subprocess.run(["bash", str(PRE_PUSH), "origin", "https://github.com/o/r.git"],
                          input=stdin, env={**base, **env}, text=True, capture_output=True)


HOOK_ENV = {"BRANCH": "blacksmith/issue-1", "ISSUE": "1"}


def test_pre_push_hook_rejects_main(tmp_path):
    proc = _hook("refs/heads/x 1111 refs/heads/main 2222\n", tmp_path, **HOOK_ENV)
    assert proc.returncode == 1
    assert "refs/heads/main" in proc.stderr


def test_pre_push_hook_accepts_branch_and_assets(tmp_path):
    stdin = ("refs/heads/blacksmith/issue-1 1111 refs/heads/blacksmith/issue-1 2222\n"
             "refs/heads/a 3333 refs/heads/blacksmith-assets/issue-1 0000\n")
    proc = _hook(stdin, tmp_path, **HOOK_ENV)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("remote_ref", [
    "refs/heads/other/blacksmith/issue-1",      # merely ends with the branch
    "refs/heads/blacksmith/issue-12",
    "refs/heads/blacksmith-assets/issue-2",
    "refs/tags/blacksmith/issue-1",
])
def test_pre_push_hook_requires_exact_refs(tmp_path, remote_ref):
    stdin = ("refs/heads/blacksmith/issue-1 1111 refs/heads/blacksmith/issue-1 2222\n"
             f"refs/heads/x 1111 {remote_ref} 2222\n")
    proc = _hook(stdin, tmp_path, **HOOK_ENV)
    assert proc.returncode == 1
    assert remote_ref in proc.stderr


def test_pre_push_hook_falls_back_to_config_file(tmp_path):
    conf = tmp_path / "config"
    conf.write_text("ISSUE=4\nREPO=o/r\nBRANCH=blacksmith/issue-4\n")
    ok = _hook("refs/heads/b 1 refs/heads/blacksmith/issue-4 2\n", tmp_path,
               BLACKSMITH_CONFIG_FILE=str(conf))
    assert ok.returncode == 0, ok.stderr
    bad = _hook("refs/heads/b 1 refs/heads/main 2\n", tmp_path,
                BLACKSMITH_CONFIG_FILE=str(conf))
    assert bad.returncode == 1


def test_pre_push_hook_without_branch_rejects(tmp_path):
    proc = _hook("refs/heads/b 1 refs/heads/main 2\n", tmp_path)
    assert proc.returncode == 1
    assert "BRANCH" in proc.stderr


def test_set_conf_replaces_or_appends(tmp_path):
    """bootstrap's set_conf/file_get helpers keep the runner config idempotent."""
    text = BOOTSTRAP.read_text()
    fns = "".join(re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M).group(0)
                  for name in ("file_get", "set_conf"))
    conf = tmp_path / "config"
    conf.write_text('ISSUE=7\nREPO="o/r"\nPREVIEW_ZONE=\n')
    script = (f"set -euo pipefail\nCONF={conf}\n{fns}"
              "set_conf BRANCH b/issue-7\nset_conf BRANCH b/issue-7\nset_conf ISSUE 8\n"
              'echo "$(file_get "$CONF" REPO)|$(file_get "$CONF" PREVIEW_ZONE)|$(file_get "$CONF" NOPE)"\n')
    proc = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "o/r||\n"
    lines = conf.read_text().splitlines()
    assert lines.count("BRANCH=b/issue-7") == 1 and "ISSUE=8" in lines and "ISSUE=7" not in lines
