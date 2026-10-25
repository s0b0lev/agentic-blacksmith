import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

from blacksmith.claude import StageResult
from blacksmith.config import load_config
from blacksmith.github import PR, Issue
from blacksmith.preview import PreviewResult
from blacksmith.runner import CommandResult
from blacksmith.stages import RunContext, load_prompt, run_first_pass
from blacksmith.state import load_state
from conftest import FakeRunner

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/sample-shop/blacksmith.yml"
PROMPTS = ("plan", "build", "preview_fix", "verify", "build_fix", "design", "pr")
NOW = "2026-10-23T12:00:00+00:00"
UP = PreviewResult(True, "https://pr-7.example.com/", ("preview", "pw123"), None, "")
DOWN = PreviewResult(False, "https://pr-7.example.com/", None, "up app failed", "== logs ==\nboom")
PYTEST = "bash -lc cd api && .venv/bin/pytest -q"
RUFF = "bash -lc cd api && .venv/bin/ruff check"


class EventRunner(FakeRunner):
    """FakeRunner that also logs pushes and shell checks to a shared event list."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def __call__(self, cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            self.events.append("push")
        elif cmd[0] == "bash":
            self.events.append("bash")
        return super().__call__(cmd, **kwargs)


def _write(name: str, text: str):
    return lambda work_dir: (work_dir / name).write_text(text)


class FakeClaude:
    """Identifies the stage from the prompt's first line and writes its artifact."""

    def __init__(self, work_dir: Path, events: list[str]) -> None:
        self.work_dir, self.events = work_dir, events
        self.calls: list[dict] = []
        self.actions = {
            "plan": _write("plan.md", "Goal: add a scale widget\n"),
            "verify": _write("verify.md", "RESULT: pass\nAll acceptance criteria met.\n"),
            "design": _write("design.md", "No mismatches.\n"),
            "pr": _write("pr.md", "Add scale widget\n\nCloses #7\n\nAdds the widget.\n"),
        }

    @property
    def names(self) -> list[str]:
        return [c["name"] for c in self.calls]

    def prompts(self, name: str) -> list[str]:
        return [c["prompt"] for c in self.calls if c["name"] == name]

    def __call__(self, prompt, *, cwd, max_turns, timeout_s, transcript, run, env, extra_args=()):
        name = prompt.splitlines()[0].removeprefix("PROMPT:")
        self.events.append(f"claude:{name}")
        self.calls.append({"name": name, "prompt": prompt, "timeout_s": timeout_s,
                           "max_turns": max_turns, "transcript": transcript, "env": env})
        action = self.actions.get(name)
        if isinstance(action, list):
            action = action.pop(0)
        if action:
            action(self.work_dir)
        return StageResult(True, False, "done", 3, transcript, "")


class FakeDeploy:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.results = [UP]
        self.calls = 0

    def __call__(self, cfg, pctx, run):
        self.events.append("deploy")
        self.calls += 1
        res = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if res.ok:
            meta = {"url": res.url, "host": pctx.host, "auth": list(res.auth) if res.auth else None}
            (pctx.work_dir / "preview.json").write_text(json.dumps(meta))
        return res


class FakeGitHub:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.upserts: list[tuple[int, str, str]] = []
        self.prs: list[dict] = []
        self.reviews: list[tuple[int, str]] = []
        self.fail_create_pr = False

    def issue(self, n):
        return Issue(n, "Scale widget", "Please add it.", "alice", ("blacksmith",), "open", ())

    def issue_markdown(self, issue):
        return f"# {issue.title}\n"

    def upsert_comment(self, target, kind, body):
        self.upserts.append((target, kind, body))
        return 99

    def create_pr(self, head, base, title, body, draft):
        self.events.append("create_pr")
        if self.fail_create_pr:
            raise RuntimeError("gh down")
        self.prs.append({"head": head, "base": base, "title": title, "body": body, "draft": draft})
        return PR(1, "https://x/pull/1", head)

    def request_review(self, pr, user):
        self.events.append("request_review")
        self.reviews.append((pr, user))
        return True


