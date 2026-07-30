"""Huella's Reflex layer: what crosses the wire, what must never, and which reading
produced what is on screen.

Four questions run through every test here.

**Does the browser get facts or prose?** Cards, prices, requirement rows and audit rows are
rebuilt from `Advice`, `Requirement` and `EvidenceBundle` on the server, so the tests assert
against typed values and never against a sentence a model wrote.

**Does the turn's trace reach the session that produced it?** `bind_sink()` before
`asyncio.create_task()` is the whole mechanism, and a bundle that sees no events accepts
nothing — so the ordering is asserted in the source *and* driven behaviourally.

**Does the state hold anything it is not allowed to?** The credential and the window live in
`privacy._SESSIONS`. `tests/test_privacy_boundary.py` scans for the types; these check that
what DID cross is a count, a label or a digest.

**And does an interview answer ever get to read as a measured one?** That is Huella's whole
premise, so `advice_mode` is derived from the turn's own `UncertaintyVerdict` and is asserted
on both sides of `tools.MIN_SAMPLE`.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any, get_args

import pytest
from reflex.istate.data import RouterData

from coros_core import devices, evidence, trace
from coros_core.evidence import Check, EvidenceBundle
from coros_core.models import Advice, AdviceItem, Requirement, RequirementKey
from coros_core.outcomes import ToolOutcome
from huella import oauth, privacy
from huella import state as st
from huella.agent import loop, tools

REPO = Path(__file__).resolve().parent.parent
FACTS = 'AGENTS.md "load-bearing facts"'

PACE_4_MINOR = 109_900_000


def fresh() -> Any:
    return st.State(_reflex_internal_init=True)


def _routed(as_path: str, query: dict[str, str]) -> Any:
    """A state whose `router` carries a real URL.

    `object.__setattr__` rather than assignment: `router` is an inherited var, so a plain
    `state.router = …` on a substate with no parent recurses into `None`. The READ path
    falls through to the instance attribute, which is what the handler under test uses.
    """
    state = fresh()
    router = RouterData.from_router_data(
        {
            "headers": {"origin": "http://localhost:3001"},
            "pathname": "/",
            "asPath": as_path,
            "query": query,
            "token": "",
        }
    )
    object.__setattr__(state, "router", router)
    return state


@pytest.fixture(autouse=True)
def _no_state_left_behind():
    st._CONVERSATIONS.clear()
    oauth._PENDING.clear()
    privacy.forget_all()
    yield
    st._CONVERSATIONS.clear()
    oauth._PENDING.clear()
    privacy.forget_all()


def ev(event: str, payload: dict[str, Any] | None = None, level: str = "info", seq: int = 1):
    return trace.TraceEvent(
        seq=seq, ts=0.0, event=event, raw=json.dumps(payload or {}), level=level
    )


def item(**over: Any) -> AdviceItem:
    return AdviceItem(
        **{
            "product_id": "8039258423531",
            "title": "COROS PACE 4",
            "product_url": "https://coros.com.co/products/coros-pace-4",
            "image_url": "https://coros.com.co/cdn/shop/files/pace-4.png",
            "variant_id": "44100000000001",
            "price_minor": PACE_4_MINOR,
            "rationale": "batería suficiente para tus salidas largas",
            "satisfies": ("discipline", "weekly_hours_band"),
            **over,
        }
    )


def bundle(accepted: bool = True, **over: Any) -> EvidenceBundle:
    checks = tuple(
        Check(
            name=name,
            event=f"guardrail.{name}",
            outcome="pass" if accepted else "not_run",
            verifies="…",
            cannot_verify="…",
            confidence="high",
            detail="4 items",
        )
        for name in ("provenance", "stock", "budget", "local_availability", "buy_nothing")
    )
    return EvidenceBundle(
        **{
            "accepted": accepted,
            "kind": "recommend",
            "item_count": 1,
            "checks": checks,
            "blocking": () if accepted else ("stock did not run",),
            **over,
        }
    )


def sync(activities: int, **over: Any) -> privacy.Sync:
    """A `Sync` the gate really could have produced: the requirements are built by
    `privacy._requirement`, so `seal()` — which `DemonstratedTraining` runs again — passes."""
    return privacy.Sync(
        **{
            "outcome": ToolOutcome.OK,
            "requirements": (
                privacy._requirement(
                    "discipline", "trail_run", sample_size=activities, window_days=90
                ),
                privacy._requirement(
                    "weekly_hours_band", "4-6", sample_size=activities, window_days=90
                ),
            )
            if activities
            else (),
            "window_days": 90,
            "sample_size": activities,
            "stale_days": 2,
            **over,
        }
    )


def verdict(activities: int, *, connected: bool = True) -> tools.UncertaintyVerdict:
    return tools.check_uncertainty(sync(activities), connected=connected)


def result(**over: Any) -> loop.TurnResult:
    return loop.TurnResult(**{"text": "Yo iría con el PACE 4.", "stage": "recommend", **over})


async def drive(state: Any, text: str, turn: loop.TurnResult, monkeypatch) -> None:
    """One turn through the real handler with the agent replaced. `send_message` is the only
    way in for anything a person types, so the tests take that door too."""

    async def _run(message: str, session: Any) -> loop.TurnResult:
        return turn

    monkeypatch.setattr(loop, "run_turn", _run)
    monkeypatch.setattr(st, "run_turn", _run)
    async for _ in state.send_message({"message": text}):
        pass


def emitted_event_names() -> set[str]:
    """Every event name any `emit()` call site in this repo passes as a literal."""
    names: set[str] = set()
    sources = [*(REPO / "packages" / "coros_core").glob("*.py")]
    sources += [*(REPO / "apps" / "huella" / "huella").rglob("*.py")]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name != "emit" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    return names


def source_of(handler: Any) -> str:
    return inspect.getsource(getattr(handler, "fn", handler))


class TestTheConversationDoesNotLiveInAStateVar:
    """The plan's reason for the module-level map was that Reflex cannot hold a dataclass.
    It can — so the map has to be justified by what a state var would COST, or the next
    reader deletes it as superstition."""

    def test_a_dataclass_state_var_is_accepted_and_would_cross_the_wire(self) -> None:
        import reflex as rx

        class Holder(rx.State):
            session: loop.ConversationSession = loop.ConversationSession()

        holder = Holder(_reflex_internal_init=True)
        holder.session.turns.append({"role": "user", "text": "corro trail"})

        assert holder.dirty_vars == {"session"}, (
            "Reflex 0.9.7 tracks a dataclass state var through a MutableProxy — verified\n"
            f"  30 jul 2026. If this fails the wire cost below changed too; see {FACTS}."
        )
        delta = holder.get_delta()[Holder.get_full_name()]
        assert any("corro trail" in str(v) for v in delta.values()), (
            "a dataclass state var serialises to the browser, transcript and all. THAT is\n"
            "  why the conversation lives in state._CONVERSATIONS: not a Reflex limitation,\n"
            "  a cost — every mutation the loop makes would be broadcast, and the DISK state\n"
            "  manager pickles the same bytes into .states/."
        )

    def test_the_conversation_map_is_not_the_credential_map(self) -> None:
        """Two maps in one app, and only one of them may ever hold a token. Keeping the
        names apart is what stops a later reader reaching for the wrong one."""
        assert st._CONVERSATIONS is not privacy._SESSIONS
        assert not hasattr(st, "_SESSIONS"), (
            "huella/state.py grew a `_SESSIONS`. That name belongs to privacy.py, where the\n"
            "  credentials are."
        )

    def test_each_browser_session_gets_its_own_conversation(self) -> None:
        first = st._conversation_for("token-a")
        second = st._conversation_for("token-b")

        first.question = "corro trail"
        assert second.question == "", "two visitors must never share a conversation"

    def test_a_conversation_carries_the_key_the_privacy_gate_is_read_with(self) -> None:
        """`ConversationSession.key` is how `loop._training` reaches `privacy.*`. Left
        empty it reads as an athlete with no Strava connected, which is a lie about a
        connected one — and the interview would run for somebody whose history answered."""
        assert st._conversation_for("token-a").key == "token-a"

    def test_the_same_token_resumes_the_same_conversation(self) -> None:
        st._conversation_for("token-c").question = "busco correa"
        assert st._conversation_for("token-c").question == "busco correa", (
            "requirements, corrections and the finished stages live here — a new session\n"
            "  per turn would re-interview the person on every message"
        )

    def test_clearing_starts_a_new_conversation(self) -> None:
        st._conversation_for("token-d").question = "corro trail"
        st._reset_conversation("token-d")
        assert st._conversation_for("token-d").question == ""

    def test_the_map_is_bounded_and_forgets_the_oldest(self) -> None:
        for n in range(st.MAX_CONVERSATIONS + 5):
            st._conversation_for(f"token-{n}")

        assert len(st._CONVERSATIONS) == st.MAX_CONVERSATIONS
        assert "token-0" not in st._CONVERSATIONS
        assert f"token-{st.MAX_CONVERSATIONS + 4}" in st._CONVERSATIONS


class TestTheSinkIsBoundBeforeTheTaskThatEmits:
    def test_bind_sink_precedes_create_task_in_the_source(self) -> None:
        tree = ast.parse(textwrap.dedent(source_of(st.State.send_message)))
        binds = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "bind_sink"
        ]
        tasks = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_task"
        ]
        assert binds and tasks, "send_message must bind a sink and create the turn's task"
        assert min(binds) < min(tasks), (
            "contextvars are copied at task-creation time, so a sink bound AFTER\n"
            "  create_task routes the turn's verdicts into the process ring instead of this\n"
            f"  session — and evidence.build reads what the sink saw. See {FACTS}."
        )

    async def test_events_emitted_inside_the_turn_reach_this_sessions_trace(
        self, monkeypatch
    ) -> None:
        async def _run(message: str, session: Any) -> loop.TurnResult:
            trace.emit("gate.verdict", {"intent": "advice"})
            trace.emit("guardrail.uncertainty", {"grounded": True}, "guardrail")
            return result()

        monkeypatch.setattr(st, "run_turn", _run)
        state = fresh()
        async for _ in state.send_message({"message": "corro trail"}):
            pass

        seen = [row.event for row in state.trace]
        assert "gate.verdict" in seen and "guardrail.uncertainty" in seen
        assert [r.level for r in state.trace if r.event == "guardrail.uncertainty"] == ["guardrail"]

    async def test_the_sink_is_released_when_the_turn_ends(self, monkeypatch) -> None:
        state = fresh()
        await drive(state, "corro trail", result(), monkeypatch)

        assert trace._sink.get() is None


class TestTheCaptionTellsTheTruthWhileTheTurnRuns:
    def test_every_caption_keys_on_an_event_something_actually_emits(self) -> None:
        keyed = set(st._STAGE_STATUS) | set(st._THROTTLE_STATUS)
        unknown = sorted(keyed - emitted_event_names())
        assert not unknown, (
            f"{unknown} is keyed by a caption and emitted by nothing, so that caption can\n"
            "  never fire. DecaBot shipped a table keyed on the wrong vocabulary and the\n"
            "  spinner froze through the longest stretch of the turn."
        )

    def test_the_caption_moves_through_a_live_turn(self) -> None:
        state = fresh()
        state.status = st.OPENING_STATUS

        seen = []
        for event in (
            "gate.verdict",
            "training.synced",
            "guardrail.uncertainty",
            "turn.snapshot",
            "tool.search_products",
            "selection.built",
            "evidence.bundle",
        ):
            state._drain([ev(event)])
            seen.append(state.status)

        assert len(set(seen)) == len(seen), f"a stage left the caption unmoved: {seen}"
        assert st.OPENING_STATUS not in seen

    def test_a_rate_limit_outranks_the_stage_caption(self) -> None:
        state = fresh()
        state._drain([ev("catalog.rate_limited", level="error")])
        limited = state.status

        state._drain([ev("selection.built")])

        assert state.throttled is True
        assert state.status == limited, (
            "being rate-limited is the more important thing to be saying, and a later\n"
            "  stage event must not quietly take the caption back"
        )

    def test_a_latched_coros_limiter_does_not_claim_to_be_retrying(self) -> None:
        """COROS's 429 latches: it is never retried and never polled, because our own
        retries are what keep the door shut."""
        state = fresh()
        state._drain([ev("catalog.rate_limited", level="error")])

        assert state.status != st._RETRYING
        for promise in ("otra vez", "reintent", "de nuevo", "vuelvo a intentar"):
            assert promise not in state.status.lower(), f"{state.status!r} promises a retry"

    def test_stravas_limiter_and_coross_do_not_say_the_same_thing(self) -> None:
        """Strava publishes its window and it resets on the wall clock, so there IS a time
        after which asking again is correct. COROS's latch has no such promise. Collapsing
        the two teaches a person the wrong thing about the one they are looking at."""
        state = fresh()
        state._drain([ev("strava.rate_limited", level="error")])
        strava = state.status

        state = fresh()
        state._drain([ev("catalog.rate_limited", level="error")])

        assert strava != state.status and strava != ""

    def test_still_trying_reads_differently_from_carrying_on(self) -> None:
        state = fresh()
        state._drain([ev("catalog.retry")])
        retrying = state.status

        state._drain([ev("catalog.unavailable", level="error")])

        assert state.status != retrying


class TestFullPayloadsStayOnTheServer:
    def test_the_raw_trace_is_a_backend_only_var(self) -> None:
        assert "_raw_trace" in st.State.backend_vars
        assert "_raw_trace" not in st.State.vars

    def test_the_wire_row_is_clamped_and_the_stored_payload_is_not(self) -> None:
        state = fresh()
        state._drain([ev("retrieval.done", {"summary": "x" * 900})])

        assert len(state.trace[0].summary) <= st.SUMMARY_CHARS
        assert len(state._raw_trace[0]["payload"]["summary"]) == 900

    def test_the_run_report_is_real_json_not_a_python_repr(self) -> None:
        """Reflex hands state containers back wrapped in `MutableProxy`. `isinstance` sees
        through it and a compact `json.dumps` does not — the C encoder's exact type check
        misses the wrapper and falls through to `default=`."""
        state = fresh()
        state._drain([ev("selection.built", {"kind": "recommend", "items": 1})])

        report = state.run_report()

        assert '"event": "selection.built"' in report, (
            f"payloads serialised as a repr: state.plain() was skipped. See {FACTS}.\n"
            f"  got: {report[-200:]!r}"
        )
        assert "'event'" not in report and '"{' not in report


class TestWhatIsOnScreenIsRebuiltFromTypedAdvice:
    async def test_a_recommendation_becomes_cards_priced_in_pesos(self, monkeypatch) -> None:
        state = fresh()
        advice = Advice(kind="recommend", items=(item(),), explanation="…")
        await drive(state, "corro trail", result(advice=advice), monkeypatch)

        assert [card.title for card in state.cards] == ["COROS PACE 4"]
        assert state.cards[0].price_display == "$1.099.000", (
            "the storefront quotes pesos and UCP quotes centavos — a card off by 100x is\n"
            f"  the failure money.py exists to prevent. See {FACTS}."
        )
        assert state.total_display == "$1.099.000"
        assert state.advice_kind == "recommend" and state.is_refusal is False

    async def test_a_refusal_takes_the_cards_with_it(self, monkeypatch) -> None:
        state = fresh()
        await drive(
            state, "corro trail", result(advice=Advice(kind="recommend", items=(item(),))), monkeypatch
        )
        assert state.cards

        await drive(
            state,
            "y con presupuesto de cien mil",
            result(advice=Advice(kind="buy_nothing", explanation="nada te sirve")),
            monkeypatch,
        )

        assert state.cards == []
        assert state.is_refusal is True

    async def test_a_greeting_does_not_retract_a_standing_recommendation(
        self, monkeypatch
    ) -> None:
        state = fresh()
        await drive(
            state, "corro trail", result(advice=Advice(kind="recommend", items=(item(),))), monkeypatch
        )

        await drive(state, "gracias!", result(text="Hola —", stage="greeting"), monkeypatch)

        assert [card.title for card in state.cards] == ["COROS PACE 4"]

    async def test_an_absent_device_is_named_from_the_registry(self, monkeypatch) -> None:
        state = fresh()
        advice = Advice(kind="not_sold_locally", unavailable_devices=("pace-3",))
        await drive(state, "quiero el PACE 3", result(advice=advice), monkeypatch)

        assert state.unavailable == [devices.require("pace-3").name]
        assert "pace-3" not in state.unavailable

    async def test_the_questions_a_turn_asked_reach_the_screen(self, monkeypatch) -> None:
        state = fresh()
        asked = ("¿Cuántas horas dura tu salida más larga?", "¿Tienes un presupuesto?")
        await drive(state, "quiero un reloj", result(questions=asked, stage="questions"), monkeypatch)

        assert state.questions == list(asked) and state.has_questions is True

    async def test_a_blocked_bundle_is_reported_as_blocked(self, monkeypatch) -> None:
        state = fresh()
        await drive(
            state,
            "corro trail",
            result(advice=Advice(kind="insufficient_evidence"), evidence=bundle(accepted=False)),
            monkeypatch,
        )

        assert state.evidence_accepted is False and state.blocked is True
        assert state.blocking == ["stock did not run"]
        assert state.checks_summary == "0/5"

    def test_every_requirement_key_has_spanish(self) -> None:
        assert set(st._REQUIREMENT_ES) == set(get_args(RequirementKey))

    def test_every_declared_check_has_spanish(self) -> None:
        assert set(st._CHECK_ES) == {check.name for check in evidence._DECLARED}

    async def test_a_requirement_row_says_which_half_was_counted(self, monkeypatch) -> None:
        """The whole reason Huella is a separate app. `derived` is `Requirement.derived`,
        which `privacy.seal()` already refused to let a model set."""
        state = fresh()
        conversation = st._conversation_for(state.router.session.client_token)
        # In that order: `TrainingView.effective` merges the corrections held at BUILD time,
        # which is `loop._bind`'s whole job after the requirements stage writes them.
        conversation.preferences.set("longest_session_band", "3-5")
        conversation.view = tools.training_view(
            sync(20), connected=True, preferences=conversation.preferences
        )

        await drive(state, "corro trail", result(), monkeypatch)

        rows = {row.key: row for row in state.requirements}
        assert rows["discipline"].derived is True and rows["discipline"].source == "strava"
        assert rows["longest_session_band"].derived is False, (
            "a correction the athlete typed is showing as derived from their history.\n"
            "  Provenance is what lets the uncertainty layer say which half was measured."
        )
        assert all(row.label != row.key for row in state.requirements)


class TestTheUncertaintyLayerIsAValueAndNotATone:
    def test_every_flag_has_a_chip(self) -> None:
        assert set(st._FLAG_ES) == set(get_args(tools.UncertaintyFlag)), (
            "an uncertainty flag with no Spanish renders an English enum at a Colombian\n"
            "  reader, or raises mid-turn."
        )

    def test_every_confidence_has_a_phrase(self) -> None:
        assert set(st._CONFIDENCE_ES) == set(
            get_args(tools.UncertaintyVerdict.model_fields["confidence"].annotation)
        )

    def test_every_mode_has_a_sentence(self) -> None:
        assert set(st._MODE_ES) == {st.MODE_TRAINING, st.MODE_INTERVIEW}

    async def test_the_statement_on_screen_is_the_verdicts_own(self, monkeypatch) -> None:
        """`UncertaintyVerdict` regenerates its statement from the flags and three counts
        and compares — so copying it across is the one way to be sure the athlete reads a
        sentence nothing improvised."""
        state = fresh()
        thin = verdict(3)
        await drive(state, "corro trail", result(uncertainty=thin), monkeypatch)

        assert state.uncertainty_statement == thin.statement
        assert state.confidence == "none" and state.grounded is False
        assert st._FLAG_ES["thin"] in state.uncertainty_flags


class TestTheColdStartFallback:
    """A connected account with two activities is not a signal. The threshold is
    `tools.MIN_SAMPLE`, its reason is written where it is defined, and this state machine
    imports it rather than restating it."""

    def test_the_threshold_is_the_agents_own_and_not_a_second_copy(self) -> None:
        assert st.COLD_START_SAMPLE is tools.MIN_SAMPLE
        source = Path(inspect.getfile(st)).read_text()
        assert "MIN_SAMPLE" in source and f"= {tools.MIN_SAMPLE}\n" not in source, (
            "state.py hardcodes the cold-start threshold. Two copies drift, and the one on\n"
            "  screen would then describe a rule the agent is not applying."
        )

    def test_the_reason_is_stated_where_the_number_is(self) -> None:
        """A bare constant is a magic number; the number plus its reason is a decision."""
        declared = inspect.getsource(tools).split("MIN_SAMPLE = ")[0]
        assert "arithmetic on a rumour" in declared, (
            "tools.MIN_SAMPLE lost the sentence saying why eight. Without it the next\n"
            "  reader tunes it by feel."
        )
        assert str(tools.MIN_SAMPLE) in fresh().cold_start_reason

    @pytest.mark.parametrize("activities", (0, 1, 2, 7))
    async def test_a_thin_window_takes_the_interview_path(
        self, activities: int, monkeypatch
    ) -> None:
        state = fresh()
        await drive(
            state,
            "corro trail",
            result(advice=Advice(kind="recommend", items=(item(),)), uncertainty=verdict(activities)),
            monkeypatch,
        )

        assert state.cold_start is True
        assert state.advice_mode == st.MODE_INTERVIEW, (
            f"{activities} activities produced advice labelled as demonstrated training.\n"
            "  Huella's whole premise is that it says which one it read."
        )
        assert "historial" in state.mode_label

    async def test_an_unconnected_athlete_lands_in_exactly_the_same_place(
        self, monkeypatch
    ) -> None:
        """The Brújula-shaped fallback. "No Strava at all" and "Strava with two activities"
        are the same amount of demonstrated training: none."""
        state = fresh()
        await drive(
            state,
            "corro trail",
            result(
                advice=Advice(kind="recommend", items=(item(),)),
                uncertainty=tools.check_uncertainty(None, connected=False),
            ),
            monkeypatch,
        )

        assert state.cold_start is True and state.advice_mode == st.MODE_INTERVIEW
        assert st._FLAG_ES["not_connected"] in state.uncertainty_flags

    @pytest.mark.parametrize("activities", (8, 40))
    async def test_a_real_window_is_labelled_as_demonstrated(
        self, activities: int, monkeypatch
    ) -> None:
        state = fresh()
        turn = result(
            advice=Advice(kind="recommend", items=(item(),)), uncertainty=verdict(activities)
        )
        await drive(state, "corro trail", turn, monkeypatch)

        assert turn.uncertainty.grounded is True, "the fixture stopped being a real window"
        assert state.cold_start is False
        assert state.advice_mode == st.MODE_TRAINING and state.is_interview is False

    async def test_the_mode_moves_only_when_the_advice_does(self, monkeypatch) -> None:
        """A greeting after a recommendation must not relabel what is still on screen."""
        state = fresh()
        await drive(
            state,
            "corro trail",
            result(advice=Advice(kind="recommend", items=(item(),)), uncertainty=verdict(30)),
            monkeypatch,
        )
        assert state.advice_mode == st.MODE_TRAINING

        await drive(
            state,
            "gracias!",
            result(text="Hola —", stage="greeting", uncertainty=tools.check_uncertainty(None, connected=False)),
            monkeypatch,
        )

        assert state.advice_mode == st.MODE_TRAINING, (
            "a turn that produced no advice relabelled the advice the last one verified."
        )


class TestTheCredentialNeverReachesTheState:
    def test_no_state_var_is_typed_to_hold_one(self) -> None:
        """`tests/test_privacy_boundary.py` owns the AST scan. This asserts the positive
        half: what DID cross is a count, a label or an irreversible digest."""
        banned = {"TokenPair", "Activity", "ActivityWindow", "Athlete", "_Session"}
        annotations = {
            name: str(var._var_type) for name, var in st.State.vars.items()
        }
        offenders = [n for n, t in annotations.items() if any(b in t for b in banned)]
        assert not offenders, f"{offenders} are typed to hold a Strava payload"

    async def test_the_connection_crosses_as_counts_and_a_fingerprint(self) -> None:
        state = fresh()
        key = state.router.session.client_token
        privacy._ensure(key).pair = _pair()

        state._refresh_connection()

        assert state.connected is True
        assert state.token_fingerprint == privacy.fingerprint(_pair())
        assert len(state.token_fingerprint) == 16 and "access" not in state.token_fingerprint, (
            "the fingerprint is a salted digest of both halves at once, and it is the only\n"
            "  thing about a credential that may be stated out loud."
        )

    async def test_forgetting_drops_the_credential_and_the_conversation(self) -> None:
        state = fresh()
        key = state.router.session.client_token
        privacy._ensure(key).pair = _pair()
        st._conversation_for(key).question = "corro trail"
        state._refresh_connection()
        assert state.connected is True

        state.forget_me()

        assert privacy.is_connected(key) is False
        assert state.connected is False and state.token_fingerprint == ""
        assert key not in st._CONVERSATIONS
        assert state.strava_notice != ""

    @pytest.mark.parametrize(
        ("word", "notice", "error"),
        (
            (oauth.OK, True, False),
            (oauth.DENIED, True, False),
            (oauth.RATE_LIMITED, False, True),
            (oauth.BAD_STATE, False, True),
            ("<script>alert(1)</script>", False, False),
            ("", False, False),
        ),
    )
    def test_the_landing_word_is_the_only_thing_the_callback_hands_over(
        self, word: str, notice: bool, error: bool
    ) -> None:
        """Everything else about the exchange happened server-side. A word out of a closed
        vocabulary is what crosses, and an invented one is ignored rather than rendered.

        The router is built the way `reflex_base/event/processor/base_state_processor.py:
        353-358` builds it before every handler, off the `router_data` the compiled client
        sends with every event (`.web/utils/state.js:387-414`, which reads the live search
        string). That is what puts `?strava=` in front of `on_page_load` at all.
        """
        state = _routed(f"/?strava={word}" if word else "/", {"strava": word} if word else {})
        state._read_landing()

        assert bool(state.strava_notice) is notice
        assert bool(state.strava_error) is error
        assert not word or word not in state.strava_notice + state.strava_error, (
            "the landing word was pasted into the sentence rather than looked up. It comes "
            "off a URL, and a URL is something anybody can write."
        )

    def test_a_connect_click_on_a_locked_app_starts_no_flow(self, monkeypatch) -> None:
        monkeypatch.setattr(st, "GATE_ON", True)
        state = fresh()

        assert state.connect_strava() is None
        assert oauth.pending_count() == 0, (
            "conditional rendering is not a guard — the event is callable over the wire\n"
            "  whatever is on screen."
        )


class TestThePasswordGate:
    def test_the_gate_is_off_when_no_password_is_configured(self) -> None:
        assert st.GATE_ON is False and st._GATE_DIGEST == ""

    def test_unlocked_defaults_to_a_literal_false(self) -> None:
        """A state var's default is compiled INTO the frontend bundle, and the image is
        built with no password set — so `not GATE_ON` would bake in as True and serve the
        unlocked shell to any browser whose websocket never connected."""
        assert fresh().unlocked is False
        assert "unlocked: bool = False" in inspect.getsource(st.State)

    def test_the_two_apps_do_not_share_a_digest_or_a_cookie(self) -> None:
        """One VPS, two apps, one browser. A shared salt would make either password open
        both doors, and a shared cookie name would make either app's unlock leak into the
        other's."""
        from brujula import state as bj

        assert st._digest("misma") != bj._digest("misma")
        assert (
            st.State.get_fields()["gate_key"].default.name
            != bj.State.get_fields()["gate_key"].default.name
        )

    async def test_the_right_password_unlocks_and_the_wrong_one_does_not(
        self, monkeypatch
    ) -> None:
        """Accented on purpose. `hmac.compare_digest` raises `TypeError: comparing strings
        with non-ASCII characters is not supported`, so comparing the passwords themselves
        turns the likeliest password for a Spanish app into a 500 rather than a refusal."""
        password = "una huella no miente"
        monkeypatch.setattr(st, "GATE_ON", True)
        monkeypatch.setattr(st, "GATE_PASSWORD", password)
        monkeypatch.setattr(st, "_GATE_DIGEST", st._digest(password))
        monkeypatch.setattr(st, "_GATE_DELAY", 0)
        unlock = getattr(st.State.unlock, "fn", st.State.unlock)
        state = fresh()

        async for _ in unlock(state, {"password": "no"}):
            pass
        assert state.unlocked is False and state.gate_error != "" and state.gate_key == ""

        async for _ in unlock(state, {"password": password}):
            pass
        assert state.unlocked is True
        assert state.gate_key == st._GATE_DIGEST and password not in state.gate_key

    async def test_an_accented_password_is_refused_rather_than_raising(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(st, "GATE_ON", True)
        monkeypatch.setattr(st, "_GATE_DIGEST", st._digest("correcta"))
        monkeypatch.setattr(st, "_GATE_DELAY", 0)
        unlock = getattr(st.State.unlock, "fn", st.State.unlock)
        state = fresh()

        async for _ in unlock(state, {"password": "contraseña"}):
            pass

        assert state.unlocked is False and state.gate_error != ""

    def test_every_spending_handler_rechecks_the_gate(self) -> None:
        """Conditional rendering is not a guard. `GATE_ON and not self.unlocked` — a bare
        `not self.unlocked` silently disables the headless verify scripts, which drive
        these handlers with no browser and so never run `on_page_load`."""
        for name in ("send_message", "send_example", "connect_strava", "disconnect_strava", "forget_me"):
            assert "GATE_ON and not self.unlocked" in source_of(getattr(st.State, name)), name

    async def test_a_locked_session_never_reaches_the_agent(self, monkeypatch) -> None:
        called: list[str] = []

        async def _run(message: str, session: Any) -> loop.TurnResult:
            called.append(message)
            return result()

        monkeypatch.setattr(st, "run_turn", _run)
        monkeypatch.setattr(st, "GATE_ON", True)
        state = fresh()

        async for _ in state.send_message({"message": "corro trail"}):
            pass

        assert called == [] and state.messages == []

    def test_clearing_does_not_relock_the_app_or_disconnect_strava(self) -> None:
        state = fresh()
        key = state.router.session.client_token
        state.unlocked = True
        privacy._ensure(key).pair = _pair()

        state.clear()

        assert state.unlocked is True, "starting over resets the conversation, not admission"
        assert privacy.is_connected(key) is True, (
            "starting a conversation again made the athlete re-authorise Strava.\n"
            "  Disconnecting is its own button."
        )

    def test_the_gate_opens_itself_when_no_password_is_set(self) -> None:
        assert "if not GATE_ON:" in source_of(st.State.on_page_load)


class TestOneTurnAtATime:
    async def test_a_second_message_while_thinking_is_refused(self, monkeypatch) -> None:
        state = fresh()
        state.is_thinking = True
        await drive(state, "corro trail", result(), monkeypatch)

        assert state.messages == []

    async def test_an_empty_message_spends_nothing(self, monkeypatch) -> None:
        state = fresh()
        await drive(state, "   ", result(), monkeypatch)

        assert state.messages == [] and state.trace == []

    async def test_a_blank_turn_is_reported_rather_than_shown_as_an_empty_bubble(
        self, monkeypatch
    ) -> None:
        state = fresh()
        await drive(state, "corro trail", result(text="   ", stage="recommend"), monkeypatch)

        assert state.messages[-1].content.strip() != ""
        assert state.error, "a turn that produced no words is a defect, and has to say so"
        assert "turn.blank" in [e.event for e in trace.events("error")]

    async def test_a_turn_that_raises_says_so_without_a_stack_trace(self, monkeypatch) -> None:
        async def _run(message: str, session: Any) -> loop.TurnResult:
            raise RuntimeError("upstream exploded at 0x7f")

        monkeypatch.setattr(st, "run_turn", _run)
        state = fresh()
        async for _ in state.send_message({"message": "corro trail"}):
            pass

        assert state.error and "0x7f" not in state.error
        assert state.is_thinking is False and state.status == ""
        assert [e.event for e in trace.events("error") if e.event == "turn.error"] == ["turn.error"]


def _pair():
    from huella.strava.models import TokenPair

    return TokenPair(
        access_token="access-token-for-a-test",
        refresh_token="refresh-token-for-a-test",
        expires_at=4_000_000_000,
        scope="read,activity:read_all",
        athlete_id=1,
    )
