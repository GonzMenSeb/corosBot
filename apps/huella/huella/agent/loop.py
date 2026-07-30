"""The turn: one job per model call, and a stage is skipped only if it finished.

  1  intent gate       structured, no tools    -> IntentVerdict
  2  training          NO MODEL CALL           -> privacy.sync() + the D2 verdict
  3  interview         structured, no tools    -> the FALLBACK, only when 2 read nothing
  4  requirements      structured, no tools    -> the athlete's own corrections, typed
  5..n retrieval       function tools ONLY     -> real products, from the turn's snapshot
  n+1 selection        structured, no tools    -> product ids; the item is rebuilt from the feed
  n+2 presentation     text                    -> prose only, and only on the recommend path

Brújula asks; Huella reads. Stage 2 is the whole difference, and it is deliberately not a
model call: `privacy.derive_requirements()` counts activities in Python and hands back
sealed `Requirement`s, so the athlete's training reaches the catalogue path as bands and
never as a payload. The interview is what happens when that read comes back empty, thin
or unconnected — a fallback, not the main path — and stage 3 is skipped outright for an
athlete whose history already answered.

Five things here are worth reading before changing anything.

**A refusal from Strava never becomes advice.** `privacy.sync()` carries a `ToolOutcome`
through verbatim, so a 429 arrives as RATE_LIMITED rather than as an empty history. A
transient outcome ends the turn with `_unread_training` — no stage marked done, nothing
presented, the next turn resumes — because "we could not look at your training" and "your
training does not need anything" are two different sentences and only one of them is true.

**The uncertainty verdict is computed before anything is generated.** `check_uncertainty`
returns a typed `UncertaintyVerdict`, `DemonstratedTraining` cannot be constructed for a
window under `MIN_SAMPLE` activities, and the verdict rides on `TurnResult` and in
`Advice.caveats` — so the flag is a value a UI renders, not a sentence a prompt asked for.

**A correction is state, and it is written before the turn generates anything.** Stage 4
writes into `session.preferences`, and every later stage of the SAME turn reads
`view.effective`. Constraints are enforced at serving time: `Preferences.apply()` puts the
athlete's band where the derived one was, and there is no path by which a prompt sentence
does that job instead.

**Resumption is keyed on completion, not on emptiness.** `session.done` records the stages
that finished, so an interview that asked nothing is not an interview that never ran, and
a turn that dies during retrieval resumes there instead of presenting an empty
recommendation. Termination is governed by `evidence.build`, never by the model deciding
it is finished (KB §3.4.4).

**A response schema is never a core model.** `extra="forbid"` renders
`additionalProperties` into the schema and the API answers 400 INVALID_ARGUMENT, so the
wire models below are plain and are validated into the frozen ones in code.

Run one turn standalone:  PYTHONPATH=.:packages:apps/huella ./.venv/bin/python -m huella.agent.loop
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, get_args

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from coros_core import capability, catalog, evidence, gemini, guardrails, trace
from coros_core.capability import CapabilityRequest, CapabilityVerdict
from coros_core.evidence import EvidenceBundle
from coros_core.models import (
    Advice,
    AdviceItem,
    AdviceKind,
    AdviceSpec,
    Budget,
    CatalogProduct,
    Intent,
    IntentVerdict,
    Provenance,
    Requirement,
    RequirementKey,
)
from coros_core.outcomes import ToolOutcome, ToolResult
from coros_core.trace import emit

from huella import privacy
from huella.agent import prompts, tools

MAX_TOOL_CALLS_PER_TURN = 6
MAX_MODEL_CALLS = 25
MAX_TOOL_RETRIES = 2
RETRY_BACKOFF = 0.5

# Strava's read limit is 100 requests per rolling quarter-hour and one sync spends up to
# `client.MAX_PAGES` of it, so a conversation re-reads at most once per window. The
# freshness this costs is bounded by the same number: a window of 90 days does not move in
# fifteen minutes.
SYNC_TTL_SECONDS = 900

MAX_QUESTIONS = 3
CANDIDATE_CHARS = 60_000
SUMMARY_CHARS = 1_500
MESSAGE_CHARS = 2_000

TRAINING = "training"
INTERVIEW = "interview"
REQUIREMENTS = "requirements"
RETRIEVAL = "retrieval"
SELECTION = "selection"
PRESENTATION = "presentation"

# A message that arrives after a finished recommendation reopens the case. TRAINING is in
# here too: the athlete came back with something new, and the TTL above is what keeps the
# re-read from spending Strava's quota on every turn.
REOPENED: tuple[str, ...] = (TRAINING, REQUIREMENTS, RETRIEVAL, SELECTION, PRESENTATION)

REQUIREMENT_KEYS: tuple[str, ...] = get_args(RequirementKey)
PROVENANCES: tuple[str, ...] = get_args(Provenance)

# What a model-extracted requirement may claim. "strava" is not on the list and cannot be:
# that provenance belongs to `privacy.derive_requirements` alone, and it is what lets the
# uncertainty layer say which half of the advice was counted and which half was said.
STATED_SOURCES: frozenset[str] = frozenset({"user", "assumed"})


# ── what the model is allowed to hand back ────────────────────────────────────


class _Questions(BaseModel):
    questions: list[str] = Field(default_factory=list)


class _RawRequirement(BaseModel):
    key: str = ""
    value: str | int | bool = ""
    source: str = "user"
    rationale: str = ""
    # The delete half of an editable preference state: the athlete withdrew something they
    # said, and a later prompt contradicting an earlier one is not a deletion.
    drop: bool = False


class _Requirements(BaseModel):
    requirements: list[_RawRequirement] = Field(default_factory=list)


class _Pick(BaseModel):
    product_id: str = ""
    variant_id: str = ""
    rationale: str = ""
    satisfies: list[str] = Field(default_factory=list)


class _Selection(BaseModel):
    kind: str = "recommend"
    items: list[_Pick] = Field(default_factory=list)
    unavailable_devices: list[str] = Field(default_factory=list)
    reason: str = ""


# Every schema handed to Gemini, so a test can check all of them for the
# `additionalProperties` 400 in one place.
SCHEMAS: tuple[type[BaseModel], ...] = (IntentVerdict, _Questions, _Requirements, _Selection)


class TurnResult(BaseModel):
    text: str = ""
    intent: Intent = "advice"
    stage: str = ""
    questions: tuple[str, ...] = ()
    advice: Advice | None = None
    evidence: EvidenceBundle | None = None
    # D2 as a value the UI renders, rather than a caveat buried in prose.
    uncertainty: tools.UncertaintyVerdict | None = None
    error: str = ""


@dataclass
class ConversationSession:
    """One conversation. `done` is the whole resumption mechanism — see the docstring.

    `key` is the Reflex client token, and the only thing this object knows about the
    athlete's Strava session: it is handed to `privacy.*` and never used for anything else.
    Nothing here holds a credential, a raw payload or a window."""

    key: str = ""
    turns: list[dict[str, str]] = field(default_factory=list)
    question: str = ""
    discipline: str = ""
    answers: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    preferences: tools.Preferences = field(default_factory=tools.Preferences)
    # Derived views only: `Sync` carries counts and sealed requirements, and the window it
    # was read from never leaves `privacy.py`.
    summary: privacy.Sync | None = None
    view: tools.TrainingView | None = None
    connected: bool = False
    synced_at: float = 0.0
    seen: list[str] = field(default_factory=list)
    # Retrieval reached the end of what it asked for. False is not "nothing matched": it is
    # "we do not know", and it is what stops a refusal being reported as an inventory.
    conclusive: bool = False
    capped: bool = False
    advice: Advice | None = None
    presented: str = ""
    done: set[str] = field(default_factory=set)
    model_calls: int = 0

    def completed(self, stage: str) -> bool:
        return stage in self.done

    def complete(self, stage: str) -> None:
        self.done.add(stage)

    def reopen(self) -> None:
        # `preferences` deliberately survives: a correction the athlete made is standing
        # state until they drop it, not something a new question withdraws.
        self.done -= set(REOPENED)
        self.seen = []
        self.conclusive = False
        self.capped = False
        self.advice = None
        self.presented = ""

    def transcript(self, limit: int = 6) -> str:
        return "\n".join(f"{t['role']}: {t['text'][:400]}" for t in self.turns[-limit:]) or "(nada)"

    def said(self) -> str:
        return " ".join([self.question, *self.answers])

    def requirements(self) -> tuple[Requirement, ...]:
        return self.view.effective if self.view is not None else self.preferences.all()


class _BudgetSpent(Exception):
    """The conversation's model-call budget. Raised instead of the 26th call."""


