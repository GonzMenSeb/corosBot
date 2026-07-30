"""The turn: one job per model call, and a stage is skipped only if it finished.

  1  intent gate       structured, no tools    -> IntentVerdict
  2  interview         structured, no tools    -> at most three questions, asked once
  3  requirements      structured, no tools    -> Requirement tuple, keys validated in code
  4..n retrieval       function tools ONLY     -> real products, from the turn's snapshot
  n+1 selection        structured, no tools    -> product ids; the item is rebuilt from the feed
  n+2 presentation     text                    -> prose only, and only on the recommend path

Four things here are worth reading before changing anything.

**Resumption is keyed on completion, not on emptiness.** `session.done` records the
stages that finished, so an interview that asked nothing is not an interview that never
ran. The observed failure this prevents: a turn that dies during retrieval — a storefront
429 is the case that happens — resuming into selection with no requirements and
presenting an empty recommendation as though the catalogue were empty.

**Termination is governed by verification, never by the model deciding it is done**
(KB §3.4.4). `evidence.build` reads the turn's trace and refuses a recommendation whose
required checks left no event behind, and it runs BEFORE the presentation call: a
recommendation nothing verified never gets prose written for it, and the turn stays open
so the next one retries. The bundle is rebuilt afterwards so the `prose` row reflects the
scrub that had not happened yet.

**The four honest refusals are copy, not generated text.** "Buy nothing", "COROS
Colombia does not sell that", "I could not read the catalogue" and the capability dead
ends are rendered from typed verdicts through `prompts.py`. Only a recommendation costs a
presentation call. A generated refusal is a refusal that can drift, and these are the four
sentences a person is most likely to be lied to about.

**A response schema is never a core model.** Verified live, 30 jul 2026: `extra="forbid"`
renders `additionalProperties` into the schema and the API answers 400 INVALID_ARGUMENT,
`Unknown name "additional_properties"`. Every policy model in `coros_core.models` sets it
by design, so the wire models below are plain and are validated into the frozen ones in
code — which is where a key outside the allowlist gets dropped anyway.

Unlike DecaBot's loop this one imports `catalog` directly instead of matching exception
class names: there is one upstream, the storefront read happens here rather than inside a
swappable backend, and the model-facing tools cannot reach any other.

Run one turn standalone:  PYTHONPATH=.:packages:apps/brujula ./.venv/bin/python -m brujula.agent.loop
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, get_args

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from brujula.agent import prompts, tools
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

MAX_TOOL_CALLS_PER_TURN = 6
MAX_MODEL_CALLS = 25
MAX_TOOL_RETRIES = 2
RETRY_BACKOFF = 0.5

MAX_QUESTIONS = 3
CANDIDATE_CHARS = 60_000
SUMMARY_CHARS = 1_500
MESSAGE_CHARS = 2_000

INTERVIEW = "interview"
REQUIREMENTS = "requirements"
RETRIEVAL = "retrieval"
SELECTION = "selection"
PRESENTATION = "presentation"

# A message that arrives after a finished recommendation reopens the case: the person is
# changing something, and answering from requirements they have since replaced is worse
# than spending the calls again.
REOPENED: tuple[str, ...] = (REQUIREMENTS, RETRIEVAL, SELECTION, PRESENTATION)

REQUIREMENT_KEYS: tuple[str, ...] = get_args(RequirementKey)
PROVENANCES: tuple[str, ...] = get_args(Provenance)


# ── what the model is allowed to hand back ────────────────────────────────────


class _Questions(BaseModel):
    questions: list[str] = Field(default_factory=list)


class _RawRequirement(BaseModel):
    key: str = ""
    value: str | int | bool = ""
    source: str = "assumed"
    derived: bool = False
    sample_size: int = 0
    window_days: int = 0
    rationale: str = ""


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


# Every schema handed to Gemini, so `tests/test_brujula_agent.py` can check all of them
# for the `additionalProperties` 400 in one place.
SCHEMAS: tuple[type[BaseModel], ...] = (IntentVerdict, _Questions, _Requirements, _Selection)


class TurnResult(BaseModel):
    text: str = ""
    intent: Intent = "advice"
    stage: str = ""
    questions: tuple[str, ...] = ()
    advice: Advice | None = None
    evidence: EvidenceBundle | None = None
    error: str = ""


@dataclass
class ConversationSession:
    """One conversation. `done` is the whole resumption mechanism — see the docstring."""

    turns: list[dict[str, str]] = field(default_factory=list)
    question: str = ""
    discipline: str = ""
    answers: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    requirements: tuple[Requirement, ...] = ()
    seen: list[str] = field(default_factory=list)
    # Retrieval reached the end of what it asked for. False is not "nothing matched": it
    # is "we do not know", and it is what stops a refusal being reported as an inventory.
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
        self.done -= set(REOPENED)
        self.requirements = ()
        self.seen = []
        self.conclusive = False
        self.capped = False
        self.advice = None
        self.presented = ""

    def transcript(self, limit: int = 6) -> str:
        return "\n".join(f"{t['role']}: {t['text'][:400]}" for t in self.turns[-limit:]) or "(nada)"

    def said(self) -> str:
        return " ".join([self.question, *self.answers])


class _BudgetSpent(Exception):
    """The conversation's model-call budget. Raised instead of the 26th call."""


