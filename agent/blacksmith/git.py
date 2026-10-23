"""Minimal git operations for the runner, all through a CommandRunner."""

from pathlib import Path

from blacksmith.runner import CommandResult, CommandRunner

BOT_IDENTITY = ["-c", "user.name=blacksmith[bot]",
                "-c", "user.email=blacksmith@users.noreply.github.com"]  # fmt: skip


class GitError(RuntimeError):
    pass


def _git(repo: Path, args: list[str], run: CommandRunner) -> CommandResult:
    res = run(["git", *args], cwd=repo)
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