class _Unreadable(Exception):
    """A structured stage whose JSON did not validate. Fails the turn rather than
    substituting a default: a fabricated stage output is indistinguishable downstream from
    a real one."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"{stage} returned something this loop could not read")
        self.stage = stage


class _NoCapability(Exception):
    def __init__(self, verdict: CapabilityVerdict) -> None:
        super().__init__(f"no tool serves {verdict.need!r} on {verdict.surface}")
        self.verdict = verdict


def _model_quota(exc: BaseException) -> bool:
    """Gemini's own 429. Keyed on the HTTP code rather than on the SDK's exception class so
    it survives google-genai renaming one."""
    return getattr(exc, "code", None) == 429


async def _model(session: ConversationSession, **kwargs: Any) -> types.GenerateContentResponse:
    if session.model_calls >= MAX_MODEL_CALLS:
        emit("guardrail.model_budget", {"calls": session.model_calls}, "guardrail")
        raise _BudgetSpent
    session.model_calls += 1
    return await gemini.generate(**kwargs)


def _cfg(**kw: Any) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(system_instruction=prompts.SYSTEM, **kw)


def _parse(response: types.GenerateContentResponse, schema: Any, stage: str) -> Any:
    """`response.parsed` is silently None whenever validation fails — google-genai swallows
    the ValidationError — so a stage that broke would be indistinguishable from one that
    answered with nothing. The raw text is validated here instead."""
    try:
        return schema.model_validate_json(response.text or "")
    except (ValidationError, ValueError) as exc:
        emit("stage.unreadable", {"stage": stage, "error": str(exc)[:300]}, "error")
        raise _Unreadable(stage) from exc


# ── the turn's one storefront read ────────────────────────────────────────────


async def read_snapshot() -> tools.Snapshot:
    """One request per turn, and every catalogue tool answers from it. A second read would
    spend the harshest limiter in the system to re-fetch what we are holding."""
    try:
        products = await catalog.get_products(include_hidden=True)
    except catalog.CatalogUnavailable as exc:
        snapshot = tools.Snapshot(
            outcome=ToolOutcome.RATE_LIMITED if exc.rate_limited else ToolOutcome.UPSTREAM_ERROR,
            detail=(
                "COROS limitó las consultas y el catálogo no se pudo leer en este turno"
                if exc.rate_limited
                else f"el catálogo no respondió en este turno ({exc.status or 'sin código'})"
            ),
        )
    else:
        snapshot = tools.Snapshot(products=products)

    tools.bind_snapshot(snapshot)
    emit(
        "turn.snapshot",
        {
            "outcome": snapshot.outcome.value,
            "products": len(snapshot.products),
            "visible": len(snapshot.visible),
        },
    )
    return snapshot


def declarations() -> list[types.FunctionDeclaration]:
    """The tool surface, taken from the capability map rather than hardcoded. An empty map
    is a typed dead end here, never a retrieval stage with no tools — a model asked to
    retrieve with nothing to retrieve with answers from memory."""
    allowed: set[str] = set()
    for need in ("training_history", "product_recommendation"):
        verdict = capability.check_capability(CapabilityRequest(need=need, surface="huella"))
        if verdict.is_dead_end:
            raise _NoCapability(verdict)
        allowed |= {t.value for t in verdict.tools}
    return tools.declared(allowed)


# ── call 1 · the gate ─────────────────────────────────────────────────────────


async def _gate(session: ConversationSession, message: str) -> IntentVerdict:
    response = await _model(
        session,
        contents=prompts.GATE_PROMPT.format(
            message=message[:MESSAGE_CHARS], history=session.transcript()
        ),
        config=_cfg(response_mime_type="application/json", response_schema=IntentVerdict),
    )
    verdict = _parse(response, IntentVerdict, "gate")
    emit("gate.verdict", {"intent": verdict.intent, "discipline": verdict.discipline})
    return verdict


# ── stage 2 · the history, and no model call anywhere in it ───────────────────


async def _training(session: ConversationSession, *, now: float | None = None) -> tools.TrainingView:
    """Read the athlete's window through the privacy gate and bind what came back.

    Everything this function touches is a derived view: `privacy.sync` answers with counts
    and sealed `Requirement`s, and the window it read stays inside `privacy.py`. A second
    call inside `SYNC_TTL_SECONDS` reuses the gate's own last summary instead of spending
    Strava's read quota."""
    stamp = time.time() if now is None else now
    connected = bool(session.key) and privacy.is_connected(session.key)
    summary: privacy.Sync | None = None

    if connected:
        cached = privacy.last_sync(session.key)
        if cached is not None and stamp - session.synced_at < SYNC_TTL_SECONDS:
            summary = cached
            emit(
                "training.reused",
                {"age_s": round(stamp - session.synced_at, 1), "outcome": cached.outcome.value},
            )
        else:
            try:
                summary = await privacy.sync(session.key, window_days=privacy.WINDOW_DAYS, now=stamp)
            except privacy.NotConnected:
                connected = False
            else:
                session.synced_at = stamp
                emit(
                    "training.synced",
                    {
                        "outcome": summary.outcome.value,
                        "activities": summary.sample_size,
                        "manual_dropped": summary.manual_dropped,
                        "truncated": summary.truncated,
                        "stale_days": summary.stale_days,
                        "requirements": len(summary.requirements),
                    },
                )

    session.summary, session.connected = summary, connected
    view = _bind(session)
    # A transient refusal leaves the stage open on purpose, so the next turn reads again
    # rather than treating "we could not look" as a finished answer.
    if view.outcome.is_conclusive:
        session.complete(TRAINING)
    return view


