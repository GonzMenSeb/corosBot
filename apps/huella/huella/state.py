"""Huella's Reflex state: one class, and the three things it is not allowed to hold.

**No credential, no payload, no window.** `privacy.py` owns those and this module never
names them. What crosses into state is `privacy.Connection` and `privacy.Sync` — counts, a
salted fingerprint and sealed `Requirement`s — flattened here into scalars, because
`StateManagerDisk` pickles every state var, backend-only `_`-prefixed ones included, and a
field typed to hold a `TokenPair` would be a refresh token in a file on the host.
`tests/test_privacy_boundary.py::test_tokens_are_never_written_to_disk` scans this file for
exactly that and was vacuous until it existed.

**The conversation lives in a module-level map, not in a state var**, for the same reason
Brújula's does: Reflex 0.9.7 *does* hold a dataclass — it wraps one in a `MutableProxy` and
serialises it — so this is a cost being refused, not a limitation being worked around. Every
mutation the loop makes to the transcript, the preferences and the advice would be broadcast
to the browser and pickled to `.states/`. The map is `_CONVERSATIONS`, deliberately not
`_SESSIONS`: that name belongs to `privacy.py`, where the credentials are, and two maps
called the same thing in one app is how one of them ends up holding the other's contents.

**`bind_sink()` runs BEFORE `asyncio.create_task()`.** contextvars are copied at
task-creation time, so that ordering is the whole mechanism routing the agent loop's
`emit()` into this session's trace — and `evidence.build` reads the same events, so a bundle
that saw none accepts nothing.

**The mode is a value, not a tone.** `advice_mode` says whether what is on screen came from
demonstrated training or from an interview, and it is derived from the turn's
`UncertaintyVerdict.grounded` rather than from anything a model wrote. Huella's premise is
that it reasons from what an athlete has actually done; presenting an interview answer
without saying so would be the one lie the whole app exists not to tell.
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

from coros_core import devices, gemini, money
from coros_core.evidence import EvidenceBundle
from coros_core.models import Advice, AdviceItem
from coros_core.trace import TraceEvent, bind_sink, emit

from huella import oauth, privacy
from huella.agent import prompts, tools
from huella.agent.loop import ConversationSession, TurnResult, run_turn

# Before every module-level env read below. `coros_core.gemini` owns the two roots `.env`
# can live at — a checkout and the flattened container — and loading is idempotent. A
# password read before the file was parsed is a gate that silently stays open.
gemini.load_env()

GATE_PASSWORD = os.environ.get("HUELLA_PASSWORD", "")
GATE_ON = bool(GATE_PASSWORD)


def _digest(password: str) -> str:
    """What the cookie carries, and what a typed password is compared as.

    `hmac.compare_digest` raises `TypeError: comparing strings with non-ASCII characters
    is not supported`, so comparing the passwords themselves turns an accented one — in a
    Spanish app, the likely kind — into a 500 instead of a refusal. Hex digests are ASCII
    and equal-length, which is what the constant-time comparison wants anyway.
    """
    return hashlib.sha256(b"huella.gate.v1:" + password.encode()).hexdigest()


_GATE_DIGEST = _digest(GATE_PASSWORD) if GATE_ON else ""
_GATE_DELAY = 0.6

_POLL_INTERVAL = 0.15

SUMMARY_CHARS = 300
VALUE_CHARS = 120

MAX_CONVERSATIONS = 200

OPENING_STATUS = "Leyendo tu entrenamiento…"

_BROKE_ERROR = (
    "Ese turno se rompió antes de terminar. El registro de la derecha tiene el último "
    "paso que corrió."
)

# ── the cold start, and the number that decides it ────────────────────────────
#
# Imported, never restated. `tools.MIN_SAMPLE` is where the threshold lives and where its
# reason is written down: under eight demonstrated activities a band is arithmetic on a
# rumour, and three weeks at two sessions a week is the least that tells a habit from a
# fortnight. A connected account with two activities is therefore NOT a signal — it takes
# the interview path, exactly as an athlete with no Strava at all does, and the difference
# between the two is said out loud rather than felt in the tone.
COLD_START_SAMPLE = tools.MIN_SAMPLE

MODE_TRAINING = "training"
MODE_INTERVIEW = "interview"

_MODE_ES: dict[str, str] = {
    MODE_TRAINING: "Derivado de tu entrenamiento registrado.",
    MODE_INTERVIEW: "Derivado de lo que me contaste, no de tu historial.",
}

_COLD_START_ES = (
    "Con menos de {sample} actividades registradas por un dispositivo no hay entrenamiento "
    "demostrado que leer, así que te pregunto en vez de deducir."
)


# ── the conversation, kept out of both the wire and the pickle ─────────────────

_CONVERSATIONS: OrderedDict[str, ConversationSession] = OrderedDict()


def _conversation_for(token: str) -> ConversationSession:
    session = _CONVERSATIONS.pop(token, None)
    if session is None:
        # `key` is how the agent loop reaches the privacy gate — it is handed to
        # `privacy.*` and used for nothing else. A conversation with no key reads as an
        # athlete with no Strava connected, which is a lie about a connected one.
        session = ConversationSession(key=token)
    session.key = token
    _CONVERSATIONS[token] = session
    while len(_CONVERSATIONS) > MAX_CONVERSATIONS:
        _CONVERSATIONS.popitem(last=False)
        emit("conversation.evicted", {"conversations": len(_CONVERSATIONS)})
    return session


def _reset_conversation(token: str) -> None:
    _CONVERSATIONS.pop(token, None)


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
    from prose."""

    product_id: str
    title: str
    product_url: str
    image_url: str = ""
    price_display: str
    rationale: str = ""
    satisfies: list[str] = Field(default_factory=list)