def make_env(tmp_path, *, env=None, changed="storefront/app/page.tsx\n", **limits):
    events: list[str] = []
    repo = tmp_path / "repo"
    repo.mkdir()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in PROMPTS:
        (prompts / f"{name}.md").write_text(
            f"PROMPT:{name}\ndiag={{diagnostics}}\nchecks={{check_results}}\nverify={{verify}}\n"
        )
    cfg = load_config(EXAMPLE)
    if limits:
        cfg = dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, **limits))
    ctx = RunContext(
        repo_root=repo, work_dir=repo / ".blacksmith", issue=7, branch="blacksmith/issue-7",
        base_branch="main", host="pr-7.example.com", prompts_dir=prompts, repo="acme/shop",
        env={"FIGMA_API_KEY": "figma-key-123", "HOME": "/home/blacksmith"} if env is None else env,
    )
    run = EventRunner(events)
    run.responses["git diff"] = CommandResult(0, changed, "")
    return SimpleNamespace(
        cfg=cfg, ctx=ctx, run=run, events=events, gh=FakeGitHub(events),
        claude=FakeClaude(ctx.work_dir, events), deploy=FakeDeploy(events),
    )


def go(e):
    return run_first_pass(e.cfg, e.ctx, e.gh, e.run, claude=e.claude, deploy=e.deploy,
                          now_iso=lambda: NOW, notifier=getattr(e, "notifier", None))


def test_happy_path_calls_stages_in_order_and_opens_pr(tmp_path):
    e = make_env(tmp_path)
    state = go(e)
    assert e.events == [
        "claude:plan", "claude:build", "push", "deploy",
        "bash", "bash", "bash", "bash", "bash", "claude:verify",
        "claude:design", "claude:pr", "push", "create_pr", "request_review",
    ]
    assert len(e.gh.upserts) >= 4
    assert all(u[:2] == (7, "progress") for u in e.gh.upserts)
    final = e.gh.upserts[-1][2]
    assert "on branch `blacksmith/issue-7`" in final
    assert "### Plan\n\nGoal: add a scale widget" in final
    assert "Preview is up at https://pr-7.example.com/" in final
    assert "Opened https://x/pull/1" in final
    assert e.gh.prs[0]["draft"] is False
    assert (e.gh.prs[0]["head"], e.gh.prs[0]["base"]) == ("blacksmith/issue-7", "main")
    assert e.gh.reviews == [(1, "alice")]
    assert (state.stage, state.pr_number, state.pr_url) == ("done", 1, "https://x/pull/1")
    assert state.last_seen == NOW and state.progress_comment_id == 99
    assert load_state(e.ctx.work_dir / "state.json") == state
    assert (e.ctx.work_dir / "issue.md").read_text() == "# Scale widget\n"
    assert (e.ctx.work_dir / "shots").is_dir()
    assert "Opened https://x/pull/1" in (e.ctx.work_dir / "log.md").read_text()


def test_existing_issue_md_is_kept(tmp_path):
    e = make_env(tmp_path)
    e.ctx.work_dir.mkdir()
    (e.ctx.work_dir / "issue.md").write_text("from bootstrap\n")
    go(e)
    assert (e.ctx.work_dir / "issue.md").read_text() == "from bootstrap\n"


def test_missing_plan_salvages(tmp_path):
    e = make_env(tmp_path)
    e.claude.actions["plan"] = None
    state = go(e)
    assert e.claude.names == ["plan"]
    assert e.deploy.calls == 0
    assert e.events[-1] == "create_pr" and "push" in e.events
    pr = e.gh.prs[0]
    assert pr["draft"] is True
    assert pr["title"] == "[blacksmith] Scale widget (incomplete)"
    assert "Blacksmith stopped at stage `plan`." in pr["body"]
    assert "Reason: plan.md not written" in pr["body"]
    assert "<details><summary>Log</summary>" in pr["body"]
    assert e.gh.reviews == []
    assert (state.stage, state.pr_number, state.last_seen) == ("salvaged", 1, NOW)
    assert "Stopped at stage plan: plan.md not written. Opened draft https://x/pull/1" in (
        e.gh.upserts[-1][2])


def test_preview_failure_retries_twice_then_salvages(tmp_path):
    e = make_env(tmp_path)
    e.deploy.results = [DOWN]
    e.ctx.work_dir.mkdir()
    (e.ctx.work_dir / "preview.json").write_text('{"url": "https://stale/"}')  # earlier run
    state = go(e)
    assert e.claude.names == ["plan", "build", "preview_fix", "preview_fix"]
    assert e.deploy.calls == 3
    assert "diag=up app failed\n\n== logs ==\nboom" in e.claude.prompts("preview_fix")[0]
    assert e.gh.prs[0]["draft"] is True
    assert "stage `preview`" in e.gh.prs[0]["body"]
    assert "**Preview:**" not in e.gh.prs[0]["body"]
    assert state.stage == "salvaged"


def test_preview_fix_recovers(tmp_path):
    e = make_env(tmp_path)
    e.deploy.results = [DOWN, UP]
    state = go(e)
    assert e.claude.names.count("preview_fix") == 1
    assert state.stage == "done" and e.gh.prs[0]["draft"] is False