def _bind(session: ConversationSession) -> tools.TrainingView:
    """Rebuild the turn's view from what the gate answered and what the athlete has
    corrected since, and bind it where the tools read it."""
    view = tools.training_view(
        session.summary, connected=session.connected, preferences=session.preferences
    )
    tools.bind_training(view)
    session.view = view
    return view


# ── call 2 · the interview, which is the fallback ─────────────────────────────


def _groups() -> str:
    listed = tools.snapshot()
    if listed is None or not listed.outcome.is_ok:
        return "(no se pudo leer)"
    return "\n".join(
        f"{g.handle} — {g.title}: {sum(1 for p in listed.visible if g.holds(p))} productos"
        for g in tools.GROUPS
    )


async def _interview(session: ConversationSession, view: tools.TrainingView) -> list[str]:
    response = await _model(
        session,
        contents=prompts.INTERVIEW_PROMPT.format(
            message=session.question[:MESSAGE_CHARS],
            discipline=session.discipline or "(no la dijo)",
            uncertainty=view.uncertainty.statement,
            known="\n".join(session.answers) or "(nada todavía)",
            groups=_groups(),
        ),
        config=_cfg(response_mime_type="application/json", response_schema=_Questions),
    )
    asked = _parse(response, _Questions, INTERVIEW)
    questions = [q.strip() for q in asked.questions if q.strip()][:MAX_QUESTIONS]
    emit("questions.asked", {"count": len(questions)})
    return questions


# ── call 3 · the athlete's own corrections, written before anything generates ──


