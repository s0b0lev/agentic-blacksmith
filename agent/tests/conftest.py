import pytest

from blacksmith.runner import CommandResult


class FakeRunner:
    """Records calls; returns the first response whose key prefixes the command."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.responses: dict[str, CommandResult] = {}

    def __call__(self, cmd: list[str], **kwargs) -> CommandResult:
        self.calls.append(list(cmd))
        self.kwargs.append(kwargs)
        joined = " ".join(cmd)
        for key, result in self.responses.items():
            if joined.startswith(key):
                return result
        return CommandResult(0, "", "")


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()
