"""Structured trace events.

THE signature everything else calls:

    emit(event: str, payload: Mapping | None = None, level: Level = "info") -> TraceEvent

`level` is one of "info" | "guardrail" | "error". Guardrail verdicts MUST use
level="guardrail": the trace panel renders those distinctly, and they are the artifact
that proves a check ran rather than that a prompt asked for one.

This is the ring only. Per-session sink binding across `asyncio.create_task()`, the
storefront/UCP instrumentation and the evidence bundle land alongside `evidence.py`.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    payload: dict[str, Any] = field(default_factory=dict)
    level: Level = "info"

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "payload": self.payload,
            "level": self.level,
        }


_seq = count(1)
_ring: deque[TraceEvent] = deque(maxlen=RING)


def emit(event: str, payload: Mapping[str, Any] | None = None, level: Level = "info") -> TraceEvent:
    entry = TraceEvent(
        seq=next(_seq), ts=time.time(), event=event, payload=dict(payload or {}), level=level
    )
    _ring.append(entry)
    return entry


def events(level: Level | None = None) -> list[TraceEvent]:
    return [e for e in _ring if level is None or e.level == level]


def reset() -> None:
    _ring.clear()