class _Unreadable(Exception):
    """A structured stage whose JSON did not validate. Fails the turn rather than
    substituting a default: a fabricated stage output is indistinguishable downstream
    from a real one."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"{stage} returned something this loop could not read")
        self.stage = stage


class _NoCapability(Exception):
    def __init__(self, verdict: CapabilityVerdict) -> None:
        super().__init__(f"no tool serves {verdict.need!r} on {verdict.surface}")
        self.verdict = verdict


def _model_quota(exc: BaseException) -> bool:
    """Gemini's own 429. Keyed on the HTTP code rather than on the SDK's exception class
    so it survives google-genai renaming one."""
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
    """`response.parsed` is silently None whenever validation fails — google-genai
    swallows the ValidationError — so a stage that broke would be indistinguishable from
    one that answered with nothing. The raw text is validated here instead."""
    try:
        return schema.model_validate_json(response.text or "")
    except (ValidationError, ValueError) as exc:
        emit("stage.unreadable", {"stage": stage, "error": str(exc)[:300]}, "error")
        raise _Unreadable(stage) from exc


# ── the turn's one storefront read ────────────────────────────────────────────


async def read_snapshot() -> tools.Snapshot:
    """One request per turn, and every tool answers from it. A second read would spend
    the harshest limiter in the system — measured at a handful of requests before an
    IP-level lockout that outlasts the conversation — to re-fetch what we are holding."""
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
    """The tool surface, taken from the capability map rather than hardcoded. An empty
    map is a typed dead end here, never a retrieval stage with no tools — a model asked
    to retrieve with nothing to retrieve with answers from memory."""
    verdict = capability.check_capability(
        CapabilityRequest(need="product_recommendation", surface="brujula")
    )
    if verdict.is_dead_end:
        raise _NoCapability(verdict)
    allowed = {t.value for t in verdict.tools}
    return [d for d in tools.DECLARATIONS if d.name in allowed]


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


# ── call 2 · the interview ────────────────────────────────────────────────────


def _groups() -> str:
    listed = tools.snapshot()
    if listed is None or not listed.outcome.is_ok:
        return "(no se pudo leer)"
    return "\n".join(
        f"{g.handle} — {g.title}: {sum(1 for p in listed.visible if g.holds(p))} productos"
        for g in tools.GROUPS
    )


async def _interview(session: ConversationSession) -> list[str]:
    response = await _model(
        session,
        contents=prompts.INTERVIEW_PROMPT.format(
            message=session.question[:MESSAGE_CHARS],
            discipline=session.discipline or "(no la dijo)",
            known="\n".join(session.answers) or "(nada todavía)",
            groups=_groups(),
        ),
        config=_cfg(response_mime_type="application/json", response_schema=_Questions),
    )
    asked = _parse(response, _Questions, INTERVIEW)
    questions = [q.strip() for q in asked.questions if q.strip()][:MAX_QUESTIONS]
    emit("questions.asked", {"count": len(questions)})
    return questions


# ── call 3 · requirements ─────────────────────────────────────────────────────


async def _requirements(session: ConversationSession) -> tuple[Requirement, ...]:
    response = await _model(
        session,
        contents=prompts.REQUIREMENT_PROMPT.format(
            message=session.question[:MESSAGE_CHARS],
            answers="\n".join(session.answers) or "(ninguna)",
            discipline=session.discipline or "(no la dijo)",
            keys=", ".join(REQUIREMENT_KEYS),
        ),
        config=_cfg(response_mime_type="application/json", response_schema=_Requirements),
    )
    raw = _parse(response, _Requirements, REQUIREMENTS)

    kept: list[Requirement] = []
    rejected: list[str] = []
    for row in raw.requirements:
        data = row.model_dump()
        # A label outside `Provenance` is a wrong word for a real requirement; a key
        # outside the allowlist is a requirement this system has no way to check.
        data["source"] = data["source"] if data["source"] in PROVENANCES else "assumed"
        try:
            kept.append(Requirement(**data))
        except ValidationError:
            rejected.append(row.key)

    if rejected:
        emit(
            "guardrail.requirement_rejected",
            # The key only when it is our own vocabulary: an invented one is text derived
            # from what a person typed, and evidence bundles paste payloads back into a
            # model's context.
            {"rejected": len(rejected), "keys": [k for k in rejected if k in REQUIREMENT_KEYS]},
            "guardrail",
        )
    emit("requirements.built", {"keys": [r.key for r in kept], "count": len(kept)})
    return tuple(kept)


def _budget(requirements: Sequence[Requirement]) -> Budget:
    """`budget_minor` is centavos by prompt and by `money.py`'s contract. A value that is
    not a whole number of them is dropped rather than guessed at: a budget read wrong by
    a factor of a hundred reports "nothing fits" about a catalogue full of things that do."""
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
            # The name only when it is a name this system knows — `create_cart` is the
            # one worth seeing, and anything else is text the model made up.
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
            # The class, never the message: a tool argument is text derived from a
            # person's own words and an exception repeats it back.
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
                        f"{name} falló {MAX_TOOL_RETRIES + 1} veces. No se consultó el catálogo: "
                        "esto no es que no haya nada."
                    ),
                )
            await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
    raise AssertionError("unreachable: every attempt returns or retries")


async def _retrieve(session: ConversationSession) -> None:
    prompt = prompts.RETRIEVE_PROMPT.format(
        requirements=json.dumps(
            [{"key": r.key, "value": r.value} for r in session.requirements], ensure_ascii=False
        ),
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

        # Every function_call in a turn needs a matching response part, in ONE Content,
        # or the next request 400s.
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
    ordered = [by_id[i] for i in dict.fromkeys(session.seen) if i in by_id]
    return ordered


async def _select(
    session: ConversationSession, spec: AdviceSpec, candidates: Sequence[CatalogProduct]
) -> _Selection:
    response = await _model(
        session,
        contents=prompts.SELECT_PROMPT.format(
            requirements=json.dumps(
                [{"key": r.key, "value": r.value, "source": r.source} for r in spec.requirements],
                ensure_ascii=False,
            ),
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

    caveats = tuple(c for c in (budget.message if kind == "recommend" else "",) if c)
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
    session: ConversationSession, spec: AdviceSpec, decision: _Decision
) -> str:
    advice = decision.advice
    if advice.kind == "not_sold_locally":
        return prompts.NOT_SOLD_TEMPLATE.format(
            device=_names(decision.unavailable),
            tradeoff="\n\n".join(dict.fromkeys(u.tradeoff for u in decision.unavailable)),
        )
    if advice.kind == "buy_nothing":
        return prompts.BUY_NOTHING_TEMPLATE.format(
            reason=decision.reason or "de lo que revisé, nada resuelve lo que me contaste"
        )
    if advice.kind != "recommend":
        return prompts.UNREACHABLE_TEMPLATE

    response = await _model(
        session,
        contents=prompts.PRESENT_PROMPT.format(
            message=session.question[:MESSAGE_CHARS],
            requirements=json.dumps(
                [{"key": r.key, "value": r.value} for r in spec.requirements], ensure_ascii=False
            ),
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
    # The one guardrail with no structural equivalent: retrieval stops invented PRODUCTS,
    # nothing stops invented PROPERTIES of real ones.
    allowed = [spec.budget.max_total_minor] if spec.budget.max_total_minor is not None else []
    return guardrails.scrub_prose((response.text or "").strip(), advice.items, allowed_minor=allowed)


def _questions_text(questions: Sequence[str]) -> str:
    return prompts.CLARIFY_TEMPLATE.format(
        question="\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))
    )


# ── the turn ──────────────────────────────────────────────────────────────────


async def run_turn(user_message: str, session: ConversationSession) -> TurnResult:
    start = trace.mark()
    # A turn that never reads the feed must not answer from the previous turn's.
    tools.bind_snapshot(None)
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
    except catalog.CatalogUnavailable as exc:
        # Everything the turn reached is still on the session and `_advise` resumes from
        # whichever stage did not finish, so this is a pause and not a dead end.
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
    """(what the person reads, the stage). The exception goes to the trace and never to
    the screen — a stack trace on a projector teaches nobody anything and costs trust."""
    if _model_quota(exc):
        emit("turn.model_quota", {"error": f"{type(exc).__name__}"}, "error")
        return prompts.QUOTA_TEMPLATE, "quota"
    emit("turn.error", {"error": f"{type(exc).__name__}: {exc}"[:400]}, "error")
    return prompts.BROKE_TEMPLATE, "error"


def _injection(session: ConversationSession, result: TurnResult) -> TurnResult:
    # Redacted from the transcript too: `session.transcript()` is fed to the NEXT gate
    # call, so leaving the payload there re-injects it into a model input one turn later.
    emit("guardrail.injection_blocked", {"reason_len": len(session.turns[-1]["text"])}, "guardrail")
    session.turns[-1] = {"role": "user", "text": "[intento de inyección — ignorado]"}
    result.text, result.stage = prompts.INJECTION_TEMPLATE, "injection"
    if session.advice is not None:
        # The SAME advice, unchanged. Identical products at identical prices are the
        # proof that the injection moved nothing.
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
    # `needs_human` is the one kind that requires no check: nothing was retrieved, so
    # there is nothing to verify, and claiming otherwise would block on a check that
    # could not have run.
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

    snapshot = await read_snapshot()
    if not snapshot.outcome.is_ok:
        return _unread(snapshot, result, start)

    if not session.completed(INTERVIEW):
        session.questions = await _interview(session)
        session.complete(INTERVIEW)
        if session.questions:
            result.questions = tuple(session.questions)
            result.text = _questions_text(session.questions)
            result.stage = "questions"
            return result

    if not session.completed(REQUIREMENTS):
        session.requirements = await _requirements(session)
        session.complete(REQUIREMENTS)

    spec = AdviceSpec(
        question=session.question,
        discipline=session.discipline,
        budget=_budget(session.requirements),
        requirements=session.requirements,
    )

    if not session.completed(RETRIEVAL):
        await _retrieve(session)
        session.complete(RETRIEVAL)

    candidates = _candidates(session, snapshot)
    selection = await _select(session, spec, candidates)
    decision = _decide(session, snapshot, spec, selection, candidates)
    session.complete(SELECTION)

    # Before the presentation call, so a recommendation nothing verified never gets prose
    # written for it, and after it again so the bundle carries the scrub — KB §3.4.4.
    bundle = evidence.build(decision.advice, trace.since(start))
    if not bundle.accepted:
        return _blocked(decision, bundle, result)

    text = await _present(session, spec, decision)
    advice = decision.advice.model_copy(update={"explanation": text})
    session.complete(PRESENTATION)
    session.advice, session.presented = advice, text

    result.advice = advice
    result.evidence = evidence.build(advice, trace.since(start))
    result.text, result.stage = text, advice.kind
    return result


def _unread(snapshot: tools.Snapshot, result: TurnResult, start: int) -> TurnResult:
    """The catalogue could not be read. No stage is marked done, so the next turn picks
    up here rather than presenting an empty recommendation."""
    guardrails.check_buy_nothing((), retrieval_conclusive=False)
    text = (
        prompts.RATE_LIMITED_TEMPLATE
        if snapshot.outcome is ToolOutcome.RATE_LIMITED
        else prompts.UNREACHABLE_TEMPLATE
    )
    advice = Advice(kind="insufficient_evidence", explanation=text, caveats=(snapshot.detail,))
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
    # that RAN and disagreed is not a check nobody could run: saying "me faltó comprobar la
    # cuenta contra tu presupuesto" about a budget check that returned "nothing fits, the
    # cheapest APEX 4 is $1.899.000" describes our own reasoning falsely. Every other layer
    # already draws this line — evidence.py returns None only for `not_run`, and
    # theme.OUTCOME_COLOR gives `fail` the flag and `not_run` the secondary grey — and this
    # was the one place that collapsed the two. Found by scripts/eval_baseline.py.
    failed = [
        _CHECKS_ES[c.name] for c in bundle.checks if c.outcome == "fail" and c.name in _CHECKS_ES
    ]
    unrun = [
        _CHECKS_ES[c.name] for c in bundle.checks if c.outcome == "not_run" and c.name in _CHECKS_ES
    ]
    said = []
    if failed:
        verb = "no se cumple" if len(failed) == 1 else "no se cumplen"
        said.append(f"Revisé {', '.join(failed)} y {verb}.")
    if unrun:
        said.append(f"Me faltó comprobar {', '.join(unrun)}.")
    reason = " ".join(said) if said else "No pude comprobar lo que encontré."
    text = prompts.UNVERIFIED_TEMPLATE.format(reason=reason)

    result.advice = Advice(
        kind="insufficient_evidence",
        explanation=text,
        unavailable_devices=decision.advice.unavailable_devices,
        spec_digest=decision.advice.spec_digest,
    )
    result.evidence = bundle
    result.text, result.stage = text, "blocked"
    return result


async def _demo() -> None:
    session = ConversationSession()
    for message in (
        "corro trail, salidas de tres horas, presupuesto de dos millones. No preguntes, recomienda.",
        "¿y una correa para mi APEX 4 de 46?",
    ):
        print(f"\n\n>>> {message}\n")
        result = await run_turn(message, session)
        print(result.text)
        print(f"\n--- {result.stage} · {len(result.advice.items) if result.advice else 0} productos")
        if result.evidence is not None:
            print(result.evidence.render())


if __name__ == "__main__":
    asyncio.run(_demo())
