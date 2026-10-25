import os
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
    assert ctx.repo == "o/r"
    assert ctx.dashboard_url == ""


def test_context_from_config_file_dashboard_url(tmp_path):
    from blacksmith.__main__ import context_from, read_config_file

    path, _ = _conf(tmp_path, DASHBOARD_URL="https://g.example/d/x")
    ctx = context_from(read_config_file(path), {})
    assert ctx.dashboard_url == "https://g.example/d/x"


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


class StartupGitHub:
    def __init__(self, fail_issue=False):
        self.fail_issue = fail_issue
        self.upserts = []

    def issue(self, n):
        if self.fail_issue:
            raise RuntimeError("gh: 401 Bad credentials")
        raise AssertionError("not reached")

    def upsert_comment(self, target, kind, body):
        self.upserts.append((target, kind, body))
        return 1


def test_run_config_error_exits_2(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli

    gh = StartupGitHub()
    monkeypatch.setattr(cli, "_github", lambda conf: gh)
    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text("version: 2\n")
    assert cli.main(["--config-file", str(path), "run"]) == 2
    assert "version: must be 1" in capsys.readouterr().err
    (target, kind, body), = gh.upserts
    assert (target, kind) == (7, "progress")
    assert body.startswith("Blacksmith could not start: ConfigError(")


def test_run_startup_failure_posts_and_logs(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli

    gh = StartupGitHub(fail_issue=True)
    monkeypatch.setattr(cli, "_github", lambda conf: gh)
    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    assert cli.main(["--config-file", str(path), "run"]) == 2
    message = "Blacksmith could not start: RuntimeError('gh: 401 Bad credentials')"
    assert gh.upserts == [(7, "progress", message)]
    assert message in capsys.readouterr().err
    assert message in (repo / ".blacksmith/log.md").read_text()


def test_run_missing_runner_config_key_reported(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli

    gh = StartupGitHub()
    monkeypatch.setattr(cli, "_github", lambda conf: gh)
    path, _ = _conf(tmp_path)
    path.write_text("\n".join(line for line in path.read_text().splitlines()
                              if not line.startswith("PREVIEW_HOST=")) + "\n")
    assert cli.main(["--config-file", str(path), "run"]) == 2
    assert gh.upserts == [(7, "progress", "Blacksmith could not start: KeyError('PREVIEW_HOST')")]


def test_run_startup_post_failure_still_exits_2(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli

    def no_github(conf):
        raise RuntimeError("no gh")

    monkeypatch.setattr(cli, "_github", no_github)
    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    assert cli.main(["--config-file", str(path), "run"]) == 2
    err = capsys.readouterr().err
    assert "Blacksmith could not start: RuntimeError('no gh')" in err
    assert "could not post the start-up failure" in err


EXAMPLE_YML = AGENT_DIR.parent / "examples/sample-shop/blacksmith.yml"


def test_help_lists_review_subcommands():
    proc = subprocess.run([sys.executable, "-m", "blacksmith", "--help"], cwd=AGENT_DIR,
                          text=True, capture_output=True)
    for name in ("poll", "review-round", "self-destruct"):
        assert name in proc.stdout


def test_poll_and_review_round_print_round(tmp_path, capsys, monkeypatch):
    import blacksmith.review
    from blacksmith.__main__ import main

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    calls = []
    monkeypatch.setattr(blacksmith.review, "review_round",
                        lambda cfg, ctx, gh, run, **kwargs: calls.append(ctx.issue) or True)
    assert main(["--config-file", str(path), "poll"]) == 0
    assert capsys.readouterr().out == "round: yes\n"
    assert main(["--config-file", str(path), "review-round"]) == 0
    assert capsys.readouterr().out == "round: yes\n"
    assert calls == [7, 7]


def test_poll_exits_0_on_error(tmp_path, capsys, monkeypatch):
    import blacksmith.review
    from blacksmith.__main__ import main

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())

    def boom(*a, **k):
        raise RuntimeError("gh down")

    monkeypatch.setattr(blacksmith.review, "review_round", boom)
    assert main(["--config-file", str(path), "poll"]) == 0
    out = capsys.readouterr()
    assert out.out == "round: no\n" and "RuntimeError('gh down')" in out.err


def test_self_destruct_exit_codes(tmp_path, capsys, monkeypatch):
    import blacksmith.review
    from blacksmith.__main__ import main

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text("version: 2\n")  # invalid config still self-destructs
    seen = []
    monkeypatch.setattr(blacksmith.review, "self_destruct",
                        lambda cfg, ctx, gh: seen.append((cfg, ctx.issue)))
    assert main(["--config-file", str(path), "self-destruct"]) == 0
    assert seen == [(None, 7)]

    def boom(*a):
        raise RuntimeError("dispatch 403")

    monkeypatch.setattr(blacksmith.review, "self_destruct", boom)
    capsys.readouterr()
    assert main(["--config-file", str(path), "self-destruct"]) == 1
    assert "dispatch 403" in capsys.readouterr().err


def _config_get(*args):
    return subprocess.run([sys.executable, "-m", "blacksmith", "config", "--get", *args],
                          cwd=AGENT_DIR, text=True, capture_output=True)


def test_config_get_subcommand():
    proc = _config_get("preview.project", "--config", str(EXAMPLE_YML))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "blacksmith-preview\n"


def test_config_get_scalars_and_booleans():
    assert _config_get("branch_prefix", "--config", str(EXAMPLE_YML)).stdout == "blacksmith/issue-\n"
    assert _config_get("design.figma", "--config", str(EXAMPLE_YML)).stdout == "true\n"
    assert _config_get("limits.lifetime_hours", "--config", str(EXAMPLE_YML)).stdout == "168\n"


def test_config_get_observability_values(tmp_path):
    cfg = tmp_path / "blacksmith.yml"
    cfg.write_text(
        "version: 1\npreview: {compose: c.yml, project: p, routes: [{path: '/*', to: 'web:1'}]}\n"
        "observability: {enabled: true, loki_url: https://l.example/push, loki_user: '7', public: false}\n"
    )
    assert _config_get("observability.enabled", "--config", str(cfg)).stdout == "true\n"
    assert _config_get("observability.public", "--config", str(cfg)).stdout == "false\n"


def test_config_get_empty_list_prints_nothing():
    proc = _config_get("allowed_labelers", "--config", str(EXAMPLE_YML))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_config_get_list_prints_lines():
    proc = _config_get("setup", "--config", str(EXAMPLE_YML))
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == 3 and lines[1] == "cd admin && npm ci"
    checks = _config_get("checks", "--config", str(EXAMPLE_YML)).stdout.splitlines()
    assert checks[-1] == "cd api && .venv/bin/pytest -q" and len(checks) == 4


def test_config_get_allowed_labelers_prints_lines(tmp_path):
    cfg = tmp_path / "blacksmith.yml"
    text = EXAMPLE_YML.read_text()
    assert "\nallowed_labelers: []" in text
    cfg.write_text(text.replace("\nallowed_labelers: []", "\nallowed_labelers: [alice, bob]", 1))
    proc = _config_get("allowed_labelers", "--config", str(cfg))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "alice\nbob\n"


def test_config_get_unknown_key_exits_3():
    proc = _config_get("preview.nope", "--config", str(EXAMPLE_YML))
    assert proc.returncode == 3
    assert "preview.nope" in proc.stderr and proc.stdout == ""


def test_config_get_defaults_to_repo_config(tmp_path, capsys):
    from blacksmith.__main__ import main

    path = tmp_path / "config"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    # only the keys cloud-init writes plus REPO_ROOT: no BRANCH or PREVIEW_HOST yet
    path.write_text(f"ISSUE=7\nREPO=o/r\nCONFIG_PATH=blacksmith.yml\nREPO_ROOT={repo}\n")
    assert main(["--config-file", str(path), "config", "--get", "preview.auth"]) == 0
    assert capsys.readouterr().out == "basic\n"


def test_fetch_issue_writes_issue_md(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli
    from blacksmith.github import Issue

    class Gh:
        def issue(self, n):
            return Issue(n, "T", "B", "alice", (), "open", ())

        def issue_markdown(self, issue):
            return f"# {issue.title} #{issue.number}\n"

    path = tmp_path / "config"
    repo = tmp_path / "repo"
    repo.mkdir()
    path.write_text(f"ISSUE=7\nREPO=o/r\nCONFIG_PATH=blacksmith.yml\nREPO_ROOT={repo}\n")
    monkeypatch.setattr(cli, "_github", lambda conf: Gh())
    assert cli.main(["--config-file", str(path), "fetch-issue"]) == 0
    out = repo / ".blacksmith" / "issue.md"
    assert capsys.readouterr().out == f"{out}\n"
    assert out.read_text() == "# T #7\n"


def _telegram_env(monkeypatch, token="123:abc", chat="-1001"):
    for key, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _capture_first_pass(monkeypatch, seen):
    import blacksmith.stages
    from blacksmith.state import State

    def fake(cfg, ctx, gh, run, **kwargs):
        seen.update(kwargs, repo=ctx.repo)
        return State(7, "blacksmith/issue-7", stage="done")

    monkeypatch.setattr(blacksmith.stages, "run_first_pass", fake)


def test_run_passes_the_telegram_notifier_from_the_environment(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli
    from blacksmith.notify import Telegram

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    monkeypatch.setattr(cli, "_github", lambda conf: StartupGitHub())
    seen = {}
    _capture_first_pass(monkeypatch, seen)
    _telegram_env(monkeypatch)
    assert cli.main(["--config-file", str(path), "run"]) == 0
    assert isinstance(seen["notifier"], Telegram)
    assert (seen["notifier"].token, seen["notifier"].chat_id) == ("123:abc", "-1001")
    assert seen["repo"] == "o/r"


def test_run_without_telegram_values_passes_no_notifier(tmp_path, capsys, monkeypatch):
    import blacksmith.__main__ as cli

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    monkeypatch.setattr(cli, "_github", lambda conf: StartupGitHub())
    seen = {}
    _capture_first_pass(monkeypatch, seen)
    _telegram_env(monkeypatch, token=None, chat=None)
    assert cli.main(["--config-file", str(path), "run"]) == 0
    assert seen["notifier"] is None


def test_poll_passes_the_telegram_notifier(tmp_path, capsys, monkeypatch):
    import blacksmith.review
    from blacksmith.__main__ import main

    path, repo = _conf(tmp_path)
    (repo / "blacksmith.yml").write_text(EXAMPLE_YML.read_text())
    seen = {}
    monkeypatch.setattr(blacksmith.review, "review_round",
                        lambda cfg, ctx, gh, run, **kwargs: seen.update(kwargs) or True)
    _telegram_env(monkeypatch)
    assert main(["--config-file", str(path), "poll"]) == 0
    assert seen["notifier"].chat_id == "-1001"


def test_alloy_config_command(tmp_path):
    env = tmp_path / "env"; env.write_text("GRAFANA_CLOUD_TOKEN=glc_abcdefgh\n")
    conf = tmp_path / "config"
    conf.write_text("OBSERVABILITY_ENABLED=true\nLOKI_URL=https://l\nLOKI_USER=1\nPROMETHEUS_URL=\n"
                    "PROMETHEUS_USER=\nAGENT_STREAM=true\nREPO=acme/shop\nISSUE=12\nBRANCH=b\n")
    out = subprocess.run([sys.executable, "-m", "blacksmith", "alloy-config", "--env", str(env),
                          "--runner-config", str(conf)], capture_output=True, text=True, cwd=AGENT_DIR)
    assert out.returncode == 0 and 'job = "runner"' in out.stdout
    conf.write_text(conf.read_text().replace("OBSERVABILITY_ENABLED=true", "OBSERVABILITY_ENABLED=false"))
    out = subprocess.run([sys.executable, "-m", "blacksmith", "alloy-config", "--env", str(env),
                          "--runner-config", str(conf)], capture_output=True, text=True, cwd=AGENT_DIR)
    assert out.returncode == 3 and "observability disabled" in out.stderr


def test_dashboard_command_requires_token(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "GRAFANA_API_TOKEN"}
    out = subprocess.run([sys.executable, "-m", "blacksmith", "dashboard", "delete",
                          "--grafana-url", "https://g.example", "--repo", "a/b", "--issue", "1"],
                         capture_output=True, text=True, cwd=AGENT_DIR, env=env)
    assert out.returncode == 2 and "GRAFANA_API_TOKEN" in out.stderr


def test_dashboard_create_prints_link(monkeypatch, capsys):
    from blacksmith import __main__ as cli

    class FakeGrafana:
        def __init__(self, url, token): self.url = url
        def upsert_dashboard(self, d): return d["uid"]
        def share_public(self, uid): return "tok"
        def unshare(self, uid): raise AssertionError("not called when public")
    monkeypatch.setenv("GRAFANA_API_TOKEN", "t")
    monkeypatch.setattr("blacksmith.dashboard.Grafana", FakeGrafana)
    code = cli.main(["dashboard", "create", "--grafana-url", "https://g.example", "--repo", "a/b",
                     "--issue", "1", "--branch", "b", "--public", "true", "--loki-uid", "L",
                     "--prom-uid", "P", "--agent-stream", "true", "--metrics", "false"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "https://g.example/public-dashboards/tok"
