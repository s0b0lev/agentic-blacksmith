import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
        input: str | None = None,
    ) -> CommandResult: ...


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def subprocess_runner(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> CommandResult:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            env=env,
            input=input,
            text=True,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(124, _text(exc.stdout), _text(exc.stderr))
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)