class RequirementRow(BaseModel):
    """One line of "what I think your training asks for". `derived` is what lets the rail
    show which half was counted and which half was said — it is `Requirement.derived`, not
    a guess from the wording."""

    key: str
    label: str
    value: str
    derived: bool
    source: str
    rationale: str = ""


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

# A bundle's own prose is English — it is an engineering artifact read in a PR. The rail is
# read by a person, so the check names are translated here.
_CHECK_ES: dict[str, str] = {
    "provenance": "cada dato sale del catálogo",
    "stock": "está disponible",
    "budget": "la cuenta contra tu presupuesto",
    "local_availability": "se vende en Colombia",
    "buy_nothing": "«no compres nada» seguía siendo posible",
    "prose": "nada afirmado sin respaldo",
}

# The chip beside the uncertainty panel. `tools._FLAG_ES` carries the full sentence the
# athlete reads; this is the two or three words that fit on a badge, and every flag needs
# one or the badge renders an English enum at a Colombian reader.
_FLAG_ES: dict[str, str] = {
    "not_connected": "sin Strava",
    "unread": "no se pudo leer",
    "reconnect": "hay que reconectar",
    "no_permission": "sin permiso de actividades",
    "no_history": "sin historial",
    "thin": "pocas actividades",
    "stale": "historial viejo",
    "truncated": "lectura cortada",
    "manual_reported": "actividades a mano",
    "overridden": "con correcciones tuyas",
}

_CONFIDENCE_ES: dict[str, str] = {
    "high": "ventana amplia y reciente",
    "medium": "ventana con reservas",
    "none": "sin entrenamiento demostrado",
}

