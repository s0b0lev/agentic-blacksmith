import os
import signal
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
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
            return CommandResult(124, _text(stdout), _text(stderr))
    return CommandResult(proc.returncode, stdout, stderr)
