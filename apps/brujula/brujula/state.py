"""Brújula's Reflex state: one class, and the two orderings that make it work.

**`bind_sink()` runs BEFORE `asyncio.create_task()`.** contextvars are copied at
task-creation time, so that ordering is the entire mechanism routing the agent loop's
`emit()` into this session's trace instead of the process-wide ring. Bound after, the
panel loses the verdicts — and `evidence.build` reads the same events, so a bundle that
saw none accepts nothing and the person is told a recommendation could not be verified
when in fact it was.

**The conversation lives in a module-level map, not in a state var.** Reflex 0.9.7 does
hold a dataclass — it wraps one in a `MutableProxy` and serialises it — so this is not a
limitation being worked around, it is a cost being refused: every mutation the loop makes
to the transcript, the requirements and the advice would be broadcast to the browser, and
the DISK state manager pickles the same bytes into `.states/`. Verified 30 jul 2026 and
pinned by `tests/test_brujula_state.py`.

Everything the browser reads is rebuilt here from typed values — `Advice`,
`EvidenceBundle`, `devices.py` — and never from the model's prose. Prices are formatted
on this side of the wire because `minor_to_display` is a Python function and cannot be
called on a Var inside a component.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from collections import OrderedDict
from typing import Any

import reflex as rx
from pydantic import BaseModel, Field

from brujula.agent import prompts
from brujula.agent.loop import ConversationSession, TurnResult, run_turn
from coros_core import devices, gemini, money
from coros_core.evidence import EvidenceBundle
from coros_core.models import Advice, AdviceItem
from coros_core.trace import TraceEvent, bind_sink, emit

# Before every module-level env read below. `coros_core.gemini` owns the two roots `.env`
# can live at — a checkout and the flattened container — and loading is idempotent. A
# password read before the file was parsed is a gate that silently stays open.
gemini.load_env()

GATE_PASSWORD = os.environ.get("BRUJULA_PASSWORD", "")
GATE_ON = bool(GATE_PASSWORD)


def _digest(password: str) -> str:
    """What the cookie carries, and what a typed password is compared as.

    `hmac.compare_digest` raises `TypeError: comparing strings with non-ASCII characters
    is not supported`, so comparing the passwords themselves turns an accented one — in a
    Spanish app, the likely kind — into a 500 instead of a refusal. Hex digests are ASCII
    and equal-length, which is what the constant-time comparison wants anyway.
    """
    return hashlib.sha256(b"brujula.gate.v1:" + password.encode()).hexdigest()


# What a returning browser presents instead of retyping. Producing it needs the password,
# so restoring `unlocked` from the cookie is a real check rather than trust in a flag.
_GATE_DIGEST = _digest(GATE_PASSWORD) if GATE_ON else ""
# A short shared password's only real defence is making each guess cost something.
_GATE_DELAY = 0.6

_POLL_INTERVAL = 0.15

SUMMARY_CHARS = 300
VALUE_CHARS = 120

# Bounded on purpose. These apps are hosted for weeks, not demoed for an evening, and an
# unbounded map is one conversation transcript per browser that is never freed. Losing the
# oldest costs the person an interview they already answered; it can never fabricate one.
MAX_SESSIONS = 200

OPENING_STATUS = "Leyendo el catálogo de COROS…"

_BROKE_ERROR = (
    "Ese turno se rompió antes de terminar. El registro de la derecha tiene el último "
    "paso que corrió."
)


# ── the conversation, kept out of both the wire and the pickle ─────────────────

_SESSIONS: OrderedDict[str, ConversationSession] = OrderedDict()


def _session_for(token: str) -> ConversationSession:
    session = _SESSIONS.pop(token, None)
    if session is None:
        session = ConversationSession()
    _SESSIONS[token] = session
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.popitem(last=False)
        emit("session.evicted", {"sessions": len(_SESSIONS)})
    return session


def _reset_session(token: str) -> None:
    _SESSIONS.pop(token, None)


# ── what the browser is allowed to see ────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class TraceRow(BaseModel):
    seq: int
    event: str
    level: str
    summary: str


class CheckRow(BaseModel):
    """One row of the audit rail. `outcome` is the bundle's own word — "pass", "fail" or
    "not_run" — and is never softened: a check that did not run says so."""

    name: str
    label: str
    outcome: str
    confidence: str
    detail: str = ""


class ProductCard(BaseModel):
    """Render-ready view of an `AdviceItem`, built from the feed-backed item and never
    from prose. `image_url` is `""` rather than None when COROS ships no photo: a real
    in-stock product without one is still buyable, and `rx.cond` needs a value to test."""

    product_id: str
    title: str
    product_url: str
    image_url: str = ""
    price_display: str
    rationale: str = ""
    satisfies: list[str] = Field(default_factory=list)


# The card lists what a product satisfies, so every `RequirementKey` needs a word a
# Colombian reader recognises. Pinned by tests/test_brujula_state.py.
_REQUIREMENT_ES: dict[str, str] = {
    "discipline": "disciplina",
    "device": "dispositivo",
    "case_mm": "tamaño de caja",
    "strap_mm": "ancho de correa",
    "budget_minor": "presupuesto",
    "battery_hours": "batería",
    "water_resistance": "resistencia al agua",
    "terrain": "terreno",
    "sport_mix": "mezcla de deportes",
    "weekly_hours_band": "horas por semana",
    "longest_session_band": "salida más larga",
    "elevation_band": "desnivel",
    "consistency_band": "constancia",
}

# A bundle's own prose is English — it is an engineering artifact read in a PR. The rail
# is read by a person, so the check names are translated here. `loop._CHECKS_ES` says the
# same things mid-sentence, for when a check is what blocked an answer.
_CHECK_ES: dict[str, str] = {
    "provenance": "cada dato sale del catálogo",
    "stock": "está disponible",
    "budget": "la cuenta contra tu presupuesto",
    "local_availability": "se vende en Colombia",
    "buy_nothing": "«no compres nada» seguía siendo posible",
    "prose": "nada afirmado sin respaldo",
}


# ── what the caption says while a turn runs ───────────────────────────────────

# A backoff is the one wait that must not read as a hang: `catalog._get` can sit on a
# retry for seconds. Driven off the drained trace rather than off `run_turn`'s return,
# because it has to land WHILE the turn is still running.
_RETRYING = "COROS no respondió — vuelvo a intentarlo…"
_MODEL_BUSY = "El modelo está saturado — vuelvo a intentarlo…"
# Never a promise of another attempt: COROS's 429 latches and is deliberately not
# retried and not polled, because our own retries are what keep the door shut.
_LATCHED = "COROS limitó las consultas — sigo con lo que alcancé a leer…"

_THROTTLE_STATUS: dict[str, str] = {
    "catalog.retry": _RETRYING,
    "model.retry": _MODEL_BUSY,
    "catalog.rate_limited": _LATCHED,
    "catalog.unavailable": _LATCHED,
    "ucp.rate_limited": _LATCHED,
}

# Keyed on what the LIVE pipeline emits, which is the mistake DecaBot made and paid for:
# a table keyed on a second vocabulary left the caption frozen through the longest stretch
# of the turn. `test_every_caption_keys_on_an_event_something_actually_emits` covers it.
_STAGE_STATUS: dict[str, str] = {
    "gate.verdict": "Entendiendo qué me pediste…",
    "turn.snapshot": "Leyendo el catálogo completo de COROS…",
    "questions.asked": "Viendo qué me falta saber…",
    "requirements.built": "Anotando tus requisitos…",
    "tool.list_collections": "Recorriendo el catálogo…",
    "tool.get_collection_products": "Recorriendo el catálogo…",
    "tool.search_products": "Buscando en el catálogo…",
    "guardrail.device_compat": "Comprobando qué correa le sirve…",
    "retrieval.done": "Comparando lo que encontré…",
    "selection.built": "Eligiendo — o descartando…",
    "guardrail.provenance": "Comprobando cada dato contra el catálogo…",
    "guardrail.stock": "Comprobando disponibilidad…",
    "guardrail.budget": "Haciendo la cuenta contra tu presupuesto…",
    "guardrail.local_availability": "Comprobando que se venda en Colombia…",
    "guardrail.prose": "Revisando que no afirme nada sin respaldo…",
    "evidence.bundle": "Verificando antes de responderte…",
}


def plain(value: Any) -> Any:
    """Rebuild real containers out of Reflex's `MutableProxy` wrappers.

    `isinstance` sees through the proxy; `json.dumps` does not — the C encoder does an
    exact type check, misses the wrapper and falls through to `default=`, so a whole
    payload serialises as a Python repr inside a JSON string. Verified 30 jul 2026, with
    one wrinkle worth knowing: passing `indent=` selects the pure-Python encoder, which
    goes through `isinstance` and happens to survive. A compact dump does not.
    """
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return str(value)
    return str(value)


def _summarise(payload: dict[str, Any]) -> str:
    """Trace payloads nest arbitrarily and come from every layer. Flatten to one scalar
    line so the panel never walks an unknown shape. No `plain()` here: a `TraceEvent`
    hands back a freshly decoded object, so nothing on this path is ever a proxy."""
    parts = []
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            text = str(value)
        else:
            text = json.dumps(value, default=str, ensure_ascii=False)
        parts.append(f"{key}={text[:VALUE_CHARS]}")
    return "  ".join(parts)[:SUMMARY_CHARS]


def _device_name(slug: str) -> str:
    device = devices.get(slug)
    return device.name if device is not None else slug


def _card(item: AdviceItem) -> ProductCard:
    return ProductCard(
        product_id=item.product_id,
        title=item.title,
        product_url=item.product_url,
        image_url=item.image_url or "",
        price_display=money.minor_to_display(item.price_minor),
        rationale=item.rationale,
        satisfies=[_REQUIREMENT_ES[key] for key in item.satisfies],
    )


class State(rx.State):
    messages: list[ChatMessage] = []
    draft: str = ""

    cards: list[ProductCard] = []
    advice_kind: str = ""
    caveats: list[str] = []
    # Device NAMES, resolved from the registry. The slug is ours; the name is COROS's.
    unavailable: list[str] = []
    questions: list[str] = []
    total_minor: int = 0

    checks: list[CheckRow] = []
    evidence_accepted: bool = False
    blocking: list[str] = []

    trace: list[TraceRow] = []
    show_trace: bool = True

    # `trace` crosses the wire on every drain and carries `_summarise`'s clamped line, so
    # the whole payloads live in a BACKEND-ONLY var (leading underscore) that Reflex never
    # serialises to any browser. `_bundle_text` is the same trick.
    _raw_trace: list[dict[str, Any]] = []
    _bundle_text: str = ""

    is_thinking: bool = False
    status: str = ""
    # Styling only — `status` carries the words. Separate so the spinner can change
    # character without the UI string-matching a message.
    throttled: bool = False
    error: str = ""

    # False unconditionally, NOT `not GATE_ON`. A state var's default is compiled INTO the
    # frontend bundle and the image is built with no password set, so the derived form
    # bakes in as True and serves the unlocked shell to a browser whose websocket never
    # connected. `on_page_load` is what opens the gate when it is off.
    unlocked: bool = False
    gate_error: str = ""
    gate_busy: bool = False
    gate_reveal: bool = False
    gate_key: str = rx.Cookie(name="brujula_gate", max_age=60 * 60 * 24 * 30, same_site="lax")

    @rx.var
    def gate_on(self) -> bool:
        return GATE_ON

    @rx.var
    def has_cards(self) -> bool:
        return len(self.cards) > 0

    @rx.var
    def item_count(self) -> int:
        return len(self.cards)

    @rx.var
    def total_display(self) -> str:
        return money.minor_to_display(self.total_minor)

    @rx.var
    def is_refusal(self) -> bool:
        """Every kind other than a recommendation. The four honest refusals get their own
        treatment on screen rather than reading as a paragraph that failed to recommend."""
        return self.advice_kind not in ("", "recommend")

    @rx.var
    def has_questions(self) -> bool:
        return len(self.questions) > 0

    @rx.var
    def has_evidence(self) -> bool:
        return len(self.checks) > 0

    @rx.var
    def blocked(self) -> bool:
        return len(self.checks) > 0 and not self.evidence_accepted

    @rx.var
    def checks_summary(self) -> str:
        return f"{sum(1 for c in self.checks if c.outcome == 'pass')}/{len(self.checks)}"

    def _drain(self, sink: list[TraceEvent]) -> bool:
        if not sink:
            return False
        for event in sink:
            self.trace.append(
                TraceRow(
                    seq=event.seq,
                    event=event.event,
                    level=event.level,
                    summary=_summarise(event.payload),
                )
            )
            self._raw_trace.append(event.as_dict())
            throttle = _THROTTLE_STATUS.get(event.event)
            if throttle is not None:
                self.throttled, self.status = True, throttle
            elif not self.throttled:
                # A throttle outranks a stage: being rate-limited is the more important
                # thing to be saying, and a later stage must not take the caption back.
                stage = _STAGE_STATUS.get(event.event)
                if stage is not None:
                    self.status = stage
        sink.clear()
        return True

    def _apply_advice(self, advice: Advice) -> None:
        self.advice_kind = advice.kind
        self.caveats = list(advice.caveats)
        self.unavailable = [_device_name(slug) for slug in advice.unavailable_devices]
        # The cards go with the kind. `Advice` refuses to be built with products on any
        # kind but `recommend`, and a card left on screen beside "no compres nada" is the
        # disagreement the evidence bundle exists to block.
        self.cards = [_card(item) for item in advice.items]
        self.total_minor = advice.total_minor

    def _apply_evidence(self, bundle: EvidenceBundle) -> None:
        self.evidence_accepted = bundle.accepted
        self.blocking = list(bundle.blocking)
        self.checks = [
            CheckRow(
                name=check.name,
                label=_CHECK_ES[check.name],
                outcome=check.outcome,
                confidence=check.confidence,
                detail=check.detail,
            )
            for check in bundle.checks
        ]
        self._bundle_text = bundle.render()

    def _apply(self, result: TurnResult) -> None:
        text = result.text.strip()
        if not text:
            # An empty bubble is how a turn that produced nothing goes unnoticed. Every
            # path through the loop sets text, so arriving here is a defect, not a case.
            emit("turn.blank", {"stage": result.stage}, "error")
            self.error = _BROKE_ERROR
            text = prompts.BROKE_TEMPLATE
        self.messages.append(ChatMessage(role="assistant", content=text))
        self.questions = list(result.questions)
        # Only when the turn produced one. A greeting, a redirect or a blocked injection
        # produces no advice at all and must not retract what the last turn verified.
        if result.advice is not None:
            self._apply_advice(result.advice)
        if result.evidence is not None:
            self._apply_evidence(result.evidence)

    def run_report(self) -> str:
        """The whole run as text: what was said, every payload in full, and the bundle.

        One compact JSON object per event, the same shape `trace.py` keeps at rest, so a
        reviewer can grep it. `plain()` first — see its docstring for what a compact
        `json.dumps` does to a state container."""
        lines = [
            f"# Brújula — {len(self.messages)} mensajes, {len(self._raw_trace)} eventos",
            "",
            *(f"{message.role}: {message.content}" for message in self.messages),
            "",
            "## trace",
            *(json.dumps(plain(event), ensure_ascii=False) for event in self._raw_trace),
        ]
        if self._bundle_text:
            lines += ["", self._bundle_text]
        return "\n".join(lines)

    @rx.event
    def toggle_trace(self):
        self.show_trace = not self.show_trace

    @rx.event
    def toggle_reveal(self):
        self.gate_reveal = not self.gate_reveal

    @rx.event
    def clear(self):
        """Start over: a new conversation, the same admission."""
        _reset_session(self.router.session.client_token)
        self.messages = []
        self.draft = ""
        self.cards = []
        self.advice_kind = ""
        self.caveats = []
        self.unavailable = []
        self.questions = []
        self.total_minor = 0
        self.checks = []
        self.evidence_accepted = False
        self.blocking = []
        self.trace = []
        self._raw_trace = []
        self._bundle_text = ""
        self.is_thinking = False
        self.status = ""
        self.throttled = False
        self.error = ""

    @rx.event
    async def unlock(self, form_data: dict[str, Any]):
        if not GATE_ON or self.unlocked or self.gate_busy:
            return

        self.gate_busy = True
        self.gate_error = ""
        yield

        await asyncio.sleep(_GATE_DELAY)
        # Bound so these land in THIS session's trace. Without a sink they reach only the
        # process ring, and the panel would be missing a verdict that did fire.
        sink: list[TraceEvent] = []
        bind_sink(sink)
        try:
            typed = _digest((form_data.get("password") or "").strip())
            if hmac.compare_digest(typed, _GATE_DIGEST):
                self.unlocked = True
                self.gate_key = _GATE_DIGEST
                emit("gate.unlocked", {}, "guardrail")
            else:
                self.gate_error = "Esa no es la contraseña."
                emit("gate.refused", {}, "guardrail")
            self._drain(sink)
        finally:
            bind_sink(None)

        self.gate_busy = False
        yield

    @rx.event
    async def send_example(self, text: str):
        """The empty-state chips, through the same handler a typed message takes."""
        if GATE_ON and not self.unlocked:
            return
        async for _ in self.send_message({"message": text}):
            yield

    @rx.event
    async def send_message(self, form_data: dict[str, Any]):
        text = (form_data.get("message") or self.draft or "").strip()
        # Conditional rendering is not a guard — the event is callable over the wire
        # whatever is on screen. `GATE_ON and` is not redundant either: the headless
        # verify scripts never run `on_page_load`, so a bare check disables them silently.
        if not text or self.is_thinking or (GATE_ON and not self.unlocked):
            return

        self.draft = ""
        self.error = ""
        self.questions = []
        self.messages.append(ChatMessage(role="user", content=text))
        self.is_thinking = True
        self.status = OPENING_STATUS
        self.throttled = False
        yield

        # The trace accumulates across turns rather than resetting: the gate verdict and
        # the guardrails of turn one are what a reviewer is reading for. `clear()` resets.
        sink: list[TraceEvent] = []
        # BEFORE create_task, and that is the whole point — see the module docstring.
        bind_sink(sink)
        try:
            # The turn number and the length, never the words: an evidence bundle pastes
            # trace payloads back into a model's context.
            emit("turn.start", {"turn": len(self.messages) // 2 + 1, "chars": len(text)})
            self._drain(sink)
            yield

            session = _session_for(self.router.session.client_token)
            task = asyncio.create_task(run_turn(text, session))
            while not task.done():
                await asyncio.sleep(_POLL_INTERVAL)
                if self._drain(sink):
                    yield

            self._apply(await task)
            self._drain(sink)
        except Exception as exc:
            # The class name goes to the trace, which is on screen beside this and is
            # where detail belongs. A stack trace in the chat teaches nobody anything.
            emit("turn.error", {"error": type(exc).__name__}, "error")
            self._drain(sink)
            self.error = _BROKE_ERROR
            self.messages.append(ChatMessage(role="assistant", content=prompts.BROKE_TEMPLATE))
        finally:
            bind_sink(None)
            self.is_thinking = False
            self.status = ""
            self.throttled = False
        yield

    @rx.event
    def on_page_load(self):
        """Something has to open the gate, because `unlocked` defaults to a hard False."""
        if not GATE_ON:
            self.unlocked = True
        elif not self.unlocked and hmac.compare_digest(self.gate_key, _GATE_DIGEST):
            self.unlocked = True
