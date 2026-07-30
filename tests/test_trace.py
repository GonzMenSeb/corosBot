"""The log the evidence bundle is built from.

`evidence.py` decides whether a recommendation may be presented by reading these
events, so two properties are not decoration: a record nobody can edit after the fact,
and a sink that routes a turn's events to the session that produced them. Every test
here is about one of those two.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from coros_core import trace

FACTS = "AGENTS.md's trace row of the maintenance contract"


class TestTheLogIsAppendOnly:
    def test_a_recorded_payload_cannot_be_edited_through_the_event(self) -> None:
        event = trace.emit("guardrail.stock", {"confirmed": 2, "rejected": [{"id": "x"}]})

        event.payload["confirmed"] = 99
        event.payload["rejected"].append({"id": "invented"})

        assert trace.events()[-1].payload == {"confirmed": 2, "rejected": [{"id": "x"}]}, (
            "a recorded verdict was rewritten through the event it was recorded in.\n"
            f"  evidence.py derives 'did this check run, and did it pass' from these\n"
            f"  payloads; a record a caller can edit is not evidence. See {FACTS}."
        )

    def test_a_caller_that_keeps_mutating_its_own_dict_changes_nothing(self) -> None:
        payload: dict[str, Any] = {"cleared": ["pace-4"]}
        trace.emit("guardrail.local_availability", payload)

        payload["cleared"].append("vertix-2")
        payload["unavailable"] = [{"slug": "vertix-2"}]

        assert trace.events()[-1].payload == {"cleared": ["pace-4"]}

    def test_two_reads_hand_back_independent_objects(self) -> None:
        trace.emit("guardrail.budget", {"swap_candidates": [{"title": "APEX 4"}]})

        first, second = trace.events()[-1].payload, trace.events()[-1].payload
        first["swap_candidates"].clear()

        assert second["swap_candidates"] == [{"title": "APEX 4"}]

    def test_a_payload_that_is_not_json_is_recorded_as_text_rather_than_lost(self) -> None:
        """A lossy record beats a raised exception: an observability layer that can take
        down the run it observes is worse than none."""
        trace.emit("catalog.unavailable", {"error": ValueError("429")})

        assert trace.events()[-1].payload == {"error": "429"}

    def test_the_stored_record_is_the_bytes_a_replay_would_read(self) -> None:
        event = trace.emit("agent.stage", {"stage": "retrieval"})

        assert json.loads(event.raw) == {"stage": "retrieval"}
        assert event.as_dict()["payload"] == {"stage": "retrieval"}

    def test_the_ring_says_how_much_it_dropped_rather_than_pretending(self) -> None:
        for i in range(trace.RING + 7):
            trace.emit("agent.tick", {"i": i})

        assert len(trace.events()) == trace.RING
        assert trace.dropped() == 7, (
            "the ring silently forgot events. A bounded log is fine; a bounded log that\n"
            "  reads as complete is not — evidence.py reports the loss as an untested region."
        )

    def test_sequence_numbers_are_not_reused_so_a_gap_reads_as_a_loss(self) -> None:
        first = trace.emit("agent.stage").seq
        trace.reset()
        assert trace.emit("agent.stage").seq > first


class TestASinkIsBoundBeforeTheTaskThatEmitsIntoIt:
    """`bind_sink()` before `asyncio.create_task()` — contextvars are copied at
    task-creation time, so the ordering is the whole mechanism (DecaBot `state.py:5-8`)."""

    async def test_a_task_created_after_binding_emits_into_that_sink(self) -> None:
        sink: list[trace.TraceEvent] = []
        trace.bind_sink(sink)
        try:
            await asyncio.create_task(_turn("brujula"))
        finally:
            trace.bind_sink(None)

        assert [e.event for e in sink] == ["agent.turn"]
        assert sink[0].payload == {"app": "brujula"}

    async def test_a_task_created_before_binding_never_reaches_it(self) -> None:
        """The bug this ordering prevents, pinned as behaviour: bind after create_task and
        the turn's guardrail verdicts land in the process ring, where a per-session panel
        and a per-turn evidence bundle cannot see them."""
        gate = asyncio.Event()
        task = asyncio.create_task(_gated_turn(gate))

        sink: list[trace.TraceEvent] = []
        trace.bind_sink(sink)
        try:
            gate.set()
            await task
        finally:
            trace.bind_sink(None)

        assert sink == []
        assert [e.event for e in trace.events()] == ["agent.turn"]

    async def test_two_concurrent_sessions_do_not_read_each_others_events(self) -> None:
        one: list[trace.TraceEvent] = []
        two: list[trace.TraceEvent] = []

        await asyncio.gather(
            asyncio.create_task(_session(one, "brujula")),
            asyncio.create_task(_session(two, "huella")),
        )

        assert [e.payload["app"] for e in one] == ["brujula"]
        assert [e.payload["app"] for e in two] == ["huella"]

    async def test_the_ring_still_sees_everything_a_sink_saw(self) -> None:
        sink: list[trace.TraceEvent] = []
        trace.bind_sink(sink)
        try:
            trace.emit("agent.turn", {"app": "huella"})
        finally:
            trace.bind_sink(None)

        assert [e.seq for e in trace.events()] == [e.seq for e in sink]

    async def test_an_unbound_turn_falls_back_to_the_ring_so_scripts_can_read_it(
        self,
    ) -> None:
        trace.emit("agent.turn", {"app": "script"})
        assert [e.event for e in trace.current()] == ["agent.turn"]


class TestATurnCanSliceOffItsOwnEvents:
    """A sink is per session and a bundle is per turn, so `mark()`/`since()` is what
    keeps turn three's evidence from carrying turn one's verdicts."""

    def test_only_events_after_the_mark_come_back(self) -> None:
        trace.emit("guardrail.stock", {"turn": 1})
        mark = trace.mark()
        trace.emit("guardrail.stock", {"turn": 2})

        assert [e.payload["turn"] for e in trace.since(mark)] == [2]

    def test_a_mark_taken_before_anything_happened_keeps_everything(self) -> None:
        mark = trace.mark()
        trace.emit("guardrail.stock", {"turn": 1})
        assert len(trace.since(mark)) == 1

    async def test_a_slice_reads_the_bound_sink_and_not_the_process_ring(self) -> None:
        trace.emit("agent.turn", {"app": "someone else"})

        sink: list[trace.TraceEvent] = []
        trace.bind_sink(sink)
        try:
            mark = trace.mark()
            trace.emit("guardrail.stock", {"turn": 1})
            sliced = trace.since(mark)
        finally:
            trace.bind_sink(None)

        assert [e.event for e in sliced] == ["guardrail.stock"]


class TestLevelsStayTheThreeTheUiRenders:
    def test_the_default_is_info_and_guardrail_is_opt_in(self) -> None:
        assert trace.emit("agent.stage").level == "info"
        assert trace.emit("guardrail.stock", {}, "guardrail").level == "guardrail"

    def test_the_ring_can_be_read_by_level(self) -> None:
        trace.emit("agent.stage")
        trace.emit("guardrail.stock", {}, "guardrail")
        trace.emit("catalog.unavailable", {}, "error")

        assert [e.event for e in trace.events("guardrail")] == ["guardrail.stock"]
        assert [e.event for e in trace.events("error")] == ["catalog.unavailable"]


async def _turn(app: str) -> None:
    trace.emit("agent.turn", {"app": app})


async def _gated_turn(gate: asyncio.Event) -> None:
    await gate.wait()
    trace.emit("agent.turn", {"app": "brujula"})


async def _session(sink: list[trace.TraceEvent], app: str) -> None:
    trace.bind_sink(sink)
    await asyncio.sleep(0)
    trace.emit("agent.turn", {"app": app})
