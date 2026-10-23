"""Review rounds on the open PR (driven by the poll timer) and the lifetime self-destruct."""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Sequence

from blacksmith import preview
from blacksmith.claude import StageResult, run_claude
from blacksmith.config import Config, Limits
from blacksmith.github import Comment, GitHub
from blacksmith.runner import CommandRunner
from blacksmith.stages import (
    RunContext, _commit_push, _preview_meta, _preview_section, load_prompt, utc_now_iso,
)
from blacksmith.state import State, load_state, save_state

SELF_DESTRUCT_EVENT = "blacksmith-self-destruct"
EXPIRED = ("Blacksmith preview for this PR expired after {age} and the server is being "
           "destroyed. Re-label the issue to start over.")


def _log(ctx: RunContext, now_iso: Callable[[], str], msg: str) -> None:
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.work_dir / "log.md", "a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} {msg}\n")


def _load(ctx: RunContext) -> State | None:
    path = ctx.work_dir / "state.json"
    return load_state(path) if path.exists() else None


def render_comments(comments: Sequence[Comment]) -> str:
    parts = []
    for c in comments:
        where = f", {c.path}:{c.line}" if c.path else ""
        parts.append(f"### {c.author} ({c.kind}{where}) at {c.created_at}\n\n{c.body}\n\n")
    return "".join(parts)


def _short_sha(ctx: RunContext, run: CommandRunner) -> str:
    return run(["git", "rev-parse", "--short", "HEAD"], cwd=ctx.repo_root).stdout.strip()


def _round(cfg: Config, ctx: RunContext, gh: GitHub, run: CommandRunner, state: State,
           comments: list[Comment], n: int, claude: Callable[..., StageResult],
           deploy: Callable[..., preview.PreviewResult], now_iso: Callable[[], str]) -> str:
    """Run one round and return its summary line."""
    (ctx.work_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    prompt = load_prompt(ctx.prompts_dir, "review", ctx, {
        "comments": render_comments(comments),
        "pr_number": str(state.pr_number),
        "pr_url": state.pr_url or "",
    })
    res = claude(
        prompt,
        cwd=ctx.repo_root,
        max_turns=cfg.limits.max_turns_per_stage,
        timeout_s=cfg.limits.review_round_minutes * 60,
        transcript=ctx.work_dir / "transcripts" / f"round-{n}-review.json",
        run=run,
        env=dict(ctx.env),
    )
    _log(ctx, now_iso, f"claude review round {n}: ok={res.ok} timed_out={res.timed_out} "
                       f"turns={res.num_turns} {res.cost_note}".rstrip())
    _commit_push(ctx, run, f"blacksmith: review round {n} for #{ctx.issue}")
    sha = _short_sha(ctx, run)

    before = _preview_meta(ctx.work_dir)
    pres = deploy(cfg, preview.PreviewContext(ctx.repo_root, ctx.host, ctx.work_dir), run)
    if pres.ok:
        _log(ctx, now_iso, f"preview up at {pres.url}")
        if _preview_meta(ctx.work_dir) != before:
            gh.upsert_comment(state.pr_number, "preview", _preview_section(ctx.work_dir))
        preview_note = "ok"
    else:
        _log(ctx, now_iso, f"preview failed: {pres.error}")
        preview_note = f"failed: {pres.error}"

    if res.ok:
        return f"Round {n}: addressed {len(comments)} comment(s) in {sha}; preview {preview_note}"
    why = "timed out" if res.timed_out else "error"
    return (f"Round {n}: agent session did not finish cleanly ({why}); pushed {sha}; "
            f"preview {preview_note}")


def review_round(
    cfg: Config,
    ctx: RunContext,
    gh: GitHub,
    run: CommandRunner,
    *,
    claude: Callable[..., StageResult] = run_claude,
    deploy: Callable[..., preview.PreviewResult] = preview.deploy,
    now_iso: Callable[[], str] = utc_now_iso,
) -> bool:
    """Address human comments posted since the last round. True when a round ran."""
    state = _load(ctx)
    if state is None or state.pr_number is None:
        return False
    round_start = now_iso()
    comments = gh.human_comments_since(state.pr_number, state.last_seen)
    if not comments:
        return False
    with open(ctx.work_dir / "lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log(ctx, now_iso, "review round skipped: lock busy")
            return False
        try:
            n = state.round + 1
            try:
                summary = _round(cfg, ctx, gh, run, state, comments, n, claude, deploy, now_iso)
            except Exception as exc:
                summary = f"Round {n} failed: {exc!r}"
            _log(ctx, now_iso, summary[0].lower() + summary[1:])
            try:
                gh.upsert_comment(state.pr_number, f"round-{n}", summary)
            except Exception as exc:
                _log(ctx, now_iso, f"round {n} summary comment failed: {exc!r}")
            # Advance even after a failure so the same comments are not retried forever.
            state.last_seen = round_start
            state.round = n
            save_state(ctx.work_dir / "state.json", state)
            return True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _age(hours: int) -> str:
    return f"{hours // 24} days" if hours >= 24 else f"{hours} hours"


def self_destruct(cfg: Config | None, ctx: RunContext, gh: GitHub) -> None:
    """Say goodbye on the PR (or issue), then ask the workflow to destroy this droplet.

    ``cfg`` may be None when blacksmith.yml no longer parses; the droplet must still go.
    """
    limits = cfg.limits if cfg else Limits()
    state = _load(ctx)
    target = state.pr_number if state and state.pr_number is not None else ctx.issue
    try:
        gh.upsert_comment(target, "expired", EXPIRED.format(age=_age(limits.lifetime_hours)))
    except Exception as exc:
        _log(ctx, utc_now_iso, f"expired comment failed: {exc!r}")
    gh.dispatch(SELF_DESTRUCT_EVENT, {"issue": ctx.issue})
