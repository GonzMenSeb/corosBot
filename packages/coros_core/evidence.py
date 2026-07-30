"""The bundle an accepted recommendation carries.

KB `Code As Agent Harness.pdf` §5.2.2: "make every accepted action carry an evidence
bundle containing the checks run, the assumptions preserved, the untested regions, and
the remaining risks", with each artifact declaring "what it verifies, what it cannot
verify, and what confidence it provides". That is the shape below, one field each.

Everything here is derived from `trace.py`'s events and from the advice — never from
anything a stage says about itself. A check that emitted no event did not run, and a
recommendation missing a required check is `accepted=False`, which is what makes
termination governed by verification rather than by the model deciding it is finished
(KB §3.4.4). The agent cannot fake this without emitting the event, and the events are
emitted inside `guardrails.py`.

Two readings that are easy to get backwards:

  * **An outcome is about the advice agreeing with the verdict, not about the verdict
    being good news.** An over-budget selection reported as over budget is a `pass` with
    a risk attached; an over-budget selection presented as if it fit is a `fail`. Killing
    the honest bad answer is the failure mode, not the goal.
  * **Silence is not a pass.** `scrub_prose` emits only when it excises something, so no
    `guardrail.prose` event means either clean prose or no scrub at all, and the trace
    cannot tell them apart. That ambiguity is reported as an untested region instead of
    being resolved in whichever direction flatters the run.

The prose here is English: this is an engineering artifact read in a PR or pasted into a
model, and the Spanish in a verdict's `message` is the copy shown to a person.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from coros_core import trace
from coros_core.models import Advice, AdviceKind
from coros_core.money import minor_to_display

Outcome = Literal["pass", "fail", "not_run"]
Confidence = Literal["high", "medium", "none"]

# Aligns a check's continuation lines under what it verifies.
_GUTTER = " " * 31

# A failure to reach either upstream. Whichever of these appears, stock and price
# freshness stop being verified facts for the rest of the turn.
_RETRIEVAL_FAILURES = (
    "catalog.rate_limited",
    "catalog.unavailable",
    "ucp.rate_limited",
    "ucp.tool_error",
)


class Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    event: str
    outcome: Outcome
    verifies: str
    cannot_verify: str
    confidence: Confidence
    detail: str = ""


class Assumption(BaseModel):
    """`held=None` is "nothing re-derived this", which is not the same claim as True."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str
    basis: str
    held: bool | None = None


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool = False
    kind: AdviceKind = "recommend"
    spec_digest: str = ""
    item_count: int = 0
    checks: tuple[Check, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    untested: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    blocking: tuple[str, ...] = ()

    def check(self, name: str) -> Check:
        found = next((c for c in self.checks if c.name == name), None)
        if found is None:
            raise KeyError(f"no check named {name!r}; declared: {[c.name for c in self.checks]}")
        return found

    def render(self) -> str:
        items = f"{self.item_count} item" + ("" if self.item_count == 1 else "s")
        head = f"{self.kind} · {items} · spec {self.spec_digest[:8] or '(none)'}"
        lines = [f"# Evidence bundle — {head}", "ACCEPTED" if self.accepted else "BLOCKED"]
        lines += [f"  - {reason}" for reason in self.blocking]

        lines.append("")
        lines.append("checks")
        for check in self.checks:
            lines.append(f"  {check.outcome:<8} {check.name:<20} {check.verifies}")
            lines.append(f"{_GUTTER} confidence {check.confidence} · {check.detail}")
            lines.append(f"{_GUTTER} cannot verify: {check.cannot_verify}")

        lines.append("")
        lines.append("assumptions")
        for assumption in self.assumptions:
            mark = {True: "held", False: "BROKEN", None: "unchecked"}[assumption.held]
            lines.append(f"  {mark:<10} {assumption.statement}")
            lines.append(f"  {'':<10} pinned by {assumption.basis}")

        for title, entries in (("untested", self.untested), ("risks", self.risks)):
            lines.append("")
            lines.append(title)
            lines += [f"  - {entry}" for entry in entries] or ["  (none)"]

        return "\n".join(lines) + "\n"


class _Declared(NamedTuple):
    name: str
    event: str
    verifies: str
    cannot_verify: str
    confidence: Confidence
    silence: str


_DECLARED: tuple[_Declared, ...] = (
    _Declared(
        "provenance",
        "guardrail.provenance",
        "every rendered field was rebuilt from the feed this turn fetched",
        "whether the reasoning in a rationale is sound — it checks fields, not judgement",
        "high",
        "no provenance event: nothing confirms the rendered fields came from the feed",
    ),
    _Declared(
        "stock",
        "guardrail.stock",
        "availability was read out of the feed, not off the model's candidate",
        "stock between this read and checkout — a variant can sell out inside the turn",
        "high",
        "no stock event: availability for these items is unverified",
    ),
    _Declared(
        "budget",
        "guardrail.budget",
        "the total is integer arithmetic in minor units, done in code",
        "shipping, taxes and promotions — the feed price is the only number in the sum",
        "high",
        "no budget event: the total was never added up outside the model",
    ),
    _Declared(
        "local_availability",
        "guardrail.local_availability",
        "a device COROS Colombia does not sell is named, never substituted",
        "a device nobody named and no candidate matched — it answers for the registry's 14",
        "high",
        "no local-availability event: an absent watch could have been swapped silently",
    ),
    _Declared(
        "buy_nothing",
        "guardrail.buy_nothing",
        "'buy nothing' was reachable, and an unfinished retrieval is not 'nothing fits'",
        "whether the person needs the thing at all — it verifies the outcome was available",
        "high",
        "no buy-nothing event: recommending nothing may not have been reachable",
    ),
    _Declared(
        "prose",
        "guardrail.prose",
        "a spec figure in prose appears in a retrieval-derived field or is excised",
        "silence — a clean scrub emits nothing, so this cannot distinguish clean prose "
        "from prose nobody scrubbed",
        "medium",
        "prose: the scrub leaves no event when it finds nothing, so either the prose was "
        "clean or it was never scrubbed",
    ),
)

_REQUIRED: dict[AdviceKind, tuple[str, ...]] = {
    "recommend": ("provenance", "stock", "budget", "local_availability", "buy_nothing"),
    "buy_nothing": ("buy_nothing",),
    "not_sold_locally": ("local_availability",),
    "insufficient_evidence": ("buy_nothing",),
    "needs_human": (),
}

_Verdict = tuple[Outcome, str, tuple[str, ...]]


def _ids(rows: Any, key: str = "product_id") -> set[str]:
    return {str(r.get(key) or "") for r in rows or [] if isinstance(r, dict)}


def _provenance(payload: dict[str, Any], advice: Advice) -> _Verdict:
    cleared = int(payload.get("renderable") or 0)
    dropped, mismatches = payload.get("dropped") or [], payload.get("mismatches") or []
    rendered = {i.product_id for i in advice.items}

    reasons = []
    kept = sorted(rendered & _ids(dropped))
    if kept:
        reasons.append(f"provenance dropped {', '.join(kept)} and the advice still renders it")
    if len(advice.items) > cleared:
        reasons.append(
            f"the advice renders {len(advice.items)} items and provenance cleared {cleared}"
        )
    detail = f"{cleared} cleared · {len(dropped)} dropped · {len(mismatches)} field mismatches"
    return ("fail" if reasons else "pass"), detail, tuple(reasons)


def _stock(payload: dict[str, Any], advice: Advice) -> _Verdict:
    confirmed = int(payload.get("confirmed") or 0)
    rejected = payload.get("rejected") or []
    rendered = {i.product_id for i in advice.items}

    reasons = []
    kept = sorted(rendered & _ids(rejected))
    if kept:
        reasons.append(
            f"the stock check rejected {', '.join(kept)} and the advice still recommends it"
        )
    if len(advice.items) > confirmed:
        reasons.append(
            f"the advice recommends {len(advice.items)} items and the stock check "
            f"confirmed {confirmed}"
        )
    return (
        ("fail" if reasons else "pass"),
        f"{confirmed} confirmed in stock · {len(rejected)} rejected",
        tuple(reasons),
    )


def _budget(payload: dict[str, Any], advice: Advice) -> _Verdict:
    cap = payload.get("budget_minor")
    total = int(payload.get("total_minor") or 0)
    over = int(payload.get("over_by_minor") or 0)

    reasons = []
    if payload.get("nothing_fits") and advice.items:
        reasons.append(
            f"the budget verdict says nothing fits and the advice recommends "
            f"{len(advice.items)} items"
        )
    detail = f"{minor_to_display(total)} selected"
    if isinstance(cap, int):
        detail += f" of {minor_to_display(cap)}"
        if over:
            detail += f", over by {minor_to_display(over)}"
    else:
        detail += ", no budget stated"
    return ("fail" if reasons else "pass"), detail, tuple(reasons)


def _local_availability(payload: dict[str, Any], advice: Advice) -> _Verdict:
    unavailable = payload.get("unavailable") or []
    slugs = _ids(unavailable, "slug")
    unnamed = sorted(slugs - set(advice.unavailable_devices))

    reasons = []
    if unnamed:
        reasons.append(
            f"the local-availability check found {', '.join(unnamed)} is not sold in "
            "Colombia and the advice does not carry it in unavailable_devices, which holds "
            "registry slugs"
        )
    detail = f"{len(payload.get('cleared') or [])} cleared · {len(slugs)} not sold locally"
    return ("fail" if reasons else "pass"), detail, tuple(reasons)


def _buy_nothing(payload: dict[str, Any], advice: Advice) -> _Verdict:
    reason = str(payload.get("reason") or "")
    considered = int(payload.get("considered") or 0)

    reasons = []
    if advice.kind == "recommend" and reason == "inconclusive":
        reasons.append(
            "the retrieval was inconclusive and the advice recommends products anyway — "
            "a refused request is not an inventory"
        )
    elif advice.kind == "recommend" and payload.get("buy_nothing"):
        reasons.append(
            f"the buy-nothing verdict is {reason!r} and the advice recommends products anyway"
        )
    detail = f"{considered} considered" + (f" · {reason}" if reason else "")
    return ("fail" if reasons else "pass"), detail, tuple(reasons)


def _prose(payload: dict[str, Any], _advice: Advice) -> _Verdict:
    claims = payload.get("claims") or []
    kinds = ", ".join(str(k) for k in payload.get("kinds") or [])
    return "pass", f"{len(claims)} unbacked claims excised ({kinds})", ()


_EVALUATORS = {
    "provenance": _provenance,
    "stock": _stock,
    "budget": _budget,
    "local_availability": _local_availability,
    "buy_nothing": _buy_nothing,
    "prose": _prose,
}


def _latest(events: Sequence[trace.TraceEvent]) -> dict[str, dict[str, Any]]:
    """The operative verdict is the last one: a turn may re-run a check after a retry."""
    return {e.event: e.payload for e in events}


def _risks(latest: dict[str, dict[str, Any]], advice: Advice) -> tuple[str, ...]:
    out: list[str] = []

    provenance = latest.get("guardrail.provenance") or {}
    mismatches = provenance.get("mismatches") or []
    if mismatches:
        fields = sorted({str(m.get("field") or "") for m in mismatches})
        out.append(
            f"the model sent {len(mismatches)} field values that disagreed with the feed "
            f"({', '.join(fields)}); the feed's values were rendered and the claims discarded, "
            "but a stage that misstates a field will also misstate it in prose"
        )
    dropped = provenance.get("dropped") or []
    if dropped:
        out.append(f"{len(dropped)} proposed items are not backed by the feed and were dropped")

    rejected = (latest.get("guardrail.stock") or {}).get("rejected") or []
    if rejected:
        out.append(f"{len(rejected)} proposed items were rejected on availability")

    over = int((latest.get("guardrail.budget") or {}).get("over_by_minor") or 0)
    if over:
        out.append(f"the selection is over the stated budget by {minor_to_display(over)}")

    unavailable = (latest.get("guardrail.local_availability") or {}).get("unavailable") or []
    for device in unavailable:
        name = device.get("name") or device.get("slug")
        if device.get("straps_listed"):
            out.append(
                f"{name} straps are in the Colombian catalogue and the watch is not, so a "
                "search reads as availability — the copy has to carry the absence"
            )
        else:
            out.append(f"{name} is not sold in Colombia and the advice has to say so")

    claims = (latest.get("guardrail.prose") or {}).get("claims") or []
    if claims:
        out.append(
            f"{len(claims)} unbacked spec claims were excised from the prose; a stage that "
            "invents a property once will invent another"
        )

    for event in _RETRIEVAL_FAILURES:
        if event in latest:
            out.append(
                f"{event} fired this turn: a retry inside the cooldown fails without touching "
                "the network, so the next turn can legitimately have no catalogue at all"
            )

    if not advice.spec_digest:
        out.append(
            "no spec digest: this run cannot be replayed against the spec it ran under"
        )
    return tuple(out)


def build(advice: Advice, events: Sequence[trace.TraceEvent] | None = None) -> EvidenceBundle:
    """Read the turn's trace and say whether this advice may be presented.

    `events` defaults to everything the turn can see. Pass `trace.since(trace.mark())`
    from the top of the turn: a sink spans a session, and one turn's clearance is not the
    next turn's.
    """
    latest = _latest(events if events is not None else trace.current())
    required = _REQUIRED.get(advice.kind, ())

    checks: list[Check] = []
    blocking: list[str] = []
    untested: list[str] = []

    for spec in _DECLARED:
        payload = latest.get(spec.event)
        if payload is None:
            checks.append(
                Check(
                    name=spec.name,
                    event=spec.event,
                    outcome="not_run",
                    verifies=spec.verifies,
                    cannot_verify=spec.cannot_verify,
                    confidence="none",
                    detail=f"no {spec.event} event in this turn's trace",
                )
            )
            untested.append(spec.silence)
            if spec.name in required:
                blocking.append(
                    f"{spec.name} never ran: no {spec.event} event in this turn's trace"
                )
            continue

        outcome, detail, reasons = _EVALUATORS[spec.name](payload, advice)
        checks.append(
            Check(
                name=spec.name,
                event=spec.event,
                outcome=outcome,
                verifies=spec.verifies,
                cannot_verify=spec.cannot_verify,
                confidence=spec.confidence,
                detail=detail,
            )
        )
        blocking.extend(reasons)

    for event in _RETRIEVAL_FAILURES:
        if event in latest:
            untested.append(
                f"{event}: stock and price freshness for the rendered items, because the "
                "upstream did not answer this turn"
            )

    lost = trace.dropped()
    if lost:
        untested.append(
            f"{lost} events have fallen off the end of the process ring, so earlier checks "
            "in this session are no longer inspectable"
        )

    outcomes: dict[str, Outcome] = {c.name: c.outcome for c in checks}
    retrieval_failed = any(event in latest for event in _RETRIEVAL_FAILURES)
    assumptions = (
        Assumption(
            statement="Every price is an integer COP minor unit, converted at the boundary "
            "and nowhere else.",
            basis="packages/coros_core/money.py · the source scan in tests/test_money.py",
        ),
        Assumption(
            statement="Availability comes from the feed this turn fetched: a stock claim "
            "made on the model's candidate changes nothing.",
            basis="guardrails.check_stock · guardrail.stock",
            held=_held(outcomes["stock"]),
        ),
        Assumption(
            statement="Every rendered field was rebuilt from the feed; the model "
            "contributed an id and its reasoning.",
            basis="guardrails.check_provenance · guardrail.provenance",
            held=_held(outcomes["provenance"]),
        ),
        Assumption(
            statement="A device COROS Colombia does not sell is named, never substituted.",
            basis="guardrails.check_local_availability · AGENTS.md device registry",
            held=_held(outcomes["local_availability"]),
        ),
        Assumption(
            statement="Compatibility is curated per product from COROS's own words, never "
            "derived from a strap width.",
            basis="packages/coros_core/devices.py · the AST scan in tests/test_devices.py",
        ),
        Assumption(
            statement="The catalogue answered this turn — retrieval is live every turn and "
            "never cached.",
            basis="coros_core.catalog · the absence of a catalog/ucp failure event",
            held=False if retrieval_failed else None,
        ),
    )

    bundle = EvidenceBundle(
        accepted=not blocking,
        kind=advice.kind,
        spec_digest=advice.spec_digest,
        item_count=len(advice.items),
        checks=tuple(checks),
        assumptions=assumptions,
        untested=tuple(untested),
        risks=_risks(latest, advice),
        blocking=tuple(blocking),
    )
    trace.emit(
        "evidence.bundle",
        {
            "accepted": bundle.accepted,
            "kind": bundle.kind,
            "checks": outcomes,
            "blocking": list(bundle.blocking),
            "untested": len(bundle.untested),
            "risks": len(bundle.risks),
        },
        "guardrail",
    )
    return bundle


def _held(outcome: Outcome) -> bool | None:
    return None if outcome == "not_run" else outcome == "pass"
