import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from blacksmith.runner import CommandRunner

TIMEOUT_CODE = 124


@dataclass(frozen=True)
class StageResult:
    ok: bool
    timed_out: bool
    result_text: str
    num_turns: int | None
    transcript: Path
    cost_note: str


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

    ok = res.code == 0 and not is_error and not timed_out

    content = res.stdout
    if timed_out or res.code != 0:
        content += f"\n\n== stderr ==\n{res.stderr}"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(content)

    return StageResult(ok, timed_out, result_text, num_turns, transcript, cost_note)
