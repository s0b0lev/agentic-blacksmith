import fcntl
import json

from blacksmith.claude import StageResult
from blacksmith.github import Comment
from blacksmith.review import render_comments, review_round, self_destruct
from blacksmith.runner import CommandResult
from blacksmith.state import State, load_state, save_state
from test_stages import DOWN, UP, FakeClaude, FakeGitHub, _write, make_env

T1 = "2026-10-23T12:00:00+00:00"
T2 = "2026-10-23T12:30:00+00:00"
REVIEW = Comment(11, "bob", "Rename the button", "2026-10-23T11:00:00Z", "review_comment",
                 "storefront/app/page.tsx", 42)
ISSUE_COMMENT = Comment(12, "carol", "Also fix the footer", "2026-10-23T11:05:00Z", "issue")


class ReviewGitHub(FakeGitHub):
    def __init__(self, events):
        super().__init__(events)
        self.comments: list[Comment] = []
        self.since_calls: list[tuple[int, str | None]] = []
        self.dispatches: list[tuple[str, dict]] = []
        self.fail_upsert = False
        self.replies: list[tuple[int, int, str]] = []

    def reply(self, comment, pr, body):
        self.events.append("reply")
        self.replies.append((comment.id, pr, body))

    def human_comments_since(self, pr, since):
        self.events.append("comments_since")
        self.since_calls.append((pr, since))
        return list(self.comments)

    def upsert_comment(self, target, kind, body):
        self.events.append(f"upsert:{kind}")
        if self.fail_upsert:
            raise RuntimeError("gh down")
        return super().upsert_comment(target, kind, body)

    def dispatch(self, event_type, payload):
        self.events.append("dispatch")
        self.dispatches.append((event_type, payload))


class ReviewClaude(FakeClaude):
    def __init__(self, work_dir, events):
        super().__init__(work_dir, events)
        self.result: StageResult | None = None

    def __call__(self, prompt, **kwargs):
        res = super().__call__(prompt, **kwargs)
        return self.result or res


def prepare(tmp_path, *, pr=5, comments=(REVIEW, ISSUE_COMMENT), round=0, **limits):
    e = make_env(tmp_path, **limits)
    e.gh = ReviewGitHub(e.events)
    e.gh.comments = list(comments)
    e.claude = ReviewClaude(e.ctx.work_dir, e.events)
    (e.ctx.prompts_dir / "review.md").write_text(
        "PROMPT:review\ncomments={comments}\npr={pr_number} {pr_url}\nmarker={reply_marker}\n")
    e.claude.actions["review"] = _write("replies.json", json.dumps([
        {"comment_id": 11, "kind": "review_comment", "body": "Renamed."},
        {"comment_id": 12, "kind": "issue", "body": "Footer fixed."},
    ]))
    e.ctx.work_dir.mkdir()
    state = State(7, "blacksmith/issue-7", stage="done", last_seen="2026-10-23T10:00:00+00:00",
                  round=round)
    if pr is not None:
        state.pr_number, state.pr_url = pr, f"https://x/pull/{pr}"
    save_state(e.ctx.work_dir / "state.json", state)
    e.run.responses["git status"] = CommandResult(0, " M storefront/app/page.tsx\n", "")
    e.run.responses["git rev-parse"] = CommandResult(0, "abc1234\n", "")
    return e


def go(e, *times):
    clock = iter(times or (T1, T2, T2, T2, T2, T2, T2))
    return review_round(e.cfg, e.ctx, e.gh, e.run, claude=e.claude, deploy=e.deploy,
                        now_iso=lambda: next(clock))


def state_of(e):
    return load_state(e.ctx.work_dir / "state.json")


def test_no_pr_no_round(tmp_path):
    e = prepare(tmp_path, pr=None)
    assert go(e) is False
    assert e.gh.since_calls == [] and e.claude.calls == []


def test_no_state_no_round(tmp_path):
    e = prepare(tmp_path)
    (e.ctx.work_dir / "state.json").unlink()
    assert go(e) is False
    assert e.gh.since_calls == []


def test_no_new_comments_no_round(tmp_path):
    e = prepare(tmp_path, comments=())
    assert go(e) is False
    assert e.gh.since_calls == [(5, "2026-10-23T10:00:00+00:00")]
    assert e.claude.calls == [] and e.deploy.calls == 0
    assert state_of(e).last_seen == "2026-10-23T10:00:00+00:00"


