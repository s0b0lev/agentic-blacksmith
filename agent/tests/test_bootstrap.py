import json
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
    for n in range(1, 11):
        m = re.search(rf'^step {n} "[^"]+"$', text, re.M)
        assert m, f"step {n} missing"
        positions.append(m.start())
    assert positions == sorted(positions)
    assert "step 11 " not in text


def test_observability_step_is_third_and_before_clone():
    text = BOOTSTRAP.read_text()
    assert re.search(r'^step 3 "observability"$', text, re.M)
    assert text.index('step 3 "observability"') < text.index('step 6 "git clone')
    body = text[text.index('step 3 "observability"'):text.index('step 4 "')]
    assert "OBSERVABILITY_ENABLED" in body and "GRAFANA_CLOUD_TOKEN" in body
    assert "observability disabled" in body


def test_observability_step_never_dies():
    text = BOOTSTRAP.read_text()
    func = text[text.index("setup_observability() {"):text.index('step 1 "')]
    assert "die " not in func and "exit " not in func
    body = text[text.index('step 3 "observability"'):text.index('step 4 "')]
    assert "if ! setup_observability" in body
    assert 'log "WARNING' in body


def test_observability_renders_config_and_runs_alloy_as_root():
    text = BOOTSTRAP.read_text()
    func = text[text.index("setup_observability() {"):text.index('step 1 "')]
    assert "python3 -P -m blacksmith alloy-config" in func
    assert "/etc/alloy/config.alloy" in func and "chmod 0600 /etc/alloy/config.alloy" in func
    assert "/etc/default/alloy" in func and "GC_TOKEN=" in func
    assert "alloy.service.d/override.conf" in func and "User=root" in func
    assert "systemctl enable --now alloy" in func
    assert "apt.grafana.com" in func
    assert "curl -fsSL --retry 3 https://apt.grafana.com/gpg.key" in func


def test_observability_validates_before_install_and_keeps_diagnostics_out_of_log():
    text = BOOTSTRAP.read_text()
    func = text[text.index("setup_observability() {"):text.index('step 1 "')]
    validate = func.index('alloy validate "$rendered"')
    assert validate < func.index('mv "$rendered" /etc/alloy/config.alloy')
    assert 'GC_LOKI_URL="$loki_url"' in func[:validate] and "GC_TOKEN=x" not in func
    invalid = func.index("alloy config invalid")
    line = func[func.rindex("\n", 0, invalid):func.index("\n", invalid)]
    count = '$(wc -l <<< "$out")'
    assert count in line and "$out" not in line.replace(count, "")  # only a line count
    assert 'rm -f "$rendered"' in func[invalid:func.index('mv "$rendered"')]


def test_lifetime_timer_enabled_before_any_apt_call():
    text = BOOTSTRAP.read_text()
    body = text[text.index('step 1 "'):]
    enable = body.index("systemctl enable --now blacksmith-lifetime.timer\n")
    first_apt = body.index("apt_get ")
    assert enable < first_apt
    early = body[:enable]
    assert '"$AGENT/systemd/blacksmith-lifetime.timer" /etc/systemd/system/' in early
    assert re.search(r"sed .*OnBootSec=.*LIFETIME_HOURS", early)
    assert early.index("systemctl daemon-reload") < enable


def test_failure_trap_posts_bootstrap_failed_comment():
    text = BOOTSTRAP.read_text()
    assert "trap on_exit EXIT" in text
    assert "<!-- blacksmith:bootstrap-failed -->" in text
    assert re.search(r'^\s+STEP="\$2"$', text, re.M)  # the step helper records the name


def _functions(text: str, *names: str) -> str:
    return "".join(re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M).group(0)
                   for name in names)


def test_report_failure_posts_comment_with_step(tmp_path):
    fns = _functions(BOOTSTRAP.read_text(), "file_get", "report_failure")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/args\ncat > {tmp_path}/body\n')
    (bindir / "curl").chmod(0o755)
    env_file, conf = tmp_path / "env", tmp_path / "config"
    env_file.write_text("BLACKSMITH_GITHUB_TOKEN=ghp_x\n")
    conf.write_text("ISSUE=12\nREPO=o/r\n")
    script = (f"set -euo pipefail\nENV_FILE={env_file}\nCONF={conf}\nLOG=/var/log/b.log\n"
              f"LIFETIME_HOURS=24\nSTEP_N=7\nSTEP='setup commands'\nlog() {{ :; }}\n{fns}report_failure\n")
    proc = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                          env={"PATH": f"{bindir}:/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    args = (tmp_path / "args").read_text()
    assert "Authorization: Bearer ghp_x" in args
    assert "https://api.github.com/repos/o/r/issues/12/comments" in args
    body = json.loads((tmp_path / "body").read_text())["body"]
    assert body.startswith(
        "Blacksmith bootstrap failed at step 7 (setup commands): see /var/log/b.log on the "
        "droplet. The server will self-destruct after 24 h."
    )
    assert body.endswith("<!-- blacksmith:bootstrap-failed -->")


def test_bootstrap_smoke_tests_claude_token():
    text = BOOTSTRAP.read_text()
    install = text.index("claude.ai/install.sh")
    smoke = text.index('claude -p "Reply with the single word ok." --max-turns 1 --output-format json')
    assert install < smoke
    assert "timeout 120 claude -p" in text
    assert '"is_error"' in text
    die = text.index("die ", smoke)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text[die:text.index("\n", die)]


def test_bootstrap_blocks_metadata_from_containers():
    text = BOOTSTRAP.read_text()
    rule = "DOCKER-USER -d 169.254.169.254 -j DROP"
    assert f"iptables -C {rule}" in text and f"iptables -I {rule}" in text
    assert text.index("systemctl enable --now docker") < text.index(f"iptables -I {rule}")
    assert text.index("169.254.169.254/metadata/v1/interfaces") < text.index(f"iptables -I {rule}")


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


def test_run_sh_self_destruct_falls_back_to_curl(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").write_text("#!/bin/sh\nexit 1\n")
    (bindir / "curl").write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/args\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "BLACKSMITH_GITHUB_TOKEN": "ghs_abc", "REPO": "o/r", "ISSUE": "5"}
    proc = subprocess.run(["bash", str(RUN_SH), "self-destruct"], env=env, text=True,
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr
    args = (tmp_path / "args").read_text()
    assert "https://api.github.com/repos/o/r/dispatches" in args
    assert '{"event_type":"blacksmith-self-destruct","client_payload":{"issue":5}}' in args
    assert "Authorization: Bearer ghs_abc" in args


def test_run_sh_self_destruct_no_fallback_when_runner_succeeds(tmp_path):
    bindir = _fake_python(tmp_path)
    (bindir / "curl").write_text(f"#!/bin/sh\ntouch {tmp_path}/curl-ran\n")
    (bindir / "curl").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.run(["bash", str(RUN_SH), "self-destruct"], env=env, text=True,
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "curl-ran").exists()


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
