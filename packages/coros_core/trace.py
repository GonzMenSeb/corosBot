"""The append-only trace.

THE signature everything else calls:

    emit(event: str, payload: Mapping | None = None, level: Level = "info") -> TraceEvent

`level` is one of "info" | "guardrail" | "error". Guardrail verdicts MUST use
level="guardrail": the trace panel renders those distinctly, and they are the artifact
that proves a check ran rather than that a prompt asked for one.

Two properties here are load-bearing, and both exist because `evidence.py` decides
whether a recommendation may be presented by reading this log:

  * **A payload is stored as JSON text and handed back as a fresh object on every
    read.** A record a caller can edit after the fact is not evidence, and a payload
    that cannot be serialised is not a record — so an unserialisable value is written
    as its `str()` rather than raising. An observability layer that can take down the
    run it observes is worse than none.
  * **`bind_sink()` must run BEFORE `asyncio.create_task()`.** contextvars are copied
    at task-creation time, so that ordering is the entire mechanism routing a turn's
    events into the session that produced them instead of the process-wide ring
    (DecaBot `state.py:5-8`). Bound after, the turn's verdicts land somewhere a
    per-session panel and a per-turn evidence bundle cannot see them.

The ring is bounded, so it forgets. What it forgot is counted and reported by
`dropped()`: a bounded log is fine, a bounded log that reads as complete is not.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import count
from typing import Any, Literal

Level = Literal["info", "guardrail", "error"]

# Bounded so a long-running process cannot grow one event at a time. A turn emits tens
# of events; this holds several hundred turns of a single session's worth.
RING = 4000


@dataclass(frozen=True)
class TraceEvent:
    seq: int
    ts: float
    event: str
    raw: str = "{}"
    level: Level = "info"

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.raw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "payload": self.payload,
            "level": self.level,
        }


_seq = count(1)
_last = 0
_dropped = 0
_ring: deque[TraceEvent] = deque(maxlen=RING)
_sink: ContextVar[list[TraceEvent] | None] = ContextVar("coros_trace_sink", default=None)


def bind_sink(sink: list[TraceEvent] | None) -> None:
    """Route subsequent emits into `sink` as well as the ring. Call it before creating
    the task that will do the emitting — see the module docstring."""
    _sink.set(sink)


def emit(event: str, payload: Mapping[str, Any] | None = None, level: Level = "info") -> TraceEvent:
    global _last, _dropped

    entry = TraceEvent(
        seq=next(_seq),
        ts=time.time(),
        event=event,
        raw=json.dumps(dict(payload or {}), ensure_ascii=False, default=str),
        level=level,
    )
    _last = entry.seq

    sink = _sink.get()
    if sink is not None:
        sink.append(entry)
    if len(_ring) == RING:
        _dropped += 1
    _ring.append(entry)
    return entry


def events(level: Level | None = None) -> list[TraceEvent]:
    return [e for e in _ring if level is None or e.level == level]


def current() -> list[TraceEvent]:
    """What a turn can see: the bound sink if there is one, else the process ring."""
    sink = _sink.get()
    return list(sink if sink is not None else _ring)


def mark() -> int:
    """The last sequence number issued. Take one at the top of a turn and pass it to
    `since()`: a sink spans a session, and an evidence bundle covers one turn."""
    return _last


def since(seq: int) -> list[TraceEvent]:
    return [e for e in current() if e.seq > seq]


def dropped() -> int:
    return _dropped


def reset() -> None:
    """Tests only. Sequence numbers deliberately keep climbing: a gap in them is how a
    lost event stays visible."""
    global _dropped
    _ring.clear()
    _dropped = 0