def test_round_sets_last_seen_to_round_start_not_end(tmp_path):
    e = prepare(tmp_path)
    assert go(e, T1, T2, T2, T2, T2, T2, T2) is True
    state = state_of(e)
    assert state.last_seen == T1
    assert state.round == 1


def test_round_pushes_and_redeploys(tmp_path):
    e = prepare(tmp_path, review_round_minutes=30, max_turns_per_stage=50)
    assert go(e) is True
    assert e.events == ["comments_since", "claude:review", "push", "reply", "reply", "deploy",
                        "upsert:preview", "upsert:round-1"]
    call = e.claude.calls[0]
    assert call["timeout_s"] == 1800 and call["max_turns"] == 50
    assert call["transcript"] == e.ctx.work_dir / "transcripts" / "round-1-review.json"
    assert "pr=5 https://x/pull/5" in call["prompt"]
    assert "### bob (review_comment, storefront/app/page.tsx:42)" in call["prompt"]
    assert ["git", "add", "-A"] in e.run.calls
    assert ["git", "push", "origin", "HEAD:refs/heads/blacksmith/issue-7"] in e.run.calls
    assert e.gh.upserts[0] == (5, "preview", "**Preview:** https://pr-7.example.com/\n"
                                              "**Preview login:** `preview` / `pw123`")
    assert e.gh.upserts[-1] == (5, "round-1",
                                "Round 1: addressed 2 comment(s) in abc1234; preview ok")
    assert "round 1: addressed 2 comment(s)" in (e.ctx.work_dir / "log.md").read_text()


def test_round_unchanged_preview_not_reposted(tmp_path):
    e = prepare(tmp_path, round=2)
    meta = {"url": UP.url, "host": "pr-7.example.com", "auth": list(UP.auth)}
    (e.ctx.work_dir / "preview.json").write_text(json.dumps(meta))
    assert go(e) is True
    assert [u[1] for u in e.gh.upserts] == ["round-3"]
    assert e.claude.calls[0]["transcript"].name == "round-3-review.json"
    assert state_of(e).round == 3


def test_round_deploy_failure_reported(tmp_path):
    e = prepare(tmp_path)
    e.deploy.results = [DOWN]
    assert go(e) is True
    assert e.gh.upserts == [(5, "round-1", "Round 1: addressed 2 comment(s) in abc1234; "
                                           "preview failed: up app failed")]


def test_round_claude_failure_still_pushes_and_reports(tmp_path):
    e = prepare(tmp_path)
    e.claude.result = StageResult(False, True, "", None, tmp_path / "t.json", "")
    assert go(e) is True
    assert [x for x in e.events if x != "reply"][1:4] == ["claude:review", "push", "deploy"]
    assert e.gh.upserts[-1] == (5, "round-1", "Round 1: agent session did not finish cleanly "
                                              "(timed out); pushed abc1234; preview ok")
    (tmp_path / "b").mkdir()
    e2 = prepare(tmp_path / "b")
    e2.claude.result = StageResult(False, False, "", None, tmp_path / "t.json", "")
    go(e2)
    assert "(error); pushed abc1234" in e2.gh.upserts[-1][2]


def test_round_unexpected_exception_advances_state(tmp_path):
    e = prepare(tmp_path)
    e.run.responses["git push"] = CommandResult(1, "", "rejected")
    assert go(e) is True
    assert "deploy" not in e.events
    assert e.gh.upserts[-1][:2] == (5, "round-1")
    assert e.gh.upserts[-1][2].startswith("Round 1 failed: GitError(")
    assert "rejected" in (e.ctx.work_dir / "log.md").read_text()
    state = state_of(e)
    assert (state.last_seen, state.round) == (T1, 1)