# What the landing page says after the callback put the browser back. Keyed on
# `oauth.OUTCOMES`, because a word the route can answer with and this table cannot
# translate is a redirect that lands on silence.
_LANDING_ES: dict[str, str] = {
    oauth.OK: "Listo — tu cuenta de Strava quedó conectada.",
    oauth.DENIED: "No autorizaste el acceso, así que no leí nada. Puedes intentarlo otra vez.",
    oauth.BAD_STATE: (
        "Ese enlace de vuelta ya no servía — se usa una sola vez y caduca. Vuelve a darle a "
        "conectar."
    ),
    oauth.NO_CODE: "Strava respondió sin código de autorización, así que no hay nada que canjear.",
    oauth.UNCONFIGURED: (
        "Esta instancia no tiene credenciales de Strava configuradas, así que la conexión no "
        "puede completarse."
    ),
    oauth.EXPIRED: (
        "El código de Strava ya no servía. Vuelve a darle a conectar y termina el permiso sin "
        "recargar la página."
    ),
    oauth.RATE_LIMITED: "Strava nos limitó las consultas. Espera unos minutos y vuelve a intentarlo.",
    oauth.UNREACHABLE: "No pude terminar de hablar con Strava. Eso no es que hayas dicho que no.",
}

# Which of those is bad news. The two are separate vars on screen for the same reason
# `throttled` is separate from `status`: a colour must not be decided by matching a string.
_LANDING_IS_ERROR: frozenset[str] = frozenset(
    {oauth.BAD_STATE, oauth.NO_CODE, oauth.UNCONFIGURED, oauth.EXPIRED, oauth.RATE_LIMITED,
     oauth.UNREACHABLE}
)

_UNCONFIGURED_ES = (
    "Esta instancia no tiene STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET / STRAVA_REDIRECT_URI, "
    "así que no hay a dónde mandarte. Huella funciona sin Strava: cuéntame cómo entrenas."
)


# ── what the caption says while a turn runs ───────────────────────────────────

_RETRYING = "COROS no respondió — vuelvo a intentarlo…"
_MODEL_BUSY = "El modelo está saturado — vuelvo a intentarlo…"
# Never a promise of another attempt: COROS's 429 latches and is deliberately not retried
# and not polled, because our own retries are what keep the door shut.
_LATCHED = "COROS limitó las consultas — sigo con lo que alcancé a leer…"
# Strava's window IS published and honest, so this one may say there is a time after which
# asking again is correct — which is the opposite of the line above, on purpose.
_STRAVA_LATCHED = "Strava limitó las lecturas — su ventana de 15 minutos tiene que cerrarse…"

_THROTTLE_STATUS: dict[str, str] = {
    "catalog.retry": _RETRYING,
    "model.retry": _MODEL_BUSY,
    "catalog.rate_limited": _LATCHED,
    "catalog.unavailable": _LATCHED,
    "ucp.rate_limited": _LATCHED,
    "strava.rate_limited": _STRAVA_LATCHED,
}

# Keyed on what the LIVE pipeline emits — `test_every_caption_keys_on_an_event_something_
# actually_emits` is what keeps this table and the loop speaking one vocabulary.
_STAGE_STATUS: dict[str, str] = {
    "gate.verdict": "Entendiendo qué me pediste…",
    "training.synced": "Leyendo tu historial de Strava…",
    "training.reused": "Releyendo lo que ya conté de tu entrenamiento…",
    "guardrail.privacy_gate": "Contando actividades y derivando bandas…",
    "guardrail.uncertainty": "Decidiendo cuánto puede apoyarse en tu historial…",
    "interview.skipped": "Tu historial ya respondió — no hace falta preguntarte…",
    "questions.asked": "Viendo qué me falta saber…",
    "turn.snapshot": "Leyendo el catálogo completo de COROS…",
    "preferences.updated": "Anotando tus correcciones…",
    "tool.get_training_summary": "Repasando lo que demuestra tu entrenamiento…",
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
    "guardrail.training_figures": "Revisando que ninguna banda salga como medición…",
    "guardrail.prose": "Revisando que no afirme nada sin respaldo…",
    "evidence.bundle": "Verificando antes de responderte…",
}


