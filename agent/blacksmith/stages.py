"""First-pass pipeline: plan, build, preview, verify, design, pr; salvage on failure."""

from __future__ import annotations

import json
import re
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from blacksmith import git, notify, preview
from blacksmith.claude import StageResult, agent_env, run_claude
from blacksmith.config import Config
from blacksmith.github import GitHub, Issue
from blacksmith.runner import CommandRunner
from blacksmith.state import State, load_state, save_state

Stage = Literal["plan", "build", "preview", "verify", "design", "pr"]
BUDGET = {"plan": .10, "build": .45, "preview_fix": .05, "verify": .20, "design": .10, "pr": .05}
CHECK_TIMEOUT_S = 1800
MAX_PREVIEW_FIXES = 2
MAX_BUILD_FIXES = 2
LOG_TAIL_LINES = 200
HEADER = "**Blacksmith** is working on this issue on branch `{branch}`."
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class StageFailed(Exception):
    """A stage could not produce what the next one needs; the run is salvaged."""


@dataclass(frozen=True)
class RunContext:
    repo_root: Path
    work_dir: Path
    issue: int
    branch: str
    base_branch: str
    host: str
    prompts_dir: Path
    env: Mapping[str, str]
    now: Callable[[], float] = time.monotonic
    repo: str = ""  # owner/name, for notification links