def test_lock_busy_skips(tmp_path):
    e = prepare(tmp_path)
    with open(e.ctx.work_dir / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert go(e) is False
    assert e.claude.calls == [] and e.deploy.calls == 0
    assert state_of(e).round == 0
    assert "lock busy" in (e.ctx.work_dir / "log.md").read_text()
    assert go(e) is True  # released by the test, the next poll runs


def test_comments_markdown_format():
    assert render_comments([REVIEW, ISSUE_COMMENT]) == (
        "### bob (review_comment, storefront/app/page.tsx:42) at 2026-10-23T11:00:00Z\n\n"
        "Rename the button\n\n"
        "### carol (issue) at 2026-10-23T11:05:00Z\n\n"
        "Also fix the footer\n\n"
    )


def test_self_destruct_comments_then_dispatches(tmp_path):
    e = prepare(tmp_path)
    self_destruct(e.cfg, e.ctx, e.gh)
    assert e.events == ["upsert:expired", "dispatch"]
    assert e.gh.upserts == [(5, "expired", "Blacksmith preview for this PR expired after 7 days "
                                           "and the server is being destroyed. Re-label the "
                                           "issue to start over.")]
    assert e.gh.dispatches == [("blacksmith-self-destruct", {"issue": 7})]


def test_self_destruct_uses_issue_when_no_pr(tmp_path):
    e = prepare(tmp_path, pr=None, lifetime_hours=12)
    self_destruct(e.cfg, e.ctx, e.gh)
    assert e.gh.upserts[0][:2] == (7, "expired")
    assert "expired after 12 hours and" in e.gh.upserts[0][2]
    (e.ctx.work_dir / "state.json").unlink()
    self_destruct(e.cfg, e.ctx, e.gh)
    assert e.gh.upserts[1][:2] == (7, "expired")


def test_self_destruct_comment_failure_still_dispatches(tmp_path):
    e = prepare(tmp_path)
    e.gh.fail_upsert = True
    self_destruct(e.cfg, e.ctx, e.gh)
    assert e.gh.dispatches == [("blacksmith-self-destruct", {"issue": 7})]
    assert "gh down" in (e.ctx.work_dir / "log.md").read_text()


def test_round_posts_replies_from_file_with_marker(tmp_path):
    e = prepare(tmp_path)
    assert go(e) is True
    assert e.gh.replies == [(11, 5, "Renamed.\n\n<!-- blacksmith:reply -->"),
                            (12, 5, "Footer fixed.\n\n<!-- blacksmith:reply -->")]
    assert "marker=<!-- blacksmith:reply -->" in e.claude.calls[0]["prompt"]
    assert "addressed 2 comment(s)" in e.gh.upserts[-1][2]


def test_round_ignores_unknown_reply_ids_and_bad_json(tmp_path):
    e = prepare(tmp_path)
    e.claude.actions["review"] = _write("replies.json", json.dumps([
        {"comment_id": 999, "kind": "issue", "body": "?"},
        {"comment_id": 12, "kind": "issue", "body": "Footer fixed."},
    ]))
    go(e)
    assert [r[0] for r in e.gh.replies] == [12]
    assert "addressed 1 comment(s)" in e.gh.upserts[-1][2]
    assert "unknown comment issue 999" in (e.ctx.work_dir / "log.md").read_text()

    (tmp_path / "b").mkdir()
    e2 = prepare(tmp_path / "b")
    e2.claude.actions["review"] = _write("replies.json", "{not json")
    assert go(e2) is True
    assert e2.gh.replies == []
    assert "addressed 0 comment(s)" in e2.gh.upserts[-1][2]
    assert "replies.json ignored" in (e2.ctx.work_dir / "log.md").read_text()


def test_round_deletes_stale_replies_file(tmp_path):
    e = prepare(tmp_path)
    e.claude.actions["review"] = None
    (e.ctx.work_dir / "replies.json").write_text(
        json.dumps([{"comment_id": 11, "kind": "review_comment", "body": "old"}]))
    go(e)
    assert e.gh.replies == []
    assert "addressed 0 comment(s)" in e.gh.upserts[-1][2]


def test_no_round_before_first_pass_finishes(tmp_path):
    e = prepare(tmp_path)
    state = state_of(e)
    for stage in ("init", "build", "pr"):
        state.stage = stage
        save_state(e.ctx.work_dir / "state.json", state)
        assert go(e) is False
    assert e.gh.since_calls == [] and e.claude.calls == []
    assert not (e.ctx.work_dir / "lock").exists()
    state.stage = "salvaged"
    save_state(e.ctx.work_dir / "state.json", state)
    assert go(e) is True