def _rendered(requirements: Sequence[Requirement]) -> str:
    return json.dumps(
        [
            {"key": r.key, "value": r.value, "source": r.source, "derived": r.derived}
            for r in requirements
        ],
        ensure_ascii=False,
    )


async def _requirements(session: ConversationSession, view: tools.TrainingView) -> None:
    """Read what the athlete said and write it into the preference state.

    Nothing is returned: the state IS the output, it is written before the retrieval,
    selection and presentation stages of this same turn read it, and it survives into the
    next conversation turn until an explicit `drop`."""
    response = await _model(
        session,
        contents=prompts.REQUIREMENT_PROMPT.format(
            message=session.question[:MESSAGE_CHARS],
            answers="\n".join(session.answers) or "(ninguna)",
            derived=_rendered(view.derived) if view.derived else "(no se leyó historial)",
            keys=", ".join(REQUIREMENT_KEYS),
            bands=tools.BAND_VOCABULARY,
        ),
        config=_cfg(response_mime_type="application/json", response_schema=_Requirements),
    )
    raw = _parse(response, _Requirements, REQUIREMENTS)

    stored: list[str] = []
    dropped: list[str] = []
    rejected: list[str] = []
    downgraded = 0
    for row in raw.requirements:
        if row.drop:
            (dropped if session.preferences.drop(row.key) else rejected).append(row.key)
            continue
        if row.source not in STATED_SOURCES:
            # A model claiming the athlete's own words came off Strava is the one relabel
            # that would make the uncertainty layer lie, so it is corrected rather than
            # trusted.
            downgraded += 1
        kept = session.preferences.set(row.key, row.value, rationale=row.rationale)
        (stored if kept is not None else rejected).append(row.key)

    if rejected or downgraded:
        emit(
            "guardrail.requirement_rejected",
            # The key only when it is our own vocabulary: an invented one is text derived
            # from what a person typed, and evidence bundles paste payloads back into a
            # model's context.
            {
                "rejected": len(rejected),
                "downgraded": downgraded,
                "keys": [k for k in rejected if k in REQUIREMENT_KEYS],
            },
            "guardrail",
        )
    emit(
        "preferences.updated",
        {"stored": stored, "dropped": dropped, "held": len(session.preferences.keys())},
    )


def _budget(requirements: Sequence[Requirement]) -> Budget:
    """`budget_minor` is centavos by prompt and by `money.py`'s contract. A value that is
    not a whole number of them is dropped rather than guessed at: a budget read wrong by a
    factor of a hundred reports "nothing fits" about a catalogue full of things that do."""
    for requirement in requirements:
        if requirement.key != "budget_minor":
            continue
        value = requirement.value
        text = "" if isinstance(value, bool) else str(value).strip()
        if not text.isdigit():
            emit("guardrail.budget_unreadable", {"length": len(text)}, "guardrail")
            continue
        return Budget(max_total_minor=int(text))
    return Budget()


# ── calls 4..n · retrieval, tools only ────────────────────────────────────────


def _harvest(result: ToolResult) -> list[str]:
    data = result.data if isinstance(result.data, dict) else {}
    found: list[str] = []
    for key in ("products", "straps"):
        for row in data.get(key) or []:
            product_id = str(row.get("product_id") or "") if isinstance(row, dict) else ""
            if product_id:
                found.append(product_id)
    return found


async def _dispatch(name: str, args: dict[str, Any]) -> ToolResult:
    fn = tools.DISPATCH.get(name)
    if fn is None:
        emit(
            "guardrail.unknown_tool",
            # The name only when it is a name this system knows — `create_cart` is the one
            # worth seeing, and anything else is text the model made up.
            {
                "tool": name if name in capability.WITHHELD else "",
                "known": sorted(tools.DISPATCH),
            },
            "guardrail",
        )
        return ToolResult(
            tool=name or "unknown",
            outcome=ToolOutcome.NOT_ELIGIBLE,
            detail=(
                f"esa herramienta no existe. Las que existen: {', '.join(sorted(tools.DISPATCH))}."
            ),
        )

    for attempt in range(MAX_TOOL_RETRIES + 1):
        try:
            return await fn(**args)
        except Exception as exc:
            # The class, never the message: a tool argument is text derived from a person's
            # own words and an exception repeats it back.
            emit(
                "tool.error",
                {"tool": name, "attempt": attempt + 1, "error": type(exc).__name__},
                "error",
            )
            if attempt == MAX_TOOL_RETRIES:
                return ToolResult(
                    tool=name,
                    outcome=ToolOutcome.UPSTREAM_ERROR,
                    detail=(
                        f"{name} falló {MAX_TOOL_RETRIES + 1} veces. No se consultó: esto no es "
                        "que no haya nada."
                    ),
                )
            await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
    raise AssertionError("unreachable: every attempt returns or retries")


