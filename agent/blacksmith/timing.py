"""Wall-clock and agent-time bookkeeping for one run, rendered into the progress comment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


def fmt_duration(seconds: float) -> str:
    """``1h 02m 03s``, ``4m 02s`` or ``37s``; never negative."""
    total = max(0, round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def seconds_between(start_iso: str, end_iso: str) -> float | None:
    """Seconds from ``start_iso`` to ``end_iso``; None when either does not parse."""
    try:
        start = datetime.fromisoformat(start_iso)  # 3.11+ accepts GitHub's trailing Z
        end = datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return None
    return (end - start).total_seconds()


@dataclass
class StageTiming:
    seconds: float = 0.0        # wall clock spent in the stage
    agent_seconds: float = 0.0  # time inside Claude Code sessions during the stage
    sessions: int = 0


@dataclass
class Timing:
    """Per-run record. ``started_at`` and ``labeled_at`` are wall clock (ISO 8601); every
    duration comes from the injected monotonic clock so a changed system clock cannot skew it.
    """

    started_at: str
    labeled_at: str | None = None
    finished_at: str | None = None
    total_seconds: float = 0.0
    cost_usd: float = 0.0
    stages: dict[str, StageTiming] = field(default_factory=dict)
    # Monotonic bookkeeping; not persisted.
    _run_start: float = field(default=0.0, repr=False, compare=False)
    _stage_start: float = field(default=0.0, repr=False, compare=False)
    _current: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def begin(cls, started_at: str, mono: float, labeled_at: str | None = None) -> Timing:
        return cls(started_at=started_at, labeled_at=labeled_at, _run_start=mono)

    def start_stage(self, name: str, mono: float) -> None:
        self._current, self._stage_start = name, mono
        self.stages.setdefault(name, StageTiming())

    def end_stage(self, mono: float) -> float | None:
        """Close the current stage; returns its wall-clock seconds, None when none is open."""
        if self._current is None:
            return None
        stage = self.stages[self._current]
        stage.seconds += mono - self._stage_start
        self.total_seconds = mono - self._run_start
        self._current = None
        return stage.seconds

    def add_session(self, duration_ms: int | None, cost_usd: float | None) -> None:
        """Account one Claude Code session to the current stage (or to a ``-`` bucket)."""
        stage = self.stages.setdefault(self._current or "-", StageTiming())
        stage.sessions += 1
        if duration_ms:
            stage.agent_seconds += duration_ms / 1000
        if cost_usd:
            self.cost_usd += cost_usd

    def finish(self, mono: float, finished_at: str) -> None:
        self.end_stage(mono)
        self.total_seconds = mono - self._run_start
        self.finished_at = finished_at

    @property
    def agent_seconds(self) -> float:
        return sum(s.agent_seconds for s in self.stages.values())

    @property
    def sessions(self) -> int:
        return sum(s.sessions for s in self.stages.values())

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}

    def render(self) -> str:
        """Markdown ``### Timing`` section; empty until the first stage has finished."""
        done = [(n, s) for n, s in self.stages.items() if n != self._current or self.finished_at]
        if not done:
            return ""
        rows = ["| stage | wall clock | agent sessions |", "|---|---|---|"]
        for name, s in done:
            rows.append(f"| {name} | {fmt_duration(s.seconds)} | {_sessions(s.agent_seconds, s.sessions)} |")
        if self.finished_at:
            total_agent = _sessions(self.agent_seconds, self.sessions)
            if self.cost_usd:
                total_agent += f", ${self.cost_usd:.2f}"
            rows.append(f"| **total** | **{fmt_duration(self.total_seconds)}** | **{total_agent}** |")
        text = "### Timing\n\n" + "\n".join(rows)
        if self.finished_at and self.labeled_at:
            since_label = seconds_between(self.labeled_at, self.finished_at)
            lead = seconds_between(self.labeled_at, self.started_at)
            if since_label is not None and lead is not None and since_label >= 0 and lead >= 0:
                text += (f"\n\nFrom the label to the finish: {fmt_duration(since_label)}, of which "
                         f"{fmt_duration(lead)} passed before the runner started.")
        return text


def _sessions(agent_seconds: float, sessions: int) -> str:
    if not sessions:
        return "none"
    noun = "session" if sessions == 1 else "sessions"
    return f"{fmt_duration(agent_seconds)} in {sessions} {noun}"
