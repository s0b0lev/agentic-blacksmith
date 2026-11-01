import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from blacksmith.runner import CommandRunner

TIMEOUT_CODE = 124
# The runner pushes, talks to GitHub, posts to Telegram and ships telemetry to Grafana Cloud;
# the agent session needs none of those tokens.
RUNNER_ONLY_ENV = (
    "GH_TOKEN",
    "BLACKSMITH_GITHUB_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "GRAFANA_CLOUD_TOKEN",
)


def agent_env(env: Mapping[str, str]) -> dict[str, str]:
    """The runner's environment minus the GitHub tokens, for a Claude session."""
    return {k: v for k, v in env.items() if k not in RUNNER_ONLY_ENV}


@dataclass(frozen=True)
class StageResult:
    ok: bool
    timed_out: bool
    result_text: str
    num_turns: int | None
    transcript: Path
    cost_note: str
    duration_ms: int | None = None  # wall clock of the session as Claude Code reports it
    cost_usd: float | None = None


def run_claude(
    prompt: str,
    *,
    cwd: Path,
    max_turns: int,
    timeout_s: int,
    transcript: Path,
    run: CommandRunner,
    env: Mapping[str, str],
    extra_args: Sequence[str] = (),
) -> StageResult:
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--dangerously-skip-permissions",
        *extra_args,
    ]
    res = run(cmd, cwd=cwd, timeout=timeout_s, env=dict(env), input=prompt)
    timed_out = res.code == TIMEOUT_CODE

    result_text = res.stdout
    num_turns: int | None = None
    is_error = False
    cost_note = ""
    duration_ms: int | None = None
    cost_usd: float | None = None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        data = None
    if isinstance(data, dict):
        result_text = str(data.get("result", ""))
        num_turns = data.get("num_turns")
        is_error = bool(data.get("is_error"))
        if "total_cost_usd" in data:
            cost_note = f"cost_usd={data['total_cost_usd']}"
            cost_usd = _number(data["total_cost_usd"])
        duration_ms = _number(data.get("duration_ms"))
        if duration_ms is not None:
            duration_ms = int(duration_ms)

    ok = res.code == 0 and not is_error and not timed_out

    content = res.stdout
    if timed_out or res.code != 0:
        content += f"\n\n== stderr ==\n{res.stderr}"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(content)

    return StageResult(ok, timed_out, result_text, num_turns, transcript, cost_note,
                       duration_ms, cost_usd)


def _number(value) -> float | None:
    """A finite number from the result JSON, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None