async def _retrieve(session: ConversationSession, view: tools.TrainingView) -> None:
    prompt = prompts.RETRIEVE_PROMPT.format(
        requirements=_rendered(view.effective),
        uncertainty=view.uncertainty.statement,
        message=session.question[:MESSAGE_CHARS],
        seen=json.dumps(sorted(set(session.seen))) if session.seen else "(nada todavía)",
        max_calls=MAX_TOOL_CALLS_PER_TURN,
    )
    history: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    config = _cfg(tools=gemini.as_tools(declarations()))

    requested_total = 0
    dispatched = 0
    answered = 0
    conclusive = True
    limited = False
    summary = ""

    while requested_total < MAX_TOOL_CALLS_PER_TURN:
        response = await _model(session, contents=history, config=config)
        content = response.candidates[0].content if response.candidates else None
        if content is None:
            break
        history.append(content)

        requested = [p.function_call for p in (content.parts or []) if p.function_call]
        if not requested:
            summary = (response.text or "")[:SUMMARY_CHARS]
            break

        # Every function_call in a turn needs a matching response part, in ONE Content, or
        # the next request 400s.
        parts: list[types.Part] = []
        for call in requested:
            requested_total += 1
            name = call.name or "unknown"
            if requested_total > MAX_TOOL_CALLS_PER_TURN:
                if requested_total == MAX_TOOL_CALLS_PER_TURN + 1:
                    emit("guardrail.tool_budget", {"requested": len(requested)}, "guardrail")
                session.capped = True
                # TIMEOUT, not UNAVAILABLE: we stopped, and the model has to read that as
                # "nothing was learned here" rather than as "there is nothing".
                refusal = ToolResult(
                    tool=name,
                    outcome=ToolOutcome.TIMEOUT,
                    detail=(
                        f"se acabó el límite de {MAX_TOOL_CALLS_PER_TURN} consultas de este "
                        "turno. Esto no es una respuesta del catálogo: para y resume."
                    ),
                )
                parts.append(
                    types.Part.from_function_response(name=name, response=tools.as_response(refusal))
                )
                continue

            dispatched += 1
            result = await _dispatch(name, dict(call.args or {}))
            if name in tools.DISPATCH:
                # Our own cap is not evidence about the catalogue, and neither is a tool
                # name the model invented — only a real tool's answer moves this.
                answered += 1
                conclusive = conclusive and result.outcome.is_conclusive
                limited = limited or result.outcome is ToolOutcome.RATE_LIMITED
            session.seen.extend(_harvest(result))
            parts.append(
                types.Part.from_function_response(name=name, response=tools.as_response(result))
            )
        history.append(types.Content(role="user", parts=parts))

    session.conclusive = conclusive and answered > 0
    emit(
        "retrieval.done",
        {
            "tool_calls": dispatched,
            "requested": requested_total,
            "products_seen": len(set(session.seen)),
            "conclusive": session.conclusive,
            "rate_limited": limited,
            "summary_chars": len(summary),
        },
    )


# ── call n+1 · selection, then the checks that decide ─────────────────────────


@dataclass(frozen=True)
class _Decision:
    advice: Advice
    unavailable: tuple[guardrails.UnavailableDevice, ...]
    reason: str


def _candidates(session: ConversationSession, snapshot: tools.Snapshot) -> list[CatalogProduct]:
    by_id = {p.product_id: p for p in snapshot.visible}
    return [by_id[i] for i in dict.fromkeys(session.seen) if i in by_id]


async def _select(
    session: ConversationSession,
    spec: AdviceSpec,
    view: tools.TrainingView,
    candidates: Sequence[CatalogProduct],
) -> _Selection:
    response = await _model(
        session,
        contents=prompts.SELECT_PROMPT.format(
            requirements=_rendered(spec.requirements),
            uncertainty=view.uncertainty.statement,
            budget=(
                "sin presupuesto fijado"
                if spec.budget.is_unlimited
                else f"{spec.budget.max_total_minor} centavos de peso"
            ),
            products=json.dumps(tools.as_candidates(candidates), ensure_ascii=False)[
                :CANDIDATE_CHARS
            ],
        ),
        config=_cfg(response_mime_type="application/json", response_schema=_Selection),
    )
    return _parse(response, _Selection, SELECTION)


def _decide(
    session: ConversationSession,
    snapshot: tools.Snapshot,
    spec: AdviceSpec,
    view: tools.TrainingView,
    selection: _Selection,
    candidates: Sequence[CatalogProduct],
) -> _Decision:
    """Five checks, all in code, all emitting the events `evidence.build` reads back. The
    model contributed a product id, a variant id and a sentence; everything rendered is
    rebuilt from the feed."""
    picks = [
        {
            "product_id": pick.product_id,
            "variant_id": pick.variant_id,
            "rationale": pick.rationale[:300],
            # A key the model invented would fail AdviceItem's own validation and take a
            # good product down with it. The requirement it names is dropped, not the item.
            "satisfies": tuple(k for k in pick.satisfies if k in REQUIREMENT_KEYS),
        }
        for pick in selection.items
    ]

    provenance = guardrails.check_provenance(picks, snapshot.products)
    stock = guardrails.check_stock(provenance.renderable, snapshot.products)
    items: tuple[AdviceItem, ...] = tuple(stock.items)
    budget = guardrails.check_budget(items, spec.budget)

    by_id = {p.product_id: p for p in snapshot.products}
    local = guardrails.check_local_availability(
        text=session.said(), products=[by_id[i.product_id] for i in items if i.product_id in by_id]
    )
    nothing = guardrails.check_buy_nothing(
        candidates, budget=spec.budget, retrieval_conclusive=session.conclusive
    )

    kind: AdviceKind
    if not session.conclusive:
        kind = "insufficient_evidence"
    elif nothing.buy_nothing:
        # Before `items`, deliberately: a selection that does not fit the budget is a
        # buy-nothing, and presenting it anyway is the disagreement the bundle blocks on.
        kind = "buy_nothing"
    elif items:
        kind = "recommend"
    elif local.unavailable:
        kind = "not_sold_locally"
    else:
        kind = "buy_nothing"

    # The uncertainty statement is a caveat on every kind, not only on a recommendation:
    # "no compres nada" leaning on four activities is exactly as conditional as a purchase
    # leaning on four activities.
    caveats = tuple(
        c
        for c in (
            budget.message if kind == "recommend" else "",
            view.uncertainty.statement if view.uncertainty.flags else "",
        )
        if c
    )
    advice = Advice(
        kind=kind,
        items=items if kind == "recommend" else (),
        unavailable_devices=tuple(u.slug for u in local.unavailable),
        caveats=caveats,
        spec_digest=spec.digest,
    )
    emit(
        "selection.built",
        {
            "kind": kind,
            "claimed": len(selection.items),
            "items": len(advice.items),
            "unavailable": list(advice.unavailable_devices),
            "grounded": view.uncertainty.grounded,
        },
    )
    return _Decision(advice=advice, unavailable=local.unavailable, reason=nothing.detail)


