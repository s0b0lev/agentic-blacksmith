import json

import pytest

from blacksmith.github import MARKER, Comment, GitHub, GitHubError, Issue
from blacksmith.runner import CommandResult
from blacksmith.scrub import SecretLeak

REPO = "o/r"
ACCEPT = ["-H", "Accept: application/vnd.github+json"]
SECRET = "supersecret-token-value"


def ok(payload) -> CommandResult:
    return CommandResult(0, json.dumps(payload), "")


def make(fake_runner) -> GitHub:
    return GitHub(REPO, fake_runner, secret_values=[SECRET])


def issue_comment(id_, login, body, created_at):
    return {"id": id_, "user": {"login": login}, "body": body, "created_at": created_at}


def test_issue_markdown_includes_comments(fake_runner):
    fake_runner.responses["gh api repos/o/r/issues/5"] = ok(
        {
            "number": 5,
            "title": "Fix it",
            "body": "It is broken",
            "user": {"login": "alice"},
            "labels": [{"name": "bug"}, {"name": "p1"}],
            "state": "open",
        }
    )
    fake_runner.responses["gh api --paginate --slurp repos/o/r/issues/5/comments"] = ok(
        [[issue_comment(1, "bob", "me too", "2026-01-01T00:00:00Z")]]
    )
    gh = make(fake_runner)
    issue = gh.issue(5)
    assert issue.author == "alice"
    assert issue.labels == ("bug", "p1")
    assert issue.comments[0].kind == "issue"
    assert gh.issue_markdown(issue) == (
        "# Fix it (#5)\n\nAuthor: alice\nLabels: bug, p1\n\nIt is broken\n\n"
        "## Comments\n\n### bob at 2026-01-01T00:00:00Z\nme too\n"
    )
    assert fake_runner.calls[0] == ["gh", "api", "repos/o/r/issues/5", *ACCEPT]


def test_issue_markdown_without_comments(fake_runner):
    gh = make(fake_runner)
    issue = Issue(1, "T", "B", "a", (), "open", ())
    assert gh.issue_markdown(issue) == "# T (#1)\n\nAuthor: a\nLabels: \n\nB\n"


def test_upsert_creates_when_no_marker(fake_runner):
    fake_runner.responses["gh api --paginate --slurp repos/o/r/issues/7/comments"] = ok([[]])
    fake_runner.responses["gh api repos/o/r/issues/7/comments"] = ok({"id": 99})
    assert make(fake_runner).upsert_comment(7, "status", "hello\n\"q\"") == 99
    post = fake_runner.calls[-1]
    assert post == ["gh", "api", "repos/o/r/issues/7/comments", *ACCEPT, "--method", "POST", "--input", "-"]
    body = json.loads(fake_runner.kwargs[-1]["input"])["body"]
    assert body == "hello\n\"q\"\n\n" + MARKER.format(kind="status")
    assert body.endswith("<!-- blacksmith:status -->")


def test_upsert_edits_when_marker_found(fake_runner):
    marked = "old\n\n<!-- blacksmith:status -->"
    fake_runner.responses["gh api --paginate --slurp repos/o/r/issues/7/comments"] = ok(
        [[issue_comment(3, "bot", "x", "t"), issue_comment(42, "bot", marked, "t")]]
    )
    fake_runner.responses["gh api repos/o/r/issues/comments/42"] = ok({"id": 42})
    assert make(fake_runner).upsert_comment(7, "status", "new") == 42
    assert fake_runner.calls[-1] == [
        "gh", "api", "repos/o/r/issues/comments/42", *ACCEPT, "--method", "PATCH", "--input", "-",
    ]
    assert json.loads(fake_runner.kwargs[-1]["input"]) == {"body": "new\n\n<!-- blacksmith:status -->"}


def test_upsert_refuses_secret(fake_runner):
    with pytest.raises(SecretLeak):
        make(fake_runner).upsert_comment(7, "status", f"leak {SECRET}")
    assert fake_runner.calls == []


def test_create_pr_reuses_existing_open_pr(fake_runner):
    fake_runner.responses["gh pr list"] = ok(
        [{"number": 12, "url": "https://github.com/o/r/pull/12", "headRefName": "feat"}]
    )
    pr = make(fake_runner).create_pr("feat", "main", "T", "body", draft=False)
    assert (pr.number, pr.url, pr.head) == (12, "https://github.com/o/r/pull/12", "feat")
    assert fake_runner.calls[0] == [
        "gh", "pr", "list", "--repo", REPO, "--head", "feat", "--state", "open",
        "--json", "number,url,headRefName",
    ]
    assert fake_runner.calls[1] == [
        "gh", "pr", "edit", "12", "--repo", REPO, "--title", "T", "--body-file", "-",
    ]
    assert fake_runner.kwargs[1]["input"] == "body"
    assert not any(c[:3] == ["gh", "pr", "create"] for c in fake_runner.calls)


def test_create_pr_draft_flag(fake_runner):
    fake_runner.responses["gh pr list"] = ok([])
    fake_runner.responses["gh pr create"] = CommandResult(
        0, "Creating...\nhttps://github.com/o/r/pull/31\n", ""
    )
    pr = make(fake_runner).create_pr("feat", "main", "T", "body", draft=True)
    assert (pr.number, pr.url, pr.head) == (31, "https://github.com/o/r/pull/31", "feat")
    assert fake_runner.calls[1] == [
        "gh", "pr", "create", "--repo", REPO, "--head", "feat", "--base", "main",
        "--title", "T", "--body-file", "-", "--draft",
    ]
    assert fake_runner.kwargs[1]["input"] == "body"


