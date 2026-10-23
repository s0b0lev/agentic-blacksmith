from pathlib import Path

import pytest

from blacksmith import git
from blacksmith.runner import CommandResult

REPO = Path("/repo")


def test_push_uses_explicit_refspec_to_branch_only(fake_runner):
    git.push_branch(REPO, "blacksmith/issue-7", fake_runner)
    assert fake_runner.calls == [["git", "push", "origin", "HEAD:refs/heads/blacksmith/issue-7"]]
    assert fake_runner.kwargs[0]["cwd"] == REPO


def test_push_failure_raises(fake_runner):
    fake_runner.responses["git push"] = CommandResult(1, "", "rejected")
    with pytest.raises(git.GitError, match="rejected"):
        git.push_branch(REPO, "b", fake_runner)


def test_commit_all_false_when_clean(fake_runner):
    assert git.commit_all(REPO, "msg", fake_runner) is False
    assert fake_runner.calls == [["git", "status", "--porcelain"]]


def test_commit_all_commits_as_bot_when_dirty(fake_runner):
    fake_runner.responses["git status"] = CommandResult(0, " M a.py\n", "")
    assert git.commit_all(REPO, "build: issue 7", fake_runner) is True
    assert fake_runner.calls[1:] == [
        ["git", "add", "-A"],
        ["git", "-c", "user.name=blacksmith[bot]",
         "-c", "user.email=blacksmith@users.noreply.github.com",
         "commit", "-q", "-m", "build: issue 7"],
    ]


def test_changed_paths_uses_three_dot_diff(fake_runner):
    fake_runner.responses["git diff"] = CommandResult(0, "storefront/a.tsx\napi/b.py\n", "")
    assert git.changed_paths(REPO, "main", fake_runner) == ["storefront/a.tsx", "api/b.py"]
    assert fake_runner.calls == [["git", "diff", "--name-only", "main...HEAD"]]