def test_verify_hard_check_failure_loops_build_fix_max_twice(tmp_path):
    e = make_env(tmp_path)
    e.run.responses[PYTEST] = CommandResult(1, "", "1 failed")
    state = go(e)
    assert e.claude.names == ["plan", "build", "verify", "build_fix", "verify",
                              "build_fix", "verify"]
    assert e.deploy.calls == 3
    assert e.events.count("push") == 4  # build, two fix loops, salvage
    fix_prompt = e.claude.prompts("build_fix")[0]
    assert "| cd api && .venv/bin/pytest -q | required | FAIL code 1 |" in fix_prompt
    assert "verify=RESULT: pass" in fix_prompt
    assert e.gh.prs[0]["draft"] is True
    assert "Reason: verify failed after 2 fix loops" in e.gh.prs[0]["body"]
    assert "**Preview:** https://pr-7.example.com/" in e.gh.prs[0]["body"]
    assert state.stage == "salvaged"


def test_verify_result_fail_triggers_one_build_fix(tmp_path):
    e = make_env(tmp_path)
    e.claude.actions["verify"] = [_write("verify.md", "\nRESULT: fail\nbutton missing\n"),
                                  _write("verify.md", "RESULT: pass\n")]
    state = go(e)
    assert e.claude.names == ["plan", "build", "verify", "build_fix", "verify", "design", "pr"]
    assert "verify=\nRESULT: fail\nbutton missing" in e.claude.prompts("build_fix")[0]
    assert state.stage == "done"


def test_missing_verify_md_salvages(tmp_path):
    e = make_env(tmp_path)
    e.claude.actions["verify"] = None
    go(e)
    assert "Reason: verify.md not written" in e.gh.prs[0]["body"]


def test_soft_check_failure_does_not_loop(tmp_path):
    e = make_env(tmp_path)
    e.run.responses[RUFF] = CommandResult(1, "", "130 findings")
    state = go(e)
    assert "build_fix" not in e.claude.names
    verify_prompt = e.claude.prompts("verify")[0]
    assert "| soft | FAIL code 1 |" in verify_prompt
    assert "| cd admin && npm run typecheck | required | pass |" in verify_prompt
    assert state.stage == "done"


def test_checks_run_in_repo_with_login_shell(tmp_path):
    e = make_env(tmp_path)
    go(e)
    bash = [(c, k) for c, k in zip(e.run.calls, e.run.kwargs) if c[0] == "bash"]
    assert bash[0][0] == ["bash", "-lc", "docker compose -f ops/docker-compose.dev.yml up -d --wait"]
    assert all(k == {"cwd": e.ctx.repo_root, "timeout": 1800} for _, k in bash)


def test_design_skipped_when_no_figma_key(tmp_path):
    e = make_env(tmp_path, env={"HOME": "/home/blacksmith"})
    go(e)
    assert "design" not in e.claude.names
    design = (e.ctx.work_dir / "design.md").read_text()
    assert design.startswith("Skipped: FIGMA_API_KEY not set")
    assert "<summary>Design check</summary>\n\nSkipped: FIGMA_API_KEY not set" in e.gh.prs[0]["body"]


def test_design_skipped_when_no_storefront_changes(tmp_path):
    e = make_env(tmp_path, changed="api/app/main.py\n")
    go(e)
    assert "design" not in e.claude.names
    assert ["git", "diff", "--name-only", "origin/main...HEAD"] in e.run.calls
    assert (e.ctx.work_dir / "design.md").read_text().startswith(
        "Skipped: no changes under storefront/")


def test_pr_body_contains_preview_url_and_login(tmp_path):
    e = make_env(tmp_path)
    go(e)
    pr = e.gh.prs[0]
    assert pr["title"] == "Add scale widget"
    body = pr["body"]
    assert body.startswith("Closes #7\n\nAdds the widget.")
    assert "---\n**Preview:** https://pr-7.example.com/\n" in body
    assert "**Preview login:** `preview` / `pw123`" in body
    assert ("<details><summary>Verification</summary>\n\n"
            "RESULT: pass\nAll acceptance criteria met.\n") in body
    assert "<details><summary>Design check</summary>\n\nNo mismatches." in body


