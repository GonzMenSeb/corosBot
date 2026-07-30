"""Brújula's Reflex layer: what crosses the wire, and what must never.

Three questions run through every test here.

**Does the browser get facts or prose?** Cards, prices, refusals and audit rows are
rebuilt from `Advice` and `EvidenceBundle` on the server, so the tests assert against
typed values and never against a sentence a model wrote.

**Does the turn's trace reach the session that produced it?** `bind_sink()` before
`asyncio.create_task()` is the whole mechanism, and a bundle that sees no events
accepts nothing — so the ordering is asserted in the source *and* driven behaviourally.

**Does what is on screen still say the true thing when an upstream refuses?** The
storefront's 429 latches and is never retried, so a caption promising another attempt
would be a lie about what the process is doing.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any, get_args

from brujula import state as st
from brujula.agent import loop
from coros_core import devices, evidence, trace
from coros_core.evidence import Check, EvidenceBundle
from coros_core.models import Advice, AdviceItem, RequirementKey

REPO = Path(__file__).resolve().parent.parent
FACTS = 'AGENTS.md "load-bearing facts"'

PACE_4_MINOR = 109_900_000


def fresh() -> Any:
    return st.State(_reflex_internal_init=True)


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
            "satisfies": ("discipline", "budget_minor"),
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


def result(**over: Any) -> loop.TurnResult:
    return loop.TurnResult(**{"text": "Yo iría con el PACE 4.", "stage": "recommend", **over})


async def drive(state: Any, text: str, turn: loop.TurnResult, monkeypatch) -> None:
    """One turn through the real handler with the agent replaced. `send_message` is the
    only way in for anything a person types, so the tests take that door too."""

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
    sources += [*(REPO / "apps" / "brujula" / "brujula").rglob("*.py")]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name != "emit" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    return names


def source_of(handler: Any) -> str:
    return inspect.getsource(getattr(handler, "fn", handler))


class TestTheConversationDoesNotLiveInAStateVar:
    """The plan's reason for the module-level map was that Reflex cannot hold a
    dataclass. It can — so the map has to be justified by what a state var would COST,
    or the next reader deletes it as superstition."""

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
            "  why the conversation lives in state._SESSIONS: not a Reflex limitation, a\n"
            "  cost — every mutation the loop makes would be broadcast, and the DISK state\n"
            "  manager pickles the same bytes into .states/."
        )

    def test_each_browser_session_gets_its_own_conversation(self) -> None:
        first = st._session_for("token-a")
        second = st._session_for("token-b")

        first.question = "corro trail"
        assert second.question == "", "two visitors must never share a conversation"

    def test_the_same_token_resumes_the_same_conversation(self) -> None:
        st._session_for("token-c").question = "busco correa"
        assert st._session_for("token-c").question == "busco correa", (
            "requirements, answers and the finished stages live here — a new session per\n"
            "  turn would re-interview the person on every message"
        )

    def test_clearing_starts_a_new_conversation(self) -> None:
        st._session_for("token-d").question = "corro trail"
        st._reset_session("token-d")
        assert st._session_for("token-d").question == ""

    def test_the_map_is_bounded_and_forgets_the_oldest(self) -> None:
        """A public URL served for weeks. An unbounded map is one transcript per browser
        that is never freed."""
        for n in range(st.MAX_SESSIONS + 5):
            st._session_for(f"token-{n}")

        assert len(st._SESSIONS) == st.MAX_SESSIONS
        assert "token-0" not in st._SESSIONS
        assert f"token-{st.MAX_SESSIONS + 4}" in st._SESSIONS


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
        """The behavioural half: an event emitted from inside the task, not from the
        handler's own frame, has to land on this state's trace."""

        async def _run(message: str, session: Any) -> loop.TurnResult:
            trace.emit("gate.verdict", {"intent": "advice"})
            trace.emit("guardrail.stock", {"checked": 1}, "guardrail")
            return result()

        monkeypatch.setattr(st, "run_turn", _run)
        state = fresh()
        async for _ in state.send_message({"message": "corro trail"}):
            pass

        seen = [row.event for row in state.trace]
        assert "gate.verdict" in seen and "guardrail.stock" in seen
        assert [row.level for row in state.trace if row.event == "guardrail.stock"] == ["guardrail"]

    async def test_the_sink_is_released_when_the_turn_ends(self, monkeypatch) -> None:
        """A sink still bound after the handler returns routes the NEXT session's events
        into a list nobody reads."""
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
            "requirements.built",
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

    def test_a_latched_limiter_does_not_claim_to_be_retrying(self) -> None:
        """COROS's 429 latches: it is never retried and never polled, because our own
        retries are what keep the door shut. A caption promising another attempt would
        describe a request this process is deliberately not making."""
        state = fresh()
        state._drain([ev("catalog.rate_limited", level="error")])

        assert state.status != st._RETRYING
        for promise in ("otra vez", "reintent", "de nuevo"):
            assert promise not in state.status.lower(), f"{state.status!r} promises a retry"

    def test_still_trying_reads_differently_from_carrying_on(self) -> None:
        state = fresh()
        state._drain([ev("catalog.retry")])
        retrying = state.status

        state._drain([ev("catalog.unavailable", level="error")])

        assert state.status != retrying, (
            "'still trying' and 'gave up on that request and carried on' are different\n"
            "  facts and a person watching a spinner is entitled to both"
        )


class TestFullPayloadsStayOnTheServer:
    def test_the_raw_trace_is_a_backend_only_var(self) -> None:
        assert "_raw_trace" in st.State.backend_vars
        assert "_raw_trace" not in st.State.vars, (
            "the panel renders a clamped summary and `trace` crosses the wire on every\n"
            "  drain, so full payloads must not travel with it"
        )

    def test_the_wire_row_is_clamped_and_the_stored_payload_is_not(self) -> None:
        state = fresh()
        state._drain([ev("retrieval.done", {"summary": "x" * 900})])

        assert len(state.trace[0].summary) <= st.SUMMARY_CHARS
        assert len(state._raw_trace[0]["payload"]["summary"]) == 900

    def test_the_run_report_is_real_json_not_a_python_repr(self) -> None:
        """Reflex hands state containers back wrapped in `MutableProxy`. `isinstance`
        sees through it and a compact `json.dumps` does not — the C encoder's exact type
        check misses the wrapper and falls through to `default=`, so a whole payload
        serialises as a Python repr inside a JSON string."""
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

        assert state.cards == [], (
            "`Advice` refuses to be built with products on a non-recommend kind. A card\n"
            "  left on screen beside 'no compres nada' is exactly the disagreement the\n"
            "  evidence bundle blocks on"
        )
        assert state.is_refusal is True

    async def test_a_greeting_does_not_retract_a_standing_recommendation(
        self, monkeypatch
    ) -> None:
        state = fresh()
        await drive(
            state, "corro trail", result(advice=Advice(kind="recommend", items=(item(),))), monkeypatch
        )

        await drive(state, "gracias!", result(text="Hola —", stage="greeting"), monkeypatch)

        assert [card.title for card in state.cards] == ["COROS PACE 4"], (
            "a turn that produced no advice at all — a greeting, an off-topic redirect, an\n"
            "  injection — must not wipe the products the previous turn verified"
        )

    async def test_an_absent_device_is_named_from_the_registry(self, monkeypatch) -> None:
        state = fresh()
        advice = Advice(kind="not_sold_locally", unavailable_devices=("pace-3",))
        await drive(state, "quiero el PACE 3", result(advice=advice), monkeypatch)

        assert state.unavailable == [devices.require("pace-3").name], (
            "the slug is ours, the name is COROS's. A person reads the name, and it comes\n"
            "  from devices.py rather than from anything the model wrote"
        )
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

    async def test_a_check_row_carries_spanish_and_the_bundles_own_outcome(
        self, monkeypatch
    ) -> None:
        state = fresh()
        await drive(state, "corro trail", result(evidence=bundle()), monkeypatch)

        stock = next(row for row in state.checks if row.name == "stock")
        assert stock.outcome == "pass" and stock.label and stock.label != "stock"
        assert state.checks_summary == "5/5"

    def test_every_requirement_key_has_spanish(self) -> None:
        assert set(st._REQUIREMENT_ES) == set(get_args(RequirementKey)), (
            "a card lists what a product satisfies. A key with no label renders the\n"
            "  English enum at a Colombian reader, or raises mid-turn."
        )

    def test_every_declared_check_has_spanish(self) -> None:
        declared = {check.name for check in evidence._DECLARED}
        assert set(st._CHECK_ES) == declared, (
            "the audit rail names every check the bundle can declare. AGENTS.md's\n"
            "  maintenance contract moves evidence.py and this map together."
        )


class TestThePasswordGate:
    """The apps are on a public URL and what the gate protects is the Gemini quota and
    COROS's rate limiter — a storefront lockout is minutes long and hits everybody."""

    def test_the_gate_is_off_when_no_password_is_configured(self) -> None:
        assert st.GATE_ON is False and st._GATE_DIGEST == "", (
            "an unset BRUJULA_PASSWORD must leave local dev and this suite untouched"
        )

    def test_unlocked_defaults_to_a_literal_false(self) -> None:
        """A state var's default is compiled INTO the frontend bundle, and the image is
        built with no password set — so `not GATE_ON` would bake in as True and serve
        the unlocked shell to any browser whose websocket never connected."""
        assert fresh().unlocked is False
        assert "unlocked: bool = False" in inspect.getsource(st.State)

    async def test_the_right_password_unlocks_and_the_wrong_one_does_not(
        self, monkeypatch
    ) -> None:
        """Accented on purpose. `hmac.compare_digest` raises `TypeError: comparing strings
        with non-ASCII characters is not supported`, so comparing the passwords themselves
        turns the likeliest password for a Spanish app into a 500 rather than a refusal —
        which is why `_digest()` is what gets compared."""
        password = "una brújula no miente"
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
        assert state.gate_key == st._GATE_DIGEST and password not in state.gate_key, (
            "the cookie carries a digest of the password, never the password"
        )

    def test_every_spending_handler_rechecks_the_gate(self) -> None:
        """Conditional rendering is not a guard: the events are callable over the wire
        whatever is on screen. The guard must be `GATE_ON and not self.unlocked` — a
        bare `not self.unlocked` silently disables the headless verify scripts, which
        drive these handlers with no browser and so never run `on_page_load`."""
        for name in ("send_message", "send_example"):
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

    def test_clearing_does_not_relock_the_app(self) -> None:
        state = fresh()
        state.unlocked = True
        state.clear()
        assert state.unlocked is True, "starting over resets the conversation, not admission"

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