# ── call n+2 · presentation, and the refusals that never cost one ─────────────


def _names(unavailable: Sequence[guardrails.UnavailableDevice]) -> str:
    names = [f"el {u.name}" for u in unavailable]
    if len(names) < 2:
        return names[0] if names else "ese modelo"
    return " ni ".join([", ".join(names[:-1]), names[-1]])


async def _present(
    session: ConversationSession, spec: AdviceSpec, view: tools.TrainingView, decision: _Decision
) -> str:
    advice = decision.advice
    if advice.kind == "not_sold_locally":
        return prompts.NOT_SOLD_TEMPLATE.format(
            device=_names(decision.unavailable),
            tradeoff="\n\n".join(dict.fromkeys(u.tradeoff for u in decision.unavailable)),
        )
    if advice.kind == "buy_nothing":
        return prompts.BUY_NOTHING_TEMPLATE.format(
            reason=decision.reason or "de lo que revisé, nada resuelve lo que tu entrenamiento pide"
        )
    if advice.kind != "recommend":
        return prompts.UNREACHABLE_TEMPLATE

    response = await _model(
        session,
        contents=prompts.PRESENT_PROMPT.format(
            message=session.question[:MESSAGE_CHARS],
            requirements=_rendered(spec.requirements),
            uncertainty=view.uncertainty.statement,
            items=json.dumps(
                [{"title": i.title, "rationale": i.rationale} for i in advice.items],
                ensure_ascii=False,
            ),
            unavailable=json.dumps([u.name for u in decision.unavailable], ensure_ascii=False),
            unchecked=(
                "se acabó el límite de consultas del turno y quedaron requisitos sin revisar"
                if session.capped
                else "(nada)"
            ),
        ),
        config=_cfg(),
    )
    # Two scrubs, in this order and for two different inventions. The first excises a
    # training figure that is not a band this turn handed over — a derived range converted
    # into a measurement. The second is Brújula's: retrieval stops invented PRODUCTS,
    # nothing stops invented PROPERTIES of real ones. The band phrases are passed as
    # backing text so the second does not dismantle a band the first just cleared.
    text = tools.scrub_training_prose((response.text or "").strip(), spec.requirements)
    allowed = [spec.budget.max_total_minor] if spec.budget.max_total_minor is not None else []
    return guardrails.scrub_prose(
        text,
        [*advice.items, *tools.band_phrases(spec.requirements)],
        allowed_minor=allowed,
    )


def _questions_text(questions: Sequence[str]) -> str:
    return prompts.CLARIFY_TEMPLATE.format(
        question="\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))
    )


# ── the turn ──────────────────────────────────────────────────────────────────


async def run_turn(user_message: str, session: ConversationSession) -> TurnResult:
    start = trace.mark()
    # A turn that never reads the feed or the history must not answer from the last one's.
    tools.bind_snapshot(None)
    tools.bind_training(None)
    session.turns.append({"role": "user", "text": user_message})
    result = TurnResult()

    try:
        verdict = await _gate(session, user_message)
        result.intent = verdict.intent

        if verdict.intent == "greeting":
            result.text, result.stage = prompts.GREETING_TEMPLATE, "greeting"
        elif verdict.intent == "off_topic":
            result.text = prompts.OFF_TOPIC_TEMPLATE.format(reason=verdict.reason)
            result.stage = "off_topic"
        elif verdict.intent == "out_of_scope":
            result.text = prompts.OUT_OF_SCOPE_TEMPLATE.format(reason=verdict.reason)
            result.stage = "out_of_scope"
        elif verdict.intent == "safety_critical":
            result.text = prompts.SAFETY_CRITICAL_TEMPLATE.format(reason=verdict.reason)
            result.stage = "safety_critical"
        elif verdict.intent == "injection":
            result = _injection(session, result)
        else:
            result = await _advise(session, user_message, verdict, result, start)

    except _BudgetSpent:
        result.text, result.stage = prompts.LIMIT_TEMPLATE, "limit"
        result.error = "model call budget spent"
    except _NoCapability as exc:
        result = _dead_end(exc.verdict, result)
    except privacy.PrivacyLeak as exc:
        # The gate produced something it is not allowed to produce, or this loop built a
        # requirement claiming a provenance it did not earn. A defect, and the one failure
        # that must never be answered around.
        emit("turn.privacy_leak", {"error": type(exc).__name__}, "error")
        result.text, result.stage = prompts.BROKE_TEMPLATE, "error"
        result.error = f"{type(exc).__name__}"
    except catalog.CatalogUnavailable as exc:
        emit("turn.rate_limited", {"status": exc.status}, "error")
        result.text = (
            prompts.RATE_LIMITED_TEMPLATE if exc.rate_limited else prompts.UNREACHABLE_TEMPLATE
        )
        result.stage, result.error = "rate_limited", f"{type(exc).__name__}: {exc}"[:300]
    except Exception as exc:
        result.text, result.stage = _failure(exc)
        result.error = f"{type(exc).__name__}: {exc}"[:300]

    session.turns.append({"role": "assistant", "text": result.text})
    emit(
        "turn.done",
        {"intent": result.intent, "stage": result.stage, "model_calls": session.model_calls},
    )
    return result