def test_stage_timeout_values(tmp_path):
    e = make_env(tmp_path, first_run_minutes=120, max_turns_per_stage=50)
    e.deploy.results = [DOWN, UP]
    go(e)
    timeouts = {c["name"]: c["timeout_s"] for c in e.claude.calls}
    assert timeouts == {"plan": 720, "build": 3240, "preview_fix": 360, "verify": 1440,
                        "design": 720, "pr": 360}
    assert {c["max_turns"] for c in e.claude.calls} == {50}
    assert [c["transcript"].name for c in e.claude.calls[:3]] == [
        "01-plan.json", "02-build.json", "03-preview_fix.json"]
    assert all(c["env"] == dict(e.ctx.env) for c in e.claude.calls)


def test_build_fix_uses_verify_budget(tmp_path):
    e = make_env(tmp_path, first_run_minutes=60)
    e.run.responses[PYTEST] = CommandResult(1, "", "")
    go(e)
    assert {c["timeout_s"] for c in e.claude.calls if c["name"] == "build_fix"} == {720}


def test_unexpected_exception_salvages_with_traceback(tmp_path):
    e = make_env(tmp_path)

    def explode(work_dir):
        raise RuntimeError("kaboom")

    e.claude.actions["build"] = explode
    state = go(e)
    assert state.stage == "salvaged"
    assert "Reason: RuntimeError('kaboom')" in e.gh.prs[0]["body"]
    assert "Traceback" in (e.ctx.work_dir / "log.md").read_text()


def test_salvage_survives_create_pr_failure(tmp_path):
    e = make_env(tmp_path)
    e.claude.actions["plan"] = None
    e.gh.fail_create_pr = True
    e.run.responses["git push"] = CommandResult(1, "", "rejected")
    state = go(e)
    assert state.stage == "salvaged" and state.pr_number is None
    log = (e.ctx.work_dir / "log.md").read_text()
    assert "rejected" in log and "gh down" in log
    assert ("Stopped at stage plan: plan.md not written. Could not open a draft PR "
            "(see runner log).") in e.gh.upserts[-1][2]


def test_load_prompt_keeps_unknown_braces(tmp_path):
    e = make_env(tmp_path)
    (e.ctx.prompts_dir / "x.md").write_text(
        "Issue {issue} on {branch} at {host}, notes in {work_dir}.\n"
        "Preview {preview_url} ({preview_auth}). Extra: {diag}\n"
        '```js\nconst x = {a: 1}; f({"k": 2}); {} {{ {unknown}\n```\n'
    )
    text = load_prompt(e.ctx.prompts_dir, "x", e.ctx, {"diag": "D"})
    assert text.startswith(
        f"Issue 7 on blacksmith/issue-7 at pr-7.example.com, notes in {e.ctx.work_dir}.\n"
        "Preview none (none). Extra: D\n")
    assert '```js\nconst x = {a: 1}; f({"k": 2}); {} {{ {unknown}\n```\n' in text


def test_load_prompt_reads_preview_json(tmp_path):
    e = make_env(tmp_path)
    e.ctx.work_dir.mkdir()
    meta = {"url": "https://pr-7.example.com/", "auth": ["preview", "pw"]}
    (e.ctx.work_dir / "preview.json").write_text(json.dumps(meta))
    (e.ctx.prompts_dir / "x.md").write_text("{preview_url} {preview_auth}")
    assert load_prompt(e.ctx.prompts_dir, "x", e.ctx) == "https://pr-7.example.com/ preview / pw"


def test_pr_screenshot_links_rewritten(tmp_path):
    e = make_env(tmp_path)
    e.run.responses["git remote get-url"] = CommandResult(
        0, "https://github.com/acme/shop.git\n", "")
    e.run.responses["git -c user.name=blacksmith[bot] -c user.email=blacksmith[bot]@users.noreply"
                    ".github.com commit-tree"] = CommandResult(0, "abc123\n", "")

    def pr(work_dir):
        (work_dir / "shots" / "01-home.png").write_bytes(b"png")
        (work_dir / "pr.md").write_text(
            "Add scale widget\n\nCloses #7\n\n![Home](shots/01-home.png)\n")

    e.claude.actions["pr"] = pr
    e.claude.actions["verify"] = _write(
        "verify.md", "RESULT: pass\nSee ![home](shots/01-home.png)\n")
    go(e)
    base = "https://github.com/acme/shop/raw/blacksmith-assets/issue-7/"
    body = e.gh.prs[0]["body"]
    assert f"![Home]({base}01-home.png)" in body
    assert f"![home]({base}01-home.png)" in body
    assert "](shots/" not in body
    assert ["git", "push", "-f", "origin",
            "abc123:refs/heads/blacksmith-assets/issue-7"] in e.run.calls


def test_pr_without_screenshots_pushes_no_assets(tmp_path):
    e = make_env(tmp_path)
    go(e)
    assert not any("blacksmith-assets" in " ".join(c) for c in e.run.calls)