def plain(value: Any) -> Any:
    """Rebuild real containers out of Reflex's `MutableProxy` wrappers.

    `isinstance` sees through the proxy; `json.dumps` does not — the C encoder does an
    exact type check, misses the wrapper and falls through to `default=`, so a whole
    payload serialises as a Python repr inside a JSON string. Passing `indent=` selects the
    pure-Python encoder, which happens to survive; a compact dump does not.
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
    line so the panel never walks an unknown shape."""
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
    # serialises to any browser.
    _raw_trace: list[dict[str, Any]] = []
    _bundle_text: str = ""

    is_thinking: bool = False
    status: str = ""
    throttled: bool = False
    error: str = ""

    # ── the Strava connection, as counts and one salted digest ────────────────
    # Everything here is derived from `privacy.Connection` and `privacy.Sync`. There is
    # deliberately no var typed to hold a TokenPair, an Activity or an ActivityWindow.
    connected: bool = False
    # Proof that a pair is stored, and irreversible. It is what lets the rail show that a
    # rotation replaced one pair rather than half of two, without naming either half.
    token_fingerprint: str = ""
    scopes: list[str] = []
    rotations: int = 0
    strava_notice: str = ""
    strava_error: str = ""

    sample_size: int = 0
    window_days: int = 0
    stale_days: int = -1
    manual_dropped: int = 0
    truncated: bool = False

    # ── the uncertainty layer, as values a UI renders ─────────────────────────
    grounded: bool = False
    confidence: str = "none"
    uncertainty_statement: str = ""
    uncertainty_flags: list[str] = []
    requirements: list[RequirementRow] = []

    # Which reading produced what is on screen. "" until a turn has produced advice.
    advice_mode: str = ""

    # False unconditionally, NOT `not GATE_ON`. A state var's default is compiled INTO the
    # frontend bundle and the image is built with no password set, so the derived form
    # bakes in as True and serves the unlocked shell to a browser whose websocket never
    # connected. `on_page_load` is what opens the gate when it is off.
    unlocked: bool = False
    gate_error: str = ""
    gate_busy: bool = False
    gate_reveal: bool = False
    gate_key: str = rx.Cookie(name="huella_gate", max_age=60 * 60 * 24 * 30, same_site="lax")

    @rx.var
    def gate_on(self) -> bool:
        return GATE_ON

    @rx.var
    def strava_configured(self) -> bool:
        """Whether the connect button can do anything. Read at request time rather than at
        import, the same way `client.py` reads its credentials — a container is handed its
        environment by docker and this module may be imported before any of it is set."""
        return oauth.is_configured()

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

    @rx.var
    def confidence_label(self) -> str:
        return _CONFIDENCE_ES[self.confidence]

    @rx.var
    def mode_label(self) -> str:
        """The sentence that keeps an interview answer from reading as a measured one."""
        return _MODE_ES.get(self.advice_mode, "")

    @rx.var
    def is_interview(self) -> bool:
        return self.advice_mode == MODE_INTERVIEW

    @rx.var
    def cold_start(self) -> bool:
        """The Brújula-shaped fallback is on. Connected-with-two-activities lands here
        exactly like never-connected: `tools.MIN_SAMPLE` is the threshold and
        `check_uncertainty` is what applies it, so this var reads the verdict rather than
        re-deciding it."""
        return not self.grounded

    @rx.var
    def cold_start_reason(self) -> str:
        return _COLD_START_ES.format(sample=COLD_START_SAMPLE)

    @rx.var
    def has_requirements(self) -> bool:
        return len(self.requirements) > 0

    @rx.var
    def stale_display(self) -> str:
        return "" if self.stale_days < 0 else f"hace {self.stale_days} días"

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

    # ── what the browser learns about the connection ──────────────────────────

    def _refresh_connection(self) -> None:
        """Re-read the derived views. Everything below is a count, a label or a digest;
        the pair and the window it read stay in `privacy.py`."""
        key = self.router.session.client_token
        view = privacy.connection(key)
        self.connected = view.connected
        self.token_fingerprint = view.token_fingerprint
        self.scopes = list(view.scopes)
        self.rotations = view.rotations

        summary = privacy.last_sync(key)
        if summary is None:
            self.sample_size = 0
            self.window_days = 0
            self.stale_days = -1
            self.manual_dropped = 0
            self.truncated = False
            return
        self.sample_size = summary.sample_size
        self.window_days = summary.window_days
        # -1 rather than None: `rx.cond` needs a value to test, and "no activity at all" is
        # not "the last one was today".
        self.stale_days = -1 if summary.stale_days is None else summary.stale_days
        self.manual_dropped = summary.manual_dropped
        self.truncated = summary.truncated

    def _apply_uncertainty(self, verdict: tools.UncertaintyVerdict) -> None:
        self.grounded = verdict.grounded
        self.confidence = verdict.confidence
        self.uncertainty_statement = verdict.statement
        self.uncertainty_flags = [_FLAG_ES[flag] for flag in verdict.flags]
        self.sample_size = verdict.sample_size
        self.window_days = verdict.window_days
        self.stale_days = -1 if verdict.stale_days is None else verdict.stale_days
        self.manual_dropped = verdict.manual_dropped
        self.truncated = verdict.truncated

    def _apply_requirements(self, session: ConversationSession) -> None:
        self.requirements = [
            RequirementRow(
                key=requirement.key,
                label=_REQUIREMENT_ES[requirement.key],
                value=str(requirement.value),
                derived=requirement.derived,
                source=requirement.source,
                rationale=requirement.rationale,
            )
            for requirement in session.requirements()
        ]

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
        if result.uncertainty is not None:
            self._apply_uncertainty(result.uncertainty)
        # Only when the turn produced advice. A greeting, a redirect or a blocked injection
        # produces none and must not retract what the last turn verified — nor relabel it:
        # the mode belongs to the advice on screen, so it moves only when that does.
        if result.advice is not None:
            self._apply_advice(result.advice)
            self.advice_mode = (
                MODE_TRAINING
                if result.uncertainty is not None and result.uncertainty.grounded
                else MODE_INTERVIEW
            )
        if result.evidence is not None:
            self._apply_evidence(result.evidence)

    def run_report(self) -> str:
        """The whole run as text: what was said, every payload in full, and the bundle.

        `plain()` first — see its docstring for what a compact `json.dumps` does to a state
        container."""
        lines = [
            f"# Huella — {len(self.messages)} mensajes, {len(self._raw_trace)} eventos",
            "",
            *(f"{message.role}: {message.content}" for message in self.messages),
            "",
            "## trace",
            *(json.dumps(plain(event), ensure_ascii=False) for event in self._raw_trace),
        ]
        if self._bundle_text:
            lines += ["", self._bundle_text]
        return "\n".join(lines)

    # ── events ────────────────────────────────────────────────────────────────

    @rx.event
    def toggle_trace(self):
        self.show_trace = not self.show_trace

    @rx.event
    def toggle_reveal(self):
        self.gate_reveal = not self.gate_reveal

    @rx.event
    def clear(self):
        """Start over: a new conversation, the same admission and the same connection.
        Disconnecting is its own button — starting a conversation again is not a reason to
        make somebody re-authorise Strava."""
        _reset_conversation(self.router.session.client_token)
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
        self.requirements = []
        self.advice_mode = ""
        self.strava_notice = ""
        self.strava_error = ""

    @rx.event
    def connect_strava(self):
        """Send the browser to Strava's own authorize page.

        `oauth.begin` is what ties the `state` it issues to THIS session, and it is the only
        place that association is made — the callback has no Reflex session to read one
        from. See `huella/oauth.py`.
        """
        if GATE_ON and not self.unlocked:
            return None
        self.strava_error = ""
        self.strava_notice = ""
        try:
            url = oauth.begin(self.router.session.client_token)
        except oauth.Unconfigured:
            self.strava_error = _UNCONFIGURED_ES
            return None
        return rx.redirect(url, is_external=True)

    @rx.event
    async def disconnect_strava(self):
        if GATE_ON and not self.unlocked:
            return
        key = self.router.session.client_token
        sink: list[TraceEvent] = []
        bind_sink(sink)
        try:
            report = await privacy.disconnect(key)
            self._drain(sink)
        finally:
            bind_sink(None)
        # `revoked` is Strava's half and the drop is ours. The drop always happened, so the
        # notice never says "we still have it" — it says whether the grant upstream is gone.
        self.strava_notice = (
            "Se borró todo lo que teníamos de tu Strava."
            if report.revoked or not report.was_connected
            else report.detail
        )
        self._refresh_connection()
        self._apply_uncertainty(
            tools.check_uncertainty(None, connected=False)
        )
        yield

    @rx.event
    def forget_me(self):
        """The athlete's own "delete my data". Drops the credential, the window and the
        conversation without asking Strava anything — `privacy.forget` is a dict access and
        cannot make a network call, and dropping our copy is the half that protects them."""
        if GATE_ON and not self.unlocked:
            return
        key = self.router.session.client_token
        privacy.forget(key)
        _reset_conversation(key)
        self.clear()
        self._refresh_connection()
        self._apply_uncertainty(tools.check_uncertainty(None, connected=False))
        self.strava_notice = "Se borró todo lo que teníamos de esta sesión."

    @rx.event
    async def unlock(self, form_data: dict[str, Any]):
        if not GATE_ON or self.unlocked or self.gate_busy:
            return

        self.gate_busy = True
        self.gate_error = ""
        yield

        await asyncio.sleep(_GATE_DELAY)
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
        # whatever is on screen. `GATE_ON and` is not redundant either: a headless verify
        # script never runs `on_page_load`, so a bare check disables it silently.
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

        sink: list[TraceEvent] = []
        # BEFORE create_task, and that is the whole point — see the module docstring.
        bind_sink(sink)
        try:
            # The turn number and the length, never the words.
            emit("turn.start", {"turn": len(self.messages) // 2 + 1, "chars": len(text)})
            self._drain(sink)
            yield

            session = _conversation_for(self.router.session.client_token)
            task = asyncio.create_task(run_turn(text, session))
            while not task.done():
                await asyncio.sleep(_POLL_INTERVAL)
                if self._drain(sink):
                    yield

            self._apply(await task)
            self._drain(sink)
            self._apply_requirements(session)
            self._refresh_connection()
        except Exception as exc:
            # The class name goes to the trace, which is on screen beside this and is where
            # detail belongs. A stack trace in the chat teaches nobody anything.
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

    def _read_landing(self) -> None:
        """What `oauth.callback` redirected back with. A word out of a closed vocabulary,
        never anything Strava wrote — the callback exchanged the code server-side and this
        is the only thing that crossed."""
        try:
            outcome = str(self.router.url.query_parameters.get("strava", ""))
        except AttributeError:
            return
        if outcome not in oauth.OUTCOMES:
            return
        message = _LANDING_ES[outcome]
        if outcome in _LANDING_IS_ERROR:
            self.strava_error, self.strava_notice = message, ""
        else:
            self.strava_notice, self.strava_error = message, ""

    @rx.event
    def on_page_load(self):
        """Something has to open the gate, because `unlocked` defaults to a hard False —
        and something has to read the callback's answer, because the browser came back from
        Strava through a redirect this state machine never saw."""
        if not GATE_ON:
            self.unlocked = True
        elif not self.unlocked and hmac.compare_digest(self.gate_key, _GATE_DIGEST):
            self.unlocked = True
        self._read_landing()
        self._refresh_connection()