def _failure(exc: BaseException) -> tuple[str, str]:
    """(what the person reads, the stage). The exception goes to the trace and never to the
    screen — a stack trace on a projector teaches nobody anything and costs trust."""
    if _model_quota(exc):
        emit("turn.model_quota", {"error": f"{type(exc).__name__}"}, "error")
        return prompts.QUOTA_TEMPLATE, "quota"
    emit("turn.error", {"error": f"{type(exc).__name__}: {exc}"[:400]}, "error")
    return prompts.BROKE_TEMPLATE, "error"


def _injection(session: ConversationSession, result: TurnResult) -> TurnResult:
    # Redacted from the transcript too: `session.transcript()` is fed to the NEXT gate call,
    # so leaving the payload there re-injects it into a model input one turn later.
    emit("guardrail.injection_blocked", {"reason_len": len(session.turns[-1]["text"])}, "guardrail")
    session.turns[-1] = {"role": "user", "text": "[intento de inyección — ignorado]"}
    result.text, result.stage = prompts.INJECTION_TEMPLATE, "injection"
    if session.advice is not None:
        # The SAME advice, unchanged. Identical products at identical prices are the proof
        # that the injection moved nothing.
        result.advice = session.advice
        result.text = f"{prompts.INJECTION_TEMPLATE}\n\n{session.presented}"
    return result


def _dead_end(verdict: CapabilityVerdict, result: TurnResult) -> TurnResult:
    dead_end = verdict.dead_end
    result.text = prompts.DEAD_END_TEMPLATE.format(
        statement=dead_end.statement if dead_end else "",
        tradeoff=dead_end.tradeoff if dead_end else "",
    )
    result.stage = "dead_end"
    # `needs_human` is the one kind that requires no check: nothing was retrieved, so there
    # is nothing to verify, and claiming otherwise would block on a check that could not
    # have run.
    result.advice = Advice(kind="needs_human", explanation=result.text)
    return result


async def _advise(
    session: ConversationSession,
    message: str,
    verdict: IntentVerdict,
    result: TurnResult,
    start: int,
) -> TurnResult:
    if not session.question:
        session.question = message
        session.discipline = verdict.discipline or session.discipline
    else:
        if session.completed(PRESENTATION):
            session.reopen()
        session.answers.append(message)

    # The history first: a read we could not make ends the turn before it spends the
    # storefront's much harsher limiter on a recommendation that is not going to happen.
    view = await _training(session)
    result.uncertainty = view.uncertainty
    if not view.outcome.is_conclusive:
        return _unread_training(view, result, start)

    snapshot = await read_snapshot()
    if not snapshot.outcome.is_ok:
        return _unread(snapshot, view, result, start)

    if not session.completed(INTERVIEW):
        if view.uncertainty.grounded:
            # Huella's whole point: the window answered, so nothing is asked. The stage is
            # marked done rather than skipped, so a resume does not re-decide it.
            session.complete(INTERVIEW)
            emit(
                "interview.skipped",
                {"activities": view.uncertainty.sample_size, "confidence": view.uncertainty.confidence},
            )
        else:
            session.questions = await _interview(session, view)
            session.complete(INTERVIEW)
            if session.questions:
                result.questions = tuple(session.questions)
                result.text = _questions_text(session.questions)
                result.stage = "questions"
                return result

    if not session.completed(REQUIREMENTS):
        await _requirements(session, view)
        session.complete(REQUIREMENTS)
        # The corrections are state now, so the view is rebuilt before anything reads it:
        # this is what "a correction updates state before the next generation" means in
        # code rather than in a prompt.
        view = _bind(session)
        result.uncertainty = view.uncertainty

    spec = AdviceSpec(
        question=session.question,
        discipline=session.discipline,
        budget=_budget(view.effective),
        requirements=view.effective,
    )

    if not session.completed(RETRIEVAL):
        await _retrieve(session, view)
        session.complete(RETRIEVAL)

    candidates = _candidates(session, snapshot)
    selection = await _select(session, spec, view, candidates)
    decision = _decide(session, snapshot, spec, view, selection, candidates)
    session.complete(SELECTION)

    # Before the presentation call, so a recommendation nothing verified never gets prose
    # written for it, and after it again so the bundle carries the scrub — KB §3.4.4.
    bundle = evidence.build(decision.advice, trace.since(start))
    if not bundle.accepted:
        return _blocked(decision, bundle, result)

    text = await _present(session, spec, view, decision)
    advice = decision.advice.model_copy(update={"explanation": text})
    session.complete(PRESENTATION)
    session.advice, session.presented = advice, text

    result.advice = advice
    result.evidence = evidence.build(advice, trace.since(start))
    result.text, result.stage = text, advice.kind
    return result