def test_write_issue_markdown_creates_work_dir(tmp_path):
    from blacksmith.stages import write_issue_markdown

    work = tmp_path / "repo" / ".blacksmith"
    path = write_issue_markdown(FakeGitHub([]), 7, work)
    assert path == work / "issue.md"
    assert path.read_text() == "# Scale widget\n"


TOKENS = {"GH_TOKEN": "ghs_runner", "BLACKSMITH_GITHUB_TOKEN": "ghp_runner",
          "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat", "HOME": "/home/blacksmith"}


def test_claude_env_has_no_github_tokens(tmp_path):
    e = make_env(tmp_path, env=TOKENS)
    go(e)
    assert e.claude.calls
    for call in e.claude.calls:
        assert "GH_TOKEN" not in call["env"] and "BLACKSMITH_GITHUB_TOKEN" not in call["env"]
        assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat"


class FailingClaude(FakeClaude):
    def __call__(self, prompt, **kwargs):
        super().__call__(prompt, **kwargs)
        return StageResult(False, False, "Invalid API key · Please run /login " + "x" * 400,
                           1, kwargs["transcript"], "")


def test_failed_session_text_in_salvage_reason(tmp_path):
    e = make_env(tmp_path)
    e.claude = FailingClaude(e.ctx.work_dir, e.events)
    e.claude.actions["plan"] = None
    go(e)
    body = e.gh.prs[0]["body"]
    assert "Reason: plan.md not written (agent session `plan` error: Invalid API key" in body
    reason = next(line for line in body.splitlines() if line.startswith("Reason: "))
    assert "x" * 300 not in reason and "x" * 200 in reason


def test_ok_session_reason_unchanged(tmp_path):
    e = make_env(tmp_path)
    e.claude.actions["plan"] = None
    go(e)
    reason = next(line for line in e.gh.prs[0]["body"].splitlines()
                  if line.startswith("Reason: "))
    assert reason == "Reason: plan.md not written"


# -- Telegram notifications ----------------------------------------------------------

class FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(text)


ISSUE_URL = "https://github.com/acme/shop/issues/7"
HEAD = "acme/shop#7 Scale widget"


def test_telegram_gets_every_stage_change_and_milestone(tmp_path):
    e = make_env(tmp_path)
    e.notifier = FakeNotifier()
    go(e)
    assert e.notifier.sent[0] == f"{HEAD}\nstage plan\n{ISSUE_URL}"
    assert e.notifier.sent[1] == f"{HEAD}\nPlan: Goal: add a scale widget\n{ISSUE_URL}"
    assert (f"{HEAD}\nPreview is up at https://pr-7.example.com/\nlogin: preview / pw123\n"
            f"{ISSUE_URL}") in e.notifier.sent
    assert e.notifier.sent[-1] == f"{HEAD}\nOpened https://x/pull/1\nhttps://x/pull/1"
    stages = [m.splitlines()[1] for m in e.notifier.sent if m.splitlines()[1].startswith("stage ")]
    assert stages == ["stage plan", "stage build", "stage preview", "stage verify",
                      "stage design", "stage pr"]


def test_telegram_plan_summary_skips_headings(tmp_path):
    e = make_env(tmp_path)
    e.notifier = FakeNotifier()
    e.claude.actions["plan"] = _write("plan.md", "## Goal\n\nAdd a widget.\n\n## Criteria\n")
    go(e)
    assert e.notifier.sent[1] == f"{HEAD}\nPlan: Add a widget.\n{ISSUE_URL}"


def test_telegram_failure_is_logged_not_fatal(tmp_path):
    e = make_env(tmp_path)
    e.notifier = FakeNotifier(fail=True)
    state = go(e)
    assert state.stage == "done" and e.gh.prs[0]["draft"] is False
    log = (e.ctx.work_dir / "log.md").read_text()
    assert "telegram failed: RuntimeError('telegram down')" in log


def test_telegram_gets_the_salvage_reason_with_the_draft_link(tmp_path):
    e = make_env(tmp_path)
    e.notifier = FakeNotifier()
    e.claude.actions["plan"] = lambda work_dir: None
    go(e)
    assert e.notifier.sent[-1] == (
        f"{HEAD}\nStopped at stage plan: plan.md not written. Opened draft https://x/pull/1\n"
        "https://x/pull/1"
    )


def test_no_notifier_means_no_messages(tmp_path):
    e = make_env(tmp_path)
    state = go(e)
    assert state.stage == "done"
    assert "telegram" not in (e.ctx.work_dir / "log.md").read_text()
