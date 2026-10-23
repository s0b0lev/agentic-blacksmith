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


ASSETS = "blacksmith-assets/issue-7"


def test_push_assets_returns_none_without_pngs(tmp_path, fake_runner):
    (tmp_path / "notes.txt").write_text("x")
    assert git.push_assets(REPO, tmp_path, ASSETS, fake_runner) is None
    assert git.push_assets(REPO, tmp_path / "missing", ASSETS, fake_runner) is None
    assert fake_runner.calls == []


@pytest.mark.parametrize("remote", [
    "https://github.com/acme/shop.git", "https://github.com/acme/shop",
    "git@github.com:acme/shop.git", "git@github.com:acme/shop",
])
def test_push_assets_command_sequence_and_url(tmp_path, fake_runner, remote):
    (tmp_path / "01-home.png").write_bytes(b"png")
    fake_runner.responses["git remote get-url origin"] = CommandResult(0, remote + "\n", "")
    fake_runner.responses["git write-tree"] = CommandResult(0, "tree123\n", "")
    fake_runner.responses["git -c user.name"] = CommandResult(0, "sha456\n", "")
    url = git.push_assets(REPO, tmp_path, ASSETS, fake_runner)
    assert url == f"https://raw.githubusercontent.com/acme/shop/{ASSETS}/"
    calls = fake_runner.calls
    assert calls[0] == ["git", "remote", "get-url", "origin"]
    assert calls[1] == ["git", f"--work-tree={tmp_path}", "add", "-f", "--all"]
    assert calls[2] == ["git", "write-tree"]
    assert calls[3] == ["git", *git.BOT_IDENTITY, "commit-tree", "tree123",
                        "-m", f"blacksmith assets for {ASSETS}"]
    assert calls[4] == ["git", "push", "-f", "origin", f"sha456:refs/heads/{ASSETS}"]
    index_envs = {kw["env"]["GIT_INDEX_FILE"] for kw in fake_runner.kwargs[1:3]}
    assert len(index_envs) == 1 and "PATH" in fake_runner.kwargs[1]["env"]
    assert all(kw["cwd"] == REPO for kw in fake_runner.kwargs)


def test_push_assets_rejects_non_github_remote(tmp_path, fake_runner):
    (tmp_path / "01-home.png").write_bytes(b"png")
    fake_runner.responses["git remote"] = CommandResult(0, "https://gitlab.com/a/b.git\n", "")
    with pytest.raises(git.GitError, match="gitlab"):
        git.push_assets(REPO, tmp_path, ASSETS, fake_runner)