def _unread_training(
    view: tools.TrainingView, result: TurnResult, start: int
) -> TurnResult:
    """Strava did not answer. No stage is marked done, nothing is retrieved and nothing is
    recommended — a refusal upstream is not a training history, and a recommendation built
    on one would be a claim about an athlete nobody read."""
    guardrails.check_buy_nothing((), retrieval_conclusive=False)
    template = {
        ToolOutcome.RATE_LIMITED: prompts.STRAVA_LIMITED_TEMPLATE,
        ToolOutcome.NEEDS_HUMAN: prompts.STRAVA_RECONNECT_TEMPLATE,
    }.get(view.outcome, prompts.STRAVA_UNREACHABLE_TEMPLATE)
    text = template.format(detail=view.detail or view.uncertainty.statement)

    advice = Advice(
        kind="insufficient_evidence",
        explanation=text,
        caveats=(view.uncertainty.statement,),
    )
    result.advice = advice
    result.evidence = evidence.build(advice, trace.since(start))
    result.text, result.stage = text, "strava_unread"
    return result


def _unread(
    snapshot: tools.Snapshot, view: tools.TrainingView, result: TurnResult, start: int
) -> TurnResult:
    """The catalogue could not be read. No stage is marked done, so the next turn picks up
    here rather than presenting an empty recommendation."""
    guardrails.check_buy_nothing((), retrieval_conclusive=False)
    text = (
        prompts.RATE_LIMITED_TEMPLATE
        if snapshot.outcome is ToolOutcome.RATE_LIMITED
        else prompts.UNREACHABLE_TEMPLATE
    )
    advice = Advice(
        kind="insufficient_evidence",
        explanation=text,
        caveats=tuple(c for c in (snapshot.detail, view.uncertainty.statement) if c),
    )
    result.advice = advice
    result.evidence = evidence.build(advice, trace.since(start))
    result.text, result.stage = text, advice.kind
    return result


# A bundle's own prose is English on purpose — it is an engineering artifact read in a PR.
# What a person is told has to be Spanish and about their case, so the check names are
# translated here rather than pasted.
_CHECKS_ES: dict[str, str] = {
    "provenance": "que cada dato saliera del catálogo",
    "stock": "que estuviera disponible",
    "budget": "la cuenta contra tu presupuesto",
    "local_availability": "que se venda en Colombia",
    "buy_nothing": "que 'no compres nada' siguiera siendo una respuesta posible",
}


def _blocked(decision: _Decision, bundle: EvidenceBundle, result: TurnResult) -> TurnResult:
    emit(
        "guardrail.evidence_blocked",
        {"kind": decision.advice.kind, "blocking": list(bundle.blocking)},
        "guardrail",
    )
    # `fail` and `not_run` are different sentences and the person gets the right one. A check
    # that RAN and disagreed is not a check nobody could run, and saying "me faltó comprobar
    # X" about a budget check that returned "nothing fits, the cheapest is $1.899.000" is a
    # false statement about our own reasoning. Every other layer already draws this line —
    # evidence.py returns None only for `not_run`, and theme.OUTCOME_COLOR gives `fail` the
    # flag and `not_run` the secondary grey — and this was the one place that collapsed it.
    failed = [_CHECKS_ES[c.name] for c in bundle.checks if c.outcome == "fail" and c.name in _CHECKS_ES]
    unrun = [_CHECKS_ES[c.name] for c in bundle.checks if c.outcome == "not_run" and c.name in _CHECKS_ES]
    said = []
    if failed:
        said.append(f"Revisé {', '.join(failed)} y {'no se cumple' if len(failed) == 1 else 'no se cumplen'}.")
    if unrun:
        said.append(f"Me faltó comprobar {', '.join(unrun)}.")
    reason = " ".join(said) if said else "No pude comprobar lo que encontré."
    text = prompts.UNVERIFIED_TEMPLATE.format(reason=reason)

    result.advice = Advice(
        kind="insufficient_evidence",
        explanation=text,
        unavailable_devices=decision.advice.unavailable_devices,
        caveats=decision.advice.caveats,
        spec_digest=decision.advice.spec_digest,
    )
    result.evidence = bundle
    result.text, result.stage = text, "blocked"
    return result


async def _demo() -> None:
    session = ConversationSession(key="demo")
    for message in (
        "¿qué me sirve para lo que estoy entrenando? Presupuesto de dos millones.",
        "mi salida más larga es de cuatro horas, no de dos",
    ):
        print(f"\n\n>>> {message}\n")
        result = await run_turn(message, session)
        print(result.text)
        print(f"\n--- {result.stage} · {len(result.advice.items) if result.advice else 0} productos")
        if result.uncertainty is not None:
            print(f"--- incertidumbre: {result.uncertainty.confidence} {result.uncertainty.flags}")
        if result.evidence is not None:
            print(result.evidence.render())


if __name__ == "__main__":
    asyncio.run(_demo())