def test_create_pr_without_draft_has_no_flag(fake_runner):
    fake_runner.responses["gh pr list"] = ok([])
    fake_runner.responses["gh pr create"] = CommandResult(0, "https://github.com/o/r/pull/2\n", "")
    make(fake_runner).create_pr("feat", "main", "T", "body", draft=False)
    assert "--draft" not in fake_runner.calls[1]


def test_update_pr_body(fake_runner):
    make(fake_runner).update_pr_body(4, "new body")
    assert fake_runner.calls == [["gh", "pr", "edit", "4", "--repo", REPO, "--body-file", "-"]]
    assert fake_runner.kwargs[0]["input"] == "new body"


def _review_comment(id_, body, created_at):
    return {
        "id": id_, "user": {"login": "rev"}, "body": body, "created_at": created_at,
        "path": "a.py", "line": None, "original_line": 8,
    }


def test_human_comments_since_filters_marker_and_old(fake_runner):
    fake_runner.responses["gh api --paginate --slurp repos/o/r/issues/9/comments"] = ok(
        [[
            issue_comment(1, "h", "old", "2026-01-01T00:00:00Z"),
            issue_comment(2, "bot", "bot\n\n<!-- blacksmith:status -->", "2026-01-03T00:00:00Z"),
            issue_comment(3, "h", "new", "2026-01-03T00:00:00Z"),
        ]]
    )
    out = make(fake_runner).human_comments_since(9, "2026-01-02T00:00:00Z")
    assert [c.id for c in out] == [3]
    assert "since=2026-01-02T00%3A00%3A00Z" in fake_runner.calls[0][4]


def test_human_comments_merges_three_sources_sorted(fake_runner):
    fake_runner.responses["gh api --paginate --slurp repos/o/r/issues/9/comments"] = ok(
        [[issue_comment(1, "h", "c", "2026-01-03T00:00:00Z")]]
    )
    fake_runner.responses["gh api --paginate --slurp repos/o/r/pulls/9/comments"] = ok(
        [[_review_comment(2, "rc", "2026-01-02T00:00:00Z")]]
    )
    fake_runner.responses["gh api --paginate --slurp repos/o/r/pulls/9/reviews"] = ok(
        [[
            {"id": 3, "user": {"login": "r"}, "body": "rv", "submitted_at": "2026-01-01T00:00:00Z"},
            {"id": 4, "user": {"login": "r"}, "body": "", "submitted_at": "2026-01-04T00:00:00Z"},
        ]]
    )
    out = make(fake_runner).human_comments_since(9, None)
    assert [(c.id, c.kind) for c in out] == [(3, "review"), (2, "review_comment"), (1, "issue")]
    assert out[1].path == "a.py" and out[1].line == 8
    assert out[0].created_at == "2026-01-01T00:00:00Z"


def test_reply_review_comment_and_issue_comment(fake_runner):
    gh = make(fake_runner)
    gh.reply(Comment(5, "u", "first\nsecond", "t", "review_comment"), 9, "ok")
    assert fake_runner.calls[0] == [
        "gh", "api", "repos/o/r/pulls/9/comments/5/replies", *ACCEPT, "--method", "POST", "--input", "-",
    ]
    assert json.loads(fake_runner.kwargs[0]["input"]) == {"body": "ok"}
    gh.reply(Comment(6, "u", "first\nsecond", "t", "issue"), 9, "ok")
    assert fake_runner.calls[1][2] == "repos/o/r/issues/9/comments"
    assert json.loads(fake_runner.kwargs[1]["input"]) == {"body": "> first\n\nok"}


def test_request_review_skipped_for_self(fake_runner):
    fake_runner.responses["gh api user"] = CommandResult(0, "me\n", "")
    gh = make(fake_runner)
    assert gh.request_review(3, "me") is False
    assert fake_runner.calls == [["gh", "api", "user", "--jq", ".login"]]
    assert gh.request_review(3, "other") is True
    assert fake_runner.calls[-1] == [
        "gh", "api", "repos/o/r/pulls/3/requested_reviewers", *ACCEPT, "--method", "POST", "--input", "-",
    ]
    assert json.loads(fake_runner.kwargs[-1]["input"]) == {"reviewers": ["other"]}
    assert sum(1 for c in fake_runner.calls if c[:3] == ["gh", "api", "user"]) == 1


def test_dispatch_sends_payload_on_stdin(fake_runner):
    make(fake_runner).dispatch("go", {"a": "line\nbreak"})
    assert fake_runner.calls == [
        ["gh", "api", "repos/o/r/dispatches", *ACCEPT, "--method", "POST", "--input", "-"]
    ]
    assert json.loads(fake_runner.kwargs[0]["input"]) == {
        "event_type": "go", "client_payload": {"a": "line\nbreak"},
    }


def test_nonzero_exit_raises_with_command_and_stderr(fake_runner):
    fake_runner.responses["gh api"] = CommandResult(1, "", "boom")
    with pytest.raises(GitHubError) as err:
        make(fake_runner).dispatch("go", {"secretish": "payload"})
    assert "gh api repos/o/r/dispatches" in str(err.value)
    assert "boom" in str(err.value)
    assert "payload" not in str(err.value)


def test_reply_refuses_secret_in_quoted_comment(fake_runner):
    comment = Comment(6, "u", f"quoted {SECRET}", "t", "issue")
    with pytest.raises(SecretLeak):
        make(fake_runner).reply(comment, 9, "ok")
    assert fake_runner.calls == []


def test_dispatch_refuses_secret_in_payload(fake_runner):
    with pytest.raises(SecretLeak):
        make(fake_runner).dispatch("go", {"k": f"v {SECRET}"})
    assert fake_runner.calls == []
