"""Minimal git operations for the runner, all through a CommandRunner."""

import os
import re
import tempfile
from pathlib import Path

from blacksmith.runner import CommandResult, CommandRunner

BOT_IDENTITY = ["-c", "user.name=blacksmith[bot]",
                "-c", "user.email=blacksmith[bot]@users.noreply.github.com"]  # fmt: skip


class GitError(RuntimeError):
    pass


_GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def _git(repo: Path, args: list[str], run: CommandRunner,
         env: dict[str, str] | None = None) -> CommandResult:
    res = run(["git", *args], cwd=repo, **({"env": env} if env else {}))
    if res.code != 0:
        raise GitError(f"git {' '.join(args)} failed ({res.code}): {res.stderr.strip()}")
    return res


def commit_all(repo: Path, message: str, run: CommandRunner) -> bool:
    """Stage and commit everything; False when the tree is already clean."""
    if not _git(repo, ["status", "--porcelain"], run).stdout.strip():
        return False
    _git(repo, ["add", "-A"], run)
    _git(repo, [*BOT_IDENTITY, "commit", "-q", "-m", message], run)
    return True


def push_branch(repo: Path, branch: str, run: CommandRunner) -> None:
    _git(repo, ["push", "origin", f"HEAD:refs/heads/{branch}"], run)


def changed_paths(repo: Path, base: str, run: CommandRunner) -> list[str]:
    out = _git(repo, ["diff", "--name-only", f"{base}...HEAD"], run).stdout
    return [line for line in out.splitlines() if line.strip()]


def push_assets(repo: Path, shots_dir: Path, branch: str, run: CommandRunner) -> str | None:
    """Force-push the PNGs in ``shots_dir`` as a parentless commit to ``branch``.

    Uses a throwaway index so the repository's own index and work tree are untouched.
    Returns the github.com/<owner>/<name>/raw/<branch>/ base URL, or None without PNGs.
    """
    if not any(shots_dir.glob("*.png")):
        return None
    remote = _git(repo, ["remote", "get-url", "origin"], run).stdout.strip()
    match = _GITHUB_REMOTE.match(remote)
    if not match:
        raise GitError(f"origin is not a GitHub remote: {remote}")
    owner, name = match.groups()
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(repo, [f"--work-tree={shots_dir}", "add", "-f", "--all"], run, env)
        tree = _git(repo, ["write-tree"], run, env).stdout.strip()
    commit = _git(repo, [*BOT_IDENTITY, "commit-tree", tree,
                         "-m", f"blacksmith assets for {branch}"], run).stdout.strip()
    _git(repo, ["push", "-f", "origin", f"{commit}:refs/heads/{branch}"], run)
    return f"https://github.com/{owner}/{name}/raw/{branch}/"
