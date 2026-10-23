"""Persistent runner state stored in .blacksmith/state.json."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class State:
    issue: int
    branch: str
    pr_number: int | None = None
    pr_url: str | None = None
    progress_comment_id: int | None = None
    last_seen: str | None = None
    stage: str = "init"
    round: int = 0


def load_state(path: Path) -> State:
    return State(**json.loads(path.read_text()))


def save_state(path: Path, state: State) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
