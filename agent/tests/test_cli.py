import subprocess
import sys
from pathlib import Path

from blacksmith.runner import subprocess_runner

AGENT_DIR = Path(__file__).resolve().parent.parent


def test_version_flag():
    proc = subprocess.run(
        [sys.executable, "-m", "blacksmith", "--version"],
        cwd=AGENT_DIR,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "0.1.0"


def test_subprocess_runner_timeout():
    result = subprocess_runner(["sleep", "2"], timeout=1)
    assert result.code == 124


def test_subprocess_runner_timeout_kills_process_group(tmp_path):
    import os
    import time

    pidfile = tmp_path / "pid"
    result = subprocess_runner(
        ["bash", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], timeout=1
    )
    assert result.code == 124
    pid = int(pidfile.read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError(f"process {pid} still alive")


def _conf(tmp_path, **extra):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    values = {"ISSUE": "7", "REPO": "o/r", "BRANCH": '"blacksmith/issue-7"', "BASE_BRANCH": "main",
              "PREVIEW_HOST": "'pr-7.example.com'", "REPO_ROOT": str(repo),
              "CONFIG_PATH": "blacksmith.yml", "PROMPTS_DIR": str(tmp_path / "prompts"), **extra}
    path = tmp_path / "config"
    path.write_text("# written by bootstrap\n\n"
                    + "".join(f"{k}={v}\n" for k, v in values.items()))
    return path, repo


def test_help_lists_subcommands():
    proc = subprocess.run([sys.executable, "-m", "blacksmith", "--help"], cwd=AGENT_DIR,
                          text=True, capture_output=True)
    assert proc.returncode == 0
    for name in ("run", "preview", "status"):
        assert name in proc.stdout


def test_context_from_config_file(tmp_path):
    from blacksmith.__main__ import context_from, read_config_file

    path, repo = _conf(tmp_path)
    conf = read_config_file(path)
    assert conf["BRANCH"] == "blacksmith/issue-7" and conf["PREVIEW_HOST"] == "pr-7.example.com"
    ctx = context_from(conf, {"HOME": "/h"})
    assert (ctx.issue, ctx.branch, ctx.base_branch) == (7, "blacksmith/issue-7", "main")
    assert ctx.work_dir == repo / ".blacksmith"
    assert ctx.prompts_dir == tmp_path / "prompts"
    assert ctx.env == {"HOME": "/h"}


def test_status_without_state(tmp_path, capsys):
    from blacksmith.__main__ import main

    path, _ = _conf(tmp_path)
    assert main(["--config-file", str(path), "status"]) == 0
    assert capsys.readouterr().out.strip() == "no state"


def test_status_prints_state(tmp_path, capsys):
    from blacksmith.__main__ import main
    from blacksmith.state import State, save_state

    path, repo = _conf(tmp_path)
    (repo / ".blacksmith").mkdir()
    save_state(repo / ".blacksmith/state.json", State(7, "b", stage="verify"))
    assert main(["--config-file", str(path), "status"]) == 0
    assert '"stage": "verify"' in capsys.readouterr().out


def test_run_config_error_exits_2(tmp_path, capsys):
    from blacksmith.__main__ import main

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text("version: 2\n")
    assert main(["--config-file", str(path), "run"]) == 2
    assert "version: must be 1" in capsys.readouterr().err