@dataclass
class PipelineEnv:
    """Injected seams plus per-run bookkeeping shared by every stage."""

    claude: Callable[..., StageResult]
    deploy: Callable[..., preview.PreviewResult]
    now_iso: Callable[[], str]
    issue: Issue
    state_path: Path
    notifier: notify.Telegram | None = None
    milestones: list[str] = field(default_factory=list)
    transcripts: int = 0
    last_session: tuple[str, StageResult] | None = None  # within the current stage


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _preview_meta(work_dir: Path) -> dict | None:
    try:
        return json.loads((work_dir / "preview.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def load_prompt(prompts_dir: Path, name: str, ctx: RunContext,
                extra: Mapping[str, str] = {}) -> str:
    """Fill ``{name}`` placeholders we know; every other brace survives verbatim."""
    meta = _preview_meta(ctx.work_dir) or {}
    auth = meta.get("auth")
    values = {
        "issue": str(ctx.issue),
        "branch": ctx.branch,
        "host": ctx.host,
        "work_dir": str(ctx.work_dir),
        "preview_url": meta.get("url") or "none",
        "preview_auth": f"{auth[0]} / {auth[1]}" if auth else "none",
        **extra,
    }
    text = (prompts_dir / f"{name}.md").read_text()
    return _PLACEHOLDER.sub(lambda m: str(values.get(m[1], m[0])), text)


# -- bookkeeping -----------------------------------------------------------------

def _log(ctx: RunContext, env: PipelineEnv, msg: str) -> None:
    with open(ctx.work_dir / "log.md", "a", encoding="utf-8") as fh:
        fh.write(f"{env.now_iso()} {msg}\n")


def _post_progress(gh: GitHub, ctx: RunContext, state: State, env: PipelineEnv) -> None:
    """Best effort: a failed progress comment is logged, never fatal."""
    parts = [HEADER.format(branch=ctx.branch), *env.milestones, f"_Stage: `{state.stage}`_"]
    try:
        state.progress_comment_id = gh.upsert_comment(ctx.issue, "progress", "\n\n".join(parts))
    except Exception as exc:
        _log(ctx, env, f"progress comment failed: {exc!r}")
    save_state(env.state_path, state)


def _notify(ctx: RunContext, state: State, env: PipelineEnv, text: str) -> None:
    """Best effort: a failed Telegram message is logged, never fatal."""
    if env.notifier is None:
        return
    link = state.pr_url or notify.issue_url(ctx.repo, ctx.issue)
    try:
        env.notifier.send(notify.compose(ctx.repo, ctx.issue, env.issue.title, text, link))
    except Exception as exc:
        _log(ctx, env, f"telegram failed: {exc!r}")


def _milestone(gh: GitHub, ctx: RunContext, state: State, env: PipelineEnv, text: str,
               short: str | None = None) -> None:
    """Record a milestone in the progress comment; ``short`` replaces it in notifications."""
    env.milestones.append(text)
    _log(ctx, env, text.splitlines()[0])
    _post_progress(gh, ctx, state, env)
    _notify(ctx, state, env, text if short is None else short)


def _plan_summary(plan_md: str) -> str:
    for line in plan_md.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return f"Plan: {line[:300]}"
    return "Plan posted"


def _claude(cfg: Config, ctx: RunContext, run: CommandRunner, env: PipelineEnv, name: str,
            budget: str | None = None, extra: Mapping[str, str] = {}) -> StageResult:
    env.transcripts += 1
    transcript = ctx.work_dir / "transcripts" / f"{env.transcripts:02d}-{name}.json"
    checks = "\n".join(c.run for c in cfg.checks)
    prompt = load_prompt(ctx.prompts_dir, name, ctx, {"checks": checks, **extra})
    res = env.claude(
        prompt,
        cwd=ctx.repo_root,
        max_turns=cfg.limits.max_turns_per_stage,
        timeout_s=int(cfg.limits.first_run_minutes * 60 * BUDGET[budget or name]),
        transcript=transcript,
        run=run,
        env=agent_env(ctx.env),
    )
    env.last_session = (name, res)
    _log(ctx, env, f"claude {name}: ok={res.ok} timed_out={res.timed_out} "
                   f"turns={res.num_turns} {res.cost_note}".rstrip())
    return res


def _deploy(cfg: Config, ctx: RunContext, run: CommandRunner,
            env: PipelineEnv) -> preview.PreviewResult:
    res = env.deploy(cfg, preview.PreviewContext(ctx.repo_root, ctx.host, ctx.work_dir), run)
    _log(ctx, env, f"preview up at {res.url}" if res.ok else f"preview failed: {res.error}")
    return res


def _commit_push(ctx: RunContext, run: CommandRunner, message: str) -> None:
    git.commit_all(ctx.repo_root, message, run)
    git.push_branch(ctx.repo_root, ctx.branch, run)


def _require(path: Path) -> str:
    if not path.exists():
        raise StageFailed(f"{path.name} not written")
    return path.read_text()


def _fresh(path: Path) -> Path:
    """Remove a stale artifact so the stage must write it again."""
    path.unlink(missing_ok=True)
    return path


# -- stages ----------------------------------------------------------------------

def stage_plan(cfg, ctx, gh, run, state, env) -> None:
    plan = _fresh(ctx.work_dir / "plan.md")
    _claude(cfg, ctx, run, env, "plan")
    plan_md = _require(plan).strip()
    _milestone(gh, ctx, state, env, "### Plan\n\n" + plan_md, short=_plan_summary(plan_md))


def stage_build(cfg, ctx, gh, run, state, env) -> None:
    _claude(cfg, ctx, run, env, "build")
    _commit_push(ctx, run, f"blacksmith: build for #{ctx.issue}")


def stage_preview(cfg, ctx, gh, run, state, env) -> None:
    _fresh(ctx.work_dir / "preview.json")  # salvage must not advertise a stale preview
    res = _deploy(cfg, ctx, run, env)
    for _ in range(MAX_PREVIEW_FIXES):
        if res.ok:
            break
        _claude(cfg, ctx, run, env, "preview_fix",
                extra={"diagnostics": f"{res.error}\n\n{res.diagnostics}"})
        git.commit_all(ctx.repo_root, f"blacksmith: fix preview for #{ctx.issue}", run)
        res = _deploy(cfg, ctx, run, env)
    if not res.ok:
        raise StageFailed(f"preview failed after {MAX_PREVIEW_FIXES} fix attempts: {res.error}")
    text = f"Preview is up at {res.url}"
    if res.auth:
        text += f"\nlogin: {res.auth[0]} / {res.auth[1]}"
    _milestone(gh, ctx, state, env, text)


def _run_checks(cfg: Config, ctx: RunContext, run: CommandRunner) -> tuple[str, bool]:
    """Markdown results table and whether any required check (or setup) failed."""
    rows = ["| check | kind | result |", "|---|---|---|"]
    hard = False

    def sh(cmd: str) -> int:
        return run(["bash", "-lc", cmd], cwd=ctx.repo_root, timeout=CHECK_TIMEOUT_S).code

    for cmd in cfg.checks_setup:
        code = sh(cmd)
        if code != 0:
            hard = True
            rows.append(f"| setup: {cmd} | required | FAIL code {code} |")
    for check in cfg.checks:
        code = sh(check.run)
        hard |= code != 0 and not check.soft
        result = "pass" if code == 0 else f"FAIL code {code}"
        rows.append(f"| {check.run} | {'soft' if check.soft else 'required'} | {result} |")
    return "\n".join(rows), hard


def _verify_failed(verify_md: str) -> bool:
    first = next((line.strip() for line in verify_md.splitlines() if line.strip()), "")
    return first.startswith("RESULT: fail")


def stage_verify(cfg, ctx, gh, run, state, env) -> None:
    for loop in range(MAX_BUILD_FIXES + 1):
        table, hard = _run_checks(cfg, ctx, run)
        verify = _fresh(ctx.work_dir / "verify.md")
        _claude(cfg, ctx, run, env, "verify", extra={"check_results": table})
        verify_md = _require(verify)
        if not hard and not _verify_failed(verify_md):
            return
        if loop == MAX_BUILD_FIXES:
            raise StageFailed(f"verify failed after {MAX_BUILD_FIXES} fix loops")
        _log(ctx, env, f"verify failed (hard check failure: {hard}); build fix {loop + 1}")
        _claude(cfg, ctx, run, env, "build_fix", "verify",
                extra={"verify": verify_md, "check_results": table})
        _commit_push(ctx, run, f"blacksmith: fix verify findings for #{ctx.issue}")
        _deploy(cfg, ctx, run, env)


def _design_skip_reason(cfg: Config, ctx: RunContext, run: CommandRunner) -> str | None:
    if not cfg.design.figma:
        return "figma disabled in blacksmith.yml"
    if not ctx.env.get("FIGMA_API_KEY"):
        return "FIGMA_API_KEY not set"
    changed = git.changed_paths(ctx.repo_root, f"origin/{ctx.base_branch}", run)
    if not any(p.startswith(prefix) for p in changed for prefix in cfg.design.paths):
        return f"no changes under {', '.join(cfg.design.paths)}"
    return None


def stage_design(cfg, ctx, gh, run, state, env) -> None:
    design = _fresh(ctx.work_dir / "design.md")
    reason = _design_skip_reason(cfg, ctx, run)
    if reason:
        design.write_text(f"Skipped: {reason}\n")
        _log(ctx, env, f"design skipped: {reason}")
        return
    _claude(cfg, ctx, run, env, "design")
    if not design.exists():
        design.write_text("Design check did not write a report.\n")


def _preview_section(work_dir: Path) -> str:
    meta = _preview_meta(work_dir)
    if not meta:
        return ""
    lines = [f"**Preview:** {meta['url']}"]
    if meta.get("auth"):
        user, password = meta["auth"]
        lines.append(f"**Preview login:** `{user}` / `{password}`")
    return "\n".join(lines)


def _details(summary: str, path: Path) -> str:
    text = path.read_text() if path.exists() else "(not written)\n"
    return f"<details><summary>{summary}</summary>\n\n{text}\n</details>"


_SHOT_LINK = re.compile(r"\]\(shots/([^)\s]+)\)")


def _push_screenshots(ctx: RunContext, run: CommandRunner, env: PipelineEnv) -> str | None:
    """Best effort: a failed asset push leaves the links relative, never salvages the PR."""
    try:
        return git.push_assets(ctx.repo_root, ctx.work_dir / "shots",
                               f"blacksmith-assets/issue-{ctx.issue}", run)
    except Exception as exc:
        _log(ctx, env, f"screenshot push failed: {exc!r}")
        return None


def stage_pr(cfg, ctx, gh, run, state, env) -> None:
    pr_md = _fresh(ctx.work_dir / "pr.md")
    _claude(cfg, ctx, run, env, "pr")
    title, _, body = _require(pr_md).strip().partition("\n")
    title = title.lstrip("#").strip()
    if not title:
        raise StageFailed("pr.md has no title line")
    body = "\n\n".join([
        body.strip(),
        "---\n" + _preview_section(ctx.work_dir),
        _details("Verification", ctx.work_dir / "verify.md"),
        _details("Design check", ctx.work_dir / "design.md"),
    ])
    base = _push_screenshots(ctx, run, env)
    if base:
        body = _SHOT_LINK.sub(lambda m: f"]({base}{m[1]})", body)
    _commit_push(ctx, run, f"blacksmith: finish #{ctx.issue}")
    pr = gh.create_pr(head=ctx.branch, base=ctx.base_branch, title=title, body=body, draft=False)
    state.pr_number, state.pr_url = pr.number, pr.url
    save_state(env.state_path, state)
    try:
        gh.request_review(pr.number, env.issue.author)
    except Exception as exc:  # the PR exists; do not salvage over it
        _log(ctx, env, f"request_review failed: {exc!r}")
    state.last_seen = env.now_iso()
    state.stage = "done"
    _milestone(gh, ctx, state, env, f"Opened {pr.url}")


STAGES: tuple[tuple[Stage, Callable[..., None]], ...] = (
    ("plan", stage_plan),
    ("build", stage_build),
    ("preview", stage_preview),
    ("verify", stage_verify),
    ("design", stage_design),
    ("pr", stage_pr),
)


# -- salvage and driver ------------------------------------------------------------

def salvage(cfg, ctx, gh, run, state, env, reason: str) -> None:
    """Open a draft PR with whatever exists. Never raises."""
    stage = state.stage
    try:
        _log(ctx, env, f"salvage at stage {stage}: {reason}")
        try:
            git.commit_all(ctx.repo_root, f"blacksmith: incomplete work for #{ctx.issue}", run)
            git.push_branch(ctx.repo_root, ctx.branch, run)
        except Exception as exc:
            _log(ctx, env, f"salvage commit/push failed: {exc!r}")
        log_lines = (ctx.work_dir / "log.md").read_text().splitlines()[-LOG_TAIL_LINES:]
        body = (f"Blacksmith stopped at stage `{stage}`.\n\nReason: {reason}\n\n"
                "<details><summary>Log</summary>\n\n" + "\n".join(log_lines) + "\n</details>")
        section = _preview_section(ctx.work_dir)
        if section:
            body += "\n\n" + section
        state.stage = "salvaged"
        state.last_seen = env.now_iso()
        save_state(env.state_path, state)
        try:
            pr = gh.create_pr(head=ctx.branch, base=ctx.base_branch,
                              title=f"[blacksmith] {env.issue.title} (incomplete)",
                              body=body, draft=True)
        except Exception as exc:
            _log(ctx, env, f"salvage create_pr failed: {exc!r}")
            _milestone(gh, ctx, state, env, f"Stopped at stage {stage}: {reason}. "
                       "Could not open a draft PR (see runner log).")
            return
        state.pr_number, state.pr_url = pr.number, pr.url
        _milestone(gh, ctx, state, env,
                   f"Stopped at stage {stage}: {reason}. Opened draft {pr.url}")
    except Exception:
        try:
            _log(ctx, env, "salvage crashed:\n" + traceback.format_exc())
        except Exception:
            pass


def _failure_reason(reason: str, env: PipelineEnv) -> str:
    """Append what the stage's last agent session said when it did not finish cleanly."""
    if env.last_session is None or env.last_session[1].ok:
        return reason
    name, res = env.last_session
    why = "timed out" if res.timed_out else "error"
    said = " ".join(res.result_text.split())[:300]
    return f"{reason} (agent session `{name}` {why}: {said})" if said else \
        f"{reason} (agent session `{name}` {why})"


def write_issue_markdown(gh: GitHub, issue_number: int, work_dir: Path,
                         issue: Issue | None = None) -> Path:
    """Write ``work_dir/issue.md`` (fetching the issue unless given) and return its path."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "issue.md"
    path.write_text(gh.issue_markdown(issue if issue is not None else gh.issue(issue_number)))
    return path


def run_first_pass(
    cfg: Config,
    ctx: RunContext,
    gh: GitHub,
    run: CommandRunner,
    *,
    claude: Callable[..., StageResult] = run_claude,
    deploy: Callable[..., preview.PreviewResult] = preview.deploy,
    now_iso: Callable[[], str] = utc_now_iso,
    notifier: notify.Telegram | None = None,
) -> State:
    for d in (ctx.work_dir, ctx.work_dir / "transcripts", ctx.work_dir / "shots"):
        d.mkdir(parents=True, exist_ok=True)
    state_path = ctx.work_dir / "state.json"
    state = load_state(state_path) if state_path.exists() else State(ctx.issue, ctx.branch)
    issue = gh.issue(ctx.issue)
    if not (ctx.work_dir / "issue.md").exists():
        write_issue_markdown(gh, ctx.issue, ctx.work_dir, issue=issue)
    env = PipelineEnv(claude=claude, deploy=deploy, now_iso=now_iso, issue=issue,
                      state_path=state_path, notifier=notifier)
    try:
        for name, stage in STAGES:
            state.stage = name
            _log(ctx, env, f"stage {name}")
            _post_progress(gh, ctx, state, env)
            _notify(ctx, state, env, f"stage {name}")
            env.last_session = None
            stage(cfg, ctx, gh, run, state, env)
    except StageFailed as exc:
        salvage(cfg, ctx, gh, run, state, env, _failure_reason(str(exc), env))
    except Exception as exc:
        _log(ctx, env, "unexpected error:\n" + traceback.format_exc())
        salvage(cfg, ctx, gh, run, state, env, repr(exc))
    return state
