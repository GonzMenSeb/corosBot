"""Does the guarded pipeline beat an unguarded one when both spend the same budget?

    PYTHONPATH=.:packages:apps/brujula ./.venv/bin/python scripts/eval_baseline.py

Offline, always. The catalogue is `fixtures/products.json` — recorded live from COROS
Colombia — and nothing here opens a socket, so this is runnable in CI and costs the
storefront's IP-scoped limiter nothing. There is no Strava in it at any depth: Strava is
paywalled per `AGENTS.md`, and an evaluation that blocked on a subscription would measure
whether somebody paid.

## The experiment

The model is the one part of a turn that needs the network, so it is **replayed, not
called**. Each item below carries a recorded model proposal in exactly the shape
`loop._Selection` produces — product ids, variant ids, a rationale — plus the prose the
presentation stage produced. **Both arms receive the identical proposal.** That is the
whole design: it isolates the deterministic spine, which is the only thing this repo
claims to guarantee ("a guardrail written in the prompt is a suggestion; a guardrail
written in Python is a guarantee"), and it means a difference between the arms cannot be
a lucky sample.

  AGENT     the shipped path. `loop._decide` — the same five checks in the same order
            both apps route through — then `evidence.build`, then `scrub_prose`.
  BASELINE  retrieval-grounded pass-through. Same snapshot, same tool calls, same
            proposal; joins each pick back to the feed by product id and renders the
            feed's own title, URL and price. Ambiguity resolved by taking the first
            variant. No stock check, no budget arithmetic, no absence naming, no
            buy-nothing, no prose scrub, no evidence bundle.

The baseline joins by id on purpose. The weaker baseline — render whatever JSON the model
emitted — loses on every field and proves nothing; this one already gets titles, URLs and
price *scale* right for free, so the agent has to win on the checks rather than on the
straw. See `docs/EVAL.md` for the argument in full.

## Equal budget

Every unit that is actually scarce here is metered, per arm, by a `Ledger` neither arm can
bypass: storefront reads (the harshest limiter in the system), retrieval tool calls
(`loop.MAX_TOOL_CALLS_PER_TURN`), and model calls (`loop.MAX_MODEL_CALLS`). The two arms
draw from separate ledgers and the harness **compares them and refuses to report a winner
if they differ** — an agent that wins by spending more has not won. The reason equality is
reachable at all is that `guardrails.py` is pure: no I/O, no clock, no model. The ledger is
how that is demonstrated rather than asserted.

## The six metrics are the six declared checks

Not invented for this file. `evidence.build` declares exactly six checks; each metric below
scores an arm on the axis one of those checks exists to guarantee, whether or not that arm
ran it. `DECLARED_CHECKS` is read back out of a real bundle at import, and the harness
refuses to start if the metric table and that list disagree — a seventh declared check
forces a seventh metric rather than being silently unmeasured.

Ground truth is the fixture, never an annotation. An item carries a need, a budget, a
snapshot mode, the replayed proposal and the replayed prose; every expectation is derived
from `fixtures/products.json` at scoring time.

**One field is an exception, and it is the one to distrust.** `Item.claimed_unavailable`
is the replayed model's `_Selection.unavailable_devices`. The baseline renders it verbatim
and nothing re-derives it, so it *alone* decides the baseline's `local_availability`
score: filling it on `absent-vertix-2` turns that metric from AGENT into a tie without
touching a fixture or a check. It is a recorded model output in exactly the sense the
picks are — and it is the single input here a maintainer could tune to change a verdict.
`docs/EVAL.md` §2, "the one assumption", argues each value it is set to.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from brujula.agent import loop, prompts, tools
from coros_core import catalog, evidence, guardrails, trace
from coros_core.models import Advice, AdviceItem, AdviceSpec, Budget, CatalogProduct
from coros_core.money import MoneyError, major_string_to_minor, minor_to_display
from coros_core.outcomes import ToolOutcome

REPO = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "products.json"

# What a live turn would spend. Offline the read is served off disk, but it is metered as
# the request it stands for — the point of the ledger is the comparison, not the clock.
CATALOG_READS_PER_TURN = 1


# ── the ledger both arms draw from ────────────────────────────────────────────


@dataclass
class Ledger:
    """Scarce units, metered per arm. Nothing reaches the feed or a tool except through
    `read_catalog` / `call_tool`, so a divergence is counted rather than trusted."""

    arm: str
    catalog_reads: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    replays: int = 0

    def read_catalog(self) -> None:
        self.catalog_reads += 1

    def call_tool(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > loop.MAX_TOOL_CALLS_PER_TURN:
            raise RuntimeError(
                f"{self.arm} asked for tool call {self.tool_calls}; the turn budget is "
                f"{loop.MAX_TOOL_CALLS_PER_TURN}. An arm that overspends is not being "
                "compared at equal budget."
            )

    def replay_model(self) -> None:
        """A replayed stage is counted where a model call would be, and charged nothing.

        Both arms replay the identical proposal, so `model_calls` stays 0 on both sides
        and `replays` is what says the stage happened at all rather than being skipped."""
        self.replays += 1

    def spent(self) -> tuple[int, int, int, int]:
        return (self.catalog_reads, self.tool_calls, self.model_calls, self.replays)

    def line(self) -> str:
        return (
            f"catalog_reads={self.catalog_reads} tool_calls={self.tool_calls} "
            f"model_calls={self.model_calls} replayed_model_stages={self.replays}"
        )

    def __add__(self, other: Ledger) -> Ledger:
        return Ledger(
            arm=self.arm,
            catalog_reads=self.catalog_reads + other.catalog_reads,
            tool_calls=self.tool_calls + other.tool_calls,
            model_calls=self.model_calls + other.model_calls,
            replays=self.replays + other.replays,
        )


# ── the replayed model output ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Pick:
    """One product the replayed model proposed.

    `claimed_*` is what the model asserted about it. `check_provenance` compares those
    against the feed and then discards them; the baseline has nowhere to compare, so they
    are what it renders whenever the join by product id fails."""

    product_id: str
    variant_id: str = ""
    rationale: str = ""
    claimed_title: str = ""
    claimed_url: str = ""
    claimed_price_minor: int | None = None


@dataclass(frozen=True)
class Item:
    """One evaluation case. Carries a situation and a replay — never an expected result."""

    id: str
    need: str
    picks: tuple[Pick, ...]
    prose: str
    tool_calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    budget_minor: int | None = None
    # "ok" serves the fixture; "rate_limited" serves the typed refusal a 429 produces.
    snapshot: str = "ok"
    # `_Selection` really has an `unavailable_devices` field, so a model CAN name an absent
    # watch unprompted — and `loop._decide` ignores what it says there and asks
    # `check_local_availability` instead. The baseline passes the claim through, which is
    # the only way it can get an absence right. Whether a given model does is the modelling
    # assumption in this file: see docs/EVAL.md, "the one assumption".
    claimed_unavailable: tuple[str, ...] = ()


# ── what an arm puts on screen ────────────────────────────────────────────────


@dataclass
class Rendered:
    arm: str
    kind: str = "none"
    items: tuple[AdviceItem, ...] = ()
    unavailable_devices: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    text: str = ""
    # None for the baseline: it builds no bundle, which is the difference, not an omission.
    accepted: bool | None = None
    blocking: tuple[str, ...] = ()

    @property
    def answered(self) -> bool:
        """Put products on screen. A refusal is an answer to a person and a non-answer to
        this counter — which is the trade the counter exists to expose."""
        return bool(self.items)

    @property
    def total_minor(self) -> int:
        return sum(i.price_minor for i in self.items)


# ── the catalogue, and the retrieval both arms pay for ────────────────────────


def load_products() -> tuple[CatalogProduct, ...]:
    """All 45, hidden included. The two gift-with-purchase shirts are on the feed and must
    be reachable by a proposal, or nothing can measure an arm rendering one."""
    return catalog.normalize(json.loads(FIXTURE.read_text()), include_hidden=True)


def _snapshot_for(item: Item, products: Sequence[CatalogProduct], ledger: Ledger) -> tools.Snapshot:
    ledger.read_catalog()
    if item.snapshot == "rate_limited":
        return tools.Snapshot(
            outcome=ToolOutcome.RATE_LIMITED,
            detail="COROS limitó las consultas y el catálogo no se pudo leer en este turno",
        )
    return tools.Snapshot(products=tuple(products))


async def _retrieve(
    item: Item, products: Sequence[CatalogProduct], ledger: Ledger
) -> tuple[tools.Snapshot, tuple[CatalogProduct, ...], bool]:
    """The retrieval half of a turn, identical for both arms and charged to both.

    `conclusive` is computed the way `loop._retrieve` computes it — from the tool outcomes
    and nothing else — because that flag is what stops "we could not look" being reported
    as "there is nothing"."""
    snapshot = _snapshot_for(item, products, ledger)
    tools.bind_snapshot(snapshot)

    seen: list[str] = []
    answered = 0
    conclusive = True
    for name, args in item.tool_calls:
        ledger.call_tool()
        result = await tools.DISPATCH[name](**args)
        answered += 1
        conclusive = conclusive and result.outcome.is_conclusive
        seen.extend(loop._harvest(result))

    by_id = {p.product_id: p for p in snapshot.visible}
    candidates = tuple(by_id[i] for i in dict.fromkeys(seen) if i in by_id)
    return snapshot, candidates, (conclusive and answered > 0)


# ── arm 1 · the baseline ──────────────────────────────────────────────────────


def _first_variant(product: CatalogProduct, variant_id: str) -> Any:
    """What a pipeline with no guardrail does with an unnamed variant: takes one.

    `guardrails._pick_variant` refuses here instead, which is the behaviour under test —
    on the APEX 4 the first variant is the 46 mm case and the 42 mm is $200.000 cheaper."""
    if variant_id:
        found = next((v for v in product.variants if v.variant_id == variant_id), None)
        if found is not None:
            return found
    return product.variants[0] if product.variants else None


def run_baseline(
    item: Item, snapshot: tools.Snapshot, _candidates: Sequence[CatalogProduct]
) -> Rendered:
    """Retrieval-grounded pass-through: join by product id, render the feed's fields, keep
    everything in the order proposed.

    No check runs. The only judgement it makes is the join, and it makes the generous
    version of it — see the module docstring on why the weaker baseline proves nothing.

    `_candidates` is taken and ignored on purpose: the baseline was handed everything
    retrieval found and consults none of it, which is the whole of `check_buy_nothing`'s
    absence."""
    by_id = {p.product_id: p for p in snapshot.products}
    rows: list[AdviceItem] = []

    for pick in item.picks:
        product = by_id.get(pick.product_id)
        if product is None:
            # Nothing to join to, so the model's own claim is all there is. This is the
            # one place the baseline renders unretrieved text, and it is not a strawman:
            # a pipeline with no provenance check has no other move.
            if not pick.claimed_url:
                continue
            rows.append(
                AdviceItem(
                    product_id=pick.product_id,
                    title=pick.claimed_title,
                    product_url=pick.claimed_url,
                    variant_id=pick.variant_id,
                    price_minor=pick.claimed_price_minor or 0,
                    rationale=pick.rationale,
                )
            )
            continue

        variant = _first_variant(product, pick.variant_id)
        if variant is None:
            continue
        rows.append(
            AdviceItem(
                product_id=product.product_id,
                title=product.title,
                product_url=product.product_url,
                image_url=product.image_url,
                variant_id=variant.variant_id,
                price_minor=variant.price_minor,
                rationale=pick.rationale,
            )
        )

    return Rendered(
        arm="baseline",
        kind="recommend" if rows else "buy_nothing",
        items=tuple(rows),
        unavailable_devices=item.claimed_unavailable,
        text=item.prose if rows else prompts.BUY_NOTHING_TEMPLATE.format(reason="no encontré nada"),
    )


# ── arm 2 · the shipped agent ─────────────────────────────────────────────────

# The one stage still re-implemented here, and the reason is structural: `loop._present`
# is a coroutine whose `recommend` branch calls the model, so its refusal branches cannot
# be reached without one. Everything it needs to build them — `_names`, the templates — is
# imported rather than copied, so a change to either shows up.
_KIND_TEMPLATE = {
    "buy_nothing": lambda d: prompts.BUY_NOTHING_TEMPLATE.format(
        reason=d.reason or "de lo que revisé, nada resuelve lo que me contaste"
    ),
    "not_sold_locally": lambda d: prompts.NOT_SOLD_TEMPLATE.format(
        device=loop._names(d.unavailable),
        tradeoff="\n\n".join(dict.fromkeys(u.tradeoff for u in d.unavailable)),
    ),
    "insufficient_evidence": lambda _d: prompts.UNREACHABLE_TEMPLATE,
}


def _from_turn(result: loop.TurnResult) -> Rendered:
    """A `TurnResult` off one of the loop's own refusal paths, in the shape a metric reads.

    Everything here is read back off the result the loop built. Rebuilding any of it from
    the inputs would be the re-implementation this function exists to remove."""
    advice, bundle = result.advice, result.evidence
    if advice is None or bundle is None:
        raise RuntimeError(
            f"loop returned stage {result.stage!r} with advice={advice} evidence={bundle}. "
            "The refusal paths this harness calls set both; nothing scored afterwards "
            "would be the shipped behaviour."
        )
    return Rendered(
        arm="agent",
        kind=advice.kind,
        items=advice.items,
        unavailable_devices=advice.unavailable_devices,
        caveats=advice.caveats,
        text=result.text,
        accepted=bundle.accepted,
        blocking=bundle.blocking,
    )


def run_agent(
    item: Item,
    snapshot: tools.Snapshot,
    candidates: Sequence[CatalogProduct],
    conclusive: bool,
    mark: int,
) -> Rendered:
    """`loop._decide` verbatim, then `evidence.build`, then the loop's own refusal paths.

    Calling the shipped privates is deliberate, and it is the whole of the claim
    `docs/EVAL.md` §9 makes for this file. A harness that re-implemented the five checks —
    or `_unread`, or `_blocked` — would drift from the loop and start certifying its own
    copy of the pipeline. It already had: the inline `_blocked` here never emitted
    `guardrail.evidence_blocked`, so deleting the real one off `loop` left the harness at
    exit 0. Now `del loop._blocked` fails it, which is what `AGENTS.md`'s maintenance
    contract wants from anything that watches `loop.py`.

    `mark` is taken by the caller BEFORE retrieval, where `run_turn` takes it, so the
    bundle covers the same span of trace a live turn's bundle covers."""
    if not snapshot.outcome.is_ok:
        # No stage completes, and buy-nothing is recorded as unreachable rather than as
        # an empty catalogue.
        return _from_turn(loop._unread(snapshot, loop.TurnResult(), mark))

    session = loop.ConversationSession(question=item.need, conclusive=conclusive)
    spec = AdviceSpec(
        question=item.need, budget=Budget(max_total_minor=item.budget_minor), requirements=()
    )
    selection = loop._Selection(
        items=[
            loop._Pick(
                product_id=p.product_id, variant_id=p.variant_id, rationale=p.rationale
            )
            for p in item.picks
        ]
    )

    decision = loop._decide(session, snapshot, spec, selection, candidates)
    bundle = evidence.build(decision.advice, trace.since(mark))

    if not bundle.accepted:
        missing = [
            loop._CHECKS_ES[c.name]
            for c in bundle.checks
            if c.outcome != "pass" and c.name in loop._CHECKS_ES
        ]
        reason = (
            f"Me faltó comprobar {', '.join(missing)}."
            if missing
            else "No pude comprobar lo que encontré."
        )
        return Rendered(
            arm="agent",
            kind="insufficient_evidence",
            unavailable_devices=decision.advice.unavailable_devices,
            text=prompts.UNVERIFIED_TEMPLATE.format(reason=reason),
            accepted=False,
            blocking=bundle.blocking,
        )

    advice = decision.advice
    if advice.kind == "recommend":
        allowed = [spec.budget.max_total_minor] if spec.budget.max_total_minor is not None else []
        text = guardrails.scrub_prose(item.prose, advice.items, allowed_minor=allowed)
    else:
        text = _KIND_TEMPLATE.get(advice.kind, lambda _d: prompts.UNREACHABLE_TEMPLATE)(decision)

    final = evidence.build(advice, trace.since(mark))
    return Rendered(
        arm="agent",
        kind=advice.kind,
        items=advice.items,
        unavailable_devices=advice.unavailable_devices,
        caveats=advice.caveats,
        text=text,
        accepted=final.accepted,
        blocking=final.blocking,
    )


# ── the six metrics ───────────────────────────────────────────────────────────


@dataclass
class Score:
    """`n` is the denominator this metric applies over. `defects` is what reached a screen.

    Both travel because either alone is gameable: zero defects is free to an arm that
    renders nothing, and the answer counter in the scoreboard is what shows that."""

    defects: int = 0
    n: int = 0
    notes: tuple[str, ...] = ()

    def __add__(self, other: Score) -> Score:
        return Score(self.defects + other.defects, self.n + other.n, self.notes + other.notes)

    def cell(self) -> str:
        return f"{self.defects}/{self.n}" if self.n else "—"


@dataclass(frozen=True)
class Context:
    """What a metric may look at: the case, the whole feed, the snapshot the turn got, and
    the candidates retrieval surfaced. Identical for both arms — one judge, two subjects."""

    item: Item
    products: tuple[CatalogProduct, ...]
    snapshot: tools.Snapshot
    candidates: tuple[CatalogProduct, ...]


@dataclass
class Judged:
    item: str
    arm: str
    scores: dict[str, Score]
    answered: bool
    kind: str
    accepted: bool | None


def _feed_variant(products: Sequence[CatalogProduct], item: AdviceItem) -> Any:
    product = next((p for p in products if p.product_id == item.product_id), None)
    if product is None:
        return None, None
    variant = next((v for v in product.variants if v.variant_id == item.variant_id), None)
    return product, variant


def _score_provenance(ctx: Context, rendered: Rendered) -> Score:
    """Every rendered field rebuilt from the feed — `guardrail.provenance`'s guarantee.

    The taxonomy is `guardrails.DroppedItem.reason`'s, not a new one."""
    defects, notes = 0, []
    for row in rendered.items:
        product, variant = _feed_variant(ctx.products, row)
        if product is None:
            defects += 1
            notes.append(f"not_in_catalog:{row.product_id}")
            continue
        if product.hidden:
            defects += 1
            notes.append(f"not_merchandise:{product.handle}")
            continue
        if variant is None:
            defects += 1
            notes.append(f"unknown_variant:{product.handle}")
            continue
        if len(product.variants) > 1 and not any(
            p.product_id == row.product_id and p.variant_id == row.variant_id
            for p in ctx.item.picks
        ):
            # The model named no variant and something chose one. The rendered label and
            # price belong to an option nobody picked.
            defects += 1
            notes.append(f"variant_ambiguous:{product.handle}→{variant.label}")
            continue
        if row.title != product.title or row.product_url != product.product_url:
            defects += 1
            notes.append(f"field_mismatch:{product.handle}")
    return Score(defects=defects, n=len(rendered.items), notes=tuple(notes))


def _score_stock(ctx: Context, rendered: Rendered) -> Score:
    """`guardrail.stock`: availability read out of the feed, never off the candidate.

    The baseline is not penalised for failing to filter — it is penalised for what it put
    on screen. The model saw `available` per variant in `tools._slim` and proposed a
    sold-out one anyway, which is the failure this check exists for: "a model that labels
    a sold-out variant `available: true` changes nothing here"."""
    defects, notes = 0, []
    for row in rendered.items:
        product, variant = _feed_variant(ctx.products, row)
        if product is None:
            defects += 1
            notes.append(f"unknowable:{row.product_id}")
            continue
        if variant is None:
            variant = product.variants[0] if product.variants else None
        if variant is None or not variant.available:
            defects += 1
            notes.append(f"out_of_stock:{product.handle}")
    return Score(defects=defects, n=len(rendered.items), notes=tuple(notes))


def _scale_signature(minor: int, feed_prices: frozenset[int]) -> str:
    """The COP 100x boundary, detected by its signature and reported as one.

    The rescaling is done by `money.major_string_to_minor` and nowhere else, which is both
    the rule (`tests/test_money.py` fails the build on a bare rescale in any other module)
    and the clearer statement of the bug: reading a value that is already minor units *as
    though it were the feed's major string* is precisely what that function does, so
    "would this number be a real price if someone had converted it twice?" is one call.

    A signature, not a proof. `money.py` holds the guarantee; this only names the smell."""
    try:
        if major_string_to_minor(str(minor)) in feed_prices:
            return "x100"
        if any(major_string_to_minor(str(price)) == minor for price in feed_prices):
            return "div100"
    except MoneyError:
        return ""
    return ""


def _score_budget(ctx: Context, rendered: Rendered) -> Score:
    """`guardrail.budget`: every number about money on screen came out of `money.py`, and
    a selection over a stated budget says so.

    Two defects share this metric because both are integer arithmetic in minor units: a
    price that is not the feed's price for the variant named, and a total presented as if
    it fit a budget it exceeds."""
    feed_prices = frozenset(v.price_minor for p in ctx.products for v in p.variants)
    defects, notes = 0, []

    for row in rendered.items:
        product, variant = _feed_variant(ctx.products, row)
        if variant is None and product is not None:
            variant = _first_variant(product, "")
        if variant is None:
            defects += 1
            signature = _scale_signature(row.price_minor, feed_prices)
            notes.append(
                f"unverifiable_price:{minor_to_display(row.price_minor)}"
                + (f"·{signature}" if signature else "")
            )
            continue
        if row.price_minor != variant.price_minor:
            defects += 1
            signature = _scale_signature(row.price_minor, feed_prices)
            notes.append(
                f"wrong_price:{product.handle} {minor_to_display(row.price_minor)}"
                f"≠{minor_to_display(variant.price_minor)}"
                + (f"·{signature}" if signature else "")
            )

    if ctx.item.budget_minor is not None and rendered.items:
        over = rendered.total_minor - ctx.item.budget_minor
        if over > 0:
            stated = minor_to_display(over) in " ".join((*rendered.caveats, rendered.text))
            if not stated:
                defects += 1
                notes.append(f"silent_overage:{minor_to_display(over)}")

    n = len(rendered.items) + (1 if ctx.item.budget_minor is not None and rendered.items else 0)
    return Score(defects=defects, n=n, notes=tuple(notes))


def _score_local_availability(ctx: Context, rendered: Rendered) -> Score:
    """`guardrail.local_availability`: a device COROS Colombia does not sell is named,
    never substituted. Brújula's differentiator, and the one metric whose applicability is
    derived from the need rather than from what an arm rendered.

    Both arms can pass. The baseline passes when the replayed model happened to fill
    `unavailable_devices`; the agent passes because `check_local_availability` re-derives
    it from the registry and `LocalAvailabilityVerdict.is_available` is `Literal[True]`, so
    an absent watch has no cleared form to travel in. What this metric shows is that the
    baseline is unreliable here, not that it is incapable — see docs/EVAL.md."""
    absent = [d for d in guardrails.devices_named(ctx.item.need) if not d.sold_locally]
    if not absent:
        return Score()
    unnamed = [d.slug for d in absent if d.slug not in rendered.unavailable_devices]
    return Score(
        defects=len(unnamed),
        n=len(absent),
        notes=tuple(f"absence_unstated:{slug}" for slug in unnamed),
    )


def _score_buy_nothing(ctx: Context, rendered: Rendered) -> Score:
    """`guardrail.buy_nothing`: "we could not look" is never reported as "there is
    nothing", and buying nothing stays reachable.

    Applicability is computed over the CANDIDATES retrieval surfaced, which is the same
    input `check_buy_nothing` takes. Scoring it over the model's picks instead would let
    an arm pass by proposing junk, and would call an item "nothing to buy" while retrieval
    was holding two purchasable products."""
    unreadable = not ctx.snapshot.outcome.is_ok
    purchasable = [p for p in ctx.candidates if p.in_stock and not p.hidden]
    if not unreadable and purchasable:
        return Score()

    if unreadable:
        ok = rendered.kind == "insufficient_evidence" and not rendered.items
        note = "" if ok else f"unreadable_reported_as:{rendered.kind}"
    else:
        ok = rendered.kind in ("buy_nothing", "not_sold_locally") and not rendered.items
        note = "" if ok else f"nothing_purchasable_reported_as:{rendered.kind}"
    return Score(defects=0 if ok else 1, n=1, notes=() if ok else (note,))


def _score_prose(ctx: Context, rendered: Rendered) -> Score:
    """`guardrail.prose`: a spec figure on screen appears in a retrieval-derived field.

    Judged against the arm's OWN rendered items, which is the harder reading for whichever
    arm rendered fewer of them — backing text shrinks with the item list.

    This measures the RULE, not truthfulness, and the rule over-excises on purpose:
    `guardrails._BACKING_FIELDS` over an `AdviceItem` is effectively the title, so `nylon`
    and `46 mm` count as unbacked even when a variant label says exactly that. Two of the
    baseline's defects in the recorded run are true sentences. `evidence._DECLARED` already
    says as much in the prose check's `cannot_verify`."""
    if not rendered.text:
        return Score()
    allowed = [ctx.item.budget_minor] if ctx.item.budget_minor is not None else []
    claims = guardrails.find_unbacked_claims(rendered.text, rendered.items, allowed_minor=allowed)
    return Score(
        defects=len(claims),
        n=1,
        notes=tuple(f"{c.kind}:{c.text}" for c in claims),
    )


METRICS = {
    "provenance": _score_provenance,
    "stock": _score_stock,
    "budget": _score_budget,
    "local_availability": _score_local_availability,
    "buy_nothing": _score_buy_nothing,
    "prose": _score_prose,
}


def declared_checks() -> tuple[str, ...]:
    """The check names out of a real bundle. `needs_human` requires none of them, so this
    costs nothing and reads the public surface rather than `evidence._DECLARED`."""
    sink: list[trace.TraceEvent] = []
    trace.bind_sink(sink)
    try:
        return tuple(c.name for c in evidence.build(Advice(kind="needs_human"), []).checks)
    finally:
        trace.bind_sink(None)


def judge(ctx: Context, rendered: Rendered) -> Judged:
    return Judged(
        item=ctx.item.id,
        arm=rendered.arm,
        scores={name: fn(ctx, rendered) for name, fn in METRICS.items()},
        answered=rendered.answered,
        kind=rendered.kind,
        accepted=rendered.accepted,
    )


# ── the cases ─────────────────────────────────────────────────────────────────

_SEARCH = "search_products"
_COMPAT = "lookup_device_compat"
_COLLECTION = "get_collection_products"

# Every id, variant and price below was read out of `fixtures/products.json`. The prose is
# written the way `PRESENT_PROMPT` asks for it: reasoning, no specs, except where an item
# exists to carry an invented one.
ITEMS: tuple[Item, ...] = (
    Item(
        id="clean-strap",
        need="una correa de silicona para mi COROS NOMAD",
        tool_calls=((_SEARCH, {"query": "correa silicona nomad"}),),
        picks=(
            Pick(
                product_id="7926626189355",
                variant_id="44112573431851",
                rationale="Silicona, del ancho que COROS declara para el NOMAD.",
                claimed_title="Correa de silicona verde  24mm Coros NOMAD",
                claimed_price_minor=13000000,
            ),
        ),
        prose="Esta es la correa que le corresponde a tu NOMAD, en silicona como pediste.",
    ),
    Item(
        id="clean-watch-in-budget",
        need="quiero el COROS PACE 4 en nylon negro",
        budget_minor=150_000_000,
        tool_calls=((_SEARCH, {"query": "pace 4"}),),
        picks=(
            Pick(
                product_id="7752529543211",
                variant_id="44066526396459",
                rationale="La combinación que pediste, y está disponible.",
                claimed_title="COROS PACE 4",
                claimed_price_minor=109900000,
            ),
        ),
        prose="El PACE 4 en nylon negro es exactamente lo que describiste, y cabe en lo que dijiste.",
    ),
    Item(
        id="ambiguous-case-apex-4",
        need="quiero el COROS APEX 4 de 42mm",
        tool_calls=((_COMPAT, {"device": "APEX 4"}), (_SEARCH, {"query": "apex 4"})),
        picks=(
            Pick(
                product_id="7704999428139",
                rationale="El APEX 4 que pediste.",
                claimed_title="COROS APEX 4",
            ),
        ),
        prose="El APEX 4 es el reloj que describiste.",
    ),
    Item(
        id="unretrieved-product",
        need="busco un monitor de frecuencia cardiaca de pecho COROS",
        tool_calls=((_SEARCH, {"query": "heart rate"}),),
        picks=(
            # Two registry hazards in one pick. The id is UCP's `gid://` shape, which does
            # not join to the storefront snapshot's bare numeric id — "a UCP hit absent
            # from the snapshot cannot pass check_provenance anyway". And the price is the
            # feed's MAJOR string used as minor: the real product is $340.000, and
            # `340000` centavos renders as $3.400. The COP 100x boundary, on screen.
            Pick(
                product_id="gid://shopify/Product/7155761184811",
                variant_id="gid://shopify/ProductVariant/41000000000001",
                rationale="El pulsómetro de pecho de COROS.",
                claimed_title="COROS HEART RATE MONITOR",
                claimed_url="https://coros.com.co/products/coros-heart-rate-monitor",
                claimed_price_minor=340000,
            ),
        ),
        prose="Este pulsómetro de pecho es el que COROS vende para entrenar por zonas.",
    ),
    Item(
        id="out-of-stock-dura",
        need="quiero el COROS DURA para la bicicleta",
        tool_calls=((_SEARCH, {"query": "dura"}),),
        picks=(
            Pick(
                product_id="7251062620203",
                variant_id="41122023276587",
                rationale="El ciclocomputador de COROS.",
                claimed_title="COROS DURA",
                claimed_price_minor=115000000,
            ),
        ),
        prose="El DURA es el ciclocomputador de COROS y es lo que tu caso pide.",
    ),
    Item(
        id="out-of-stock-cadence",
        need="un sensor de cadencia para la bicicleta",
        # English query on purpose: `search_products` is literal, the product is titled
        # "COROS Bike Cadence Sensor", and "sensor cadencia" matches nothing in this feed.
        tool_calls=((_SEARCH, {"query": "cadence sensor"}),),
        picks=(
            Pick(
                product_id="7319697522731",
                variant_id="41308250112043",
                rationale="El sensor de cadencia de COROS.",
                claimed_title="COROS Bike Cadence Sensor",
                claimed_price_minor=15900000,
            ),
        ),
        prose="Este sensor mide la cadencia y se empareja con tu reloj.",
    ),
    Item(
        id="gift-with-purchase",
        need="quiero el COROS PACE 4 y algo barato para completar el pedido",
        tool_calls=((_COLLECTION, {"handle": "relojes"}),),
        picks=(
            Pick(
                product_id="7752529543211",
                variant_id="44066526363691",
                rationale="El reloj que pediste.",
                claimed_title="COROS PACE 4",
                claimed_price_minor=109900000,
            ),
            Pick(
                product_id="7427337060395",
                variant_id="41937772445739",
                rationale="Lo más barato que aparece en el catálogo.",
                claimed_title="Camisa Hombre",
                claimed_price_minor=12000000,
            ),
        ),
        prose="El PACE 4 resuelve lo que pediste, y añadí lo más barato que había para completar.",
    ),
    Item(
        id="absent-vertix-2",
        need="quiero un COROS VERTIX 2 para ultras de montaña",
        tool_calls=((_COMPAT, {"device": "VERTIX 2"}), (_COLLECTION, {"handle": "relojes"})),
        picks=(
            Pick(
                product_id="7704999428139",
                variant_id="42372173430827",
                rationale="Autonomía y caja grande para salidas largas.",
                claimed_title="COROS APEX 4",
                claimed_price_minor=209900000,
            ),
        ),
        prose="Para ultras de montaña este es el reloj con más autonomía del catálogo.",
    ),
    Item(
        id="over-budget-apex-4",
        need="quiero el COROS APEX 4 de 46mm, máximo un millón de pesos",
        budget_minor=100_000_000,
        tool_calls=((_SEARCH, {"query": "apex 4"}),),
        picks=(
            Pick(
                product_id="7704999428139",
                variant_id="42372173430827",
                rationale="El APEX 4 de 46 mm que pediste.",
                claimed_title="COROS APEX 4",
                claimed_price_minor=209900000,
            ),
        ),
        prose="El APEX 4 de 46 mm es el reloj que describiste.",
    ),
    Item(
        id="storefront-rate-limited",
        need="corro trail, salidas de tres horas, ¿qué reloj me sirve?",
        snapshot="rate_limited",
        tool_calls=((_SEARCH, {"query": "reloj trail"}),),
        picks=(
            Pick(
                product_id="7752529543211",
                variant_id="44066526363691",
                rationale="Un reloj de trail del catálogo.",
                claimed_title="COROS PACE 4",
                claimed_price_minor=109900000,
            ),
        ),
        prose="Para salidas de tres horas este reloj es suficiente.",
    ),
    Item(
        id="invented-specs",
        need="corro trail y necesito algo que aguante lluvia y salidas largas",
        tool_calls=((_SEARCH, {"query": "pace 4"}),),
        picks=(
            Pick(
                product_id="7752529543211",
                variant_id="44066526461995",
                rationale="Silicona para lluvia, y la autonomía que tus salidas piden.",
                claimed_title="COROS PACE 4",
                claimed_price_minor=109900000,
            ),
        ),
        prose=(
            "Para tus salidas el PACE 4 es la opción: sumergible hasta 100 m, hasta 45 días "
            "de batería y caja de titanio, así que la lluvia y las tres horas no son problema."
        ),
    ),
    Item(
        id="absent-pace-3-with-strap",
        need="tengo un COROS PACE 3, ¿qué me recomiendas?",
        # The replayed model DID name the absence here and did not on `absent-vertix-2`.
        # That pair is the assumption this file is most exposed on, and it is deliberate:
        # a prompt-level rule that works once and not the next time is the argument for
        # putting the rule in Python, and a baseline that could never pass would be straw.
        claimed_unavailable=("pace-3",),
        tool_calls=((_COMPAT, {"device": "PACE 3"}), (_SEARCH, {"query": "correa 22mm"})),
        picks=(
            Pick(
                product_id="7930746109995",
                variant_id="44131578314795",
                rationale="Una correa de 22 mm, el ancho que COROS declara.",
                claimed_title="Correa de 22mm silicona negra COROS APEX 4 42 mm",
                claimed_price_minor=13000000,
            ),
        ),
        prose="Esta correa es del ancho que tu reloj usa.",
    ),
    Item(
        id="absent-pace-pro",
        need="quiero un COROS PACE Pro",
        # The only item that reaches `not_sold_locally` — the advice kind behind the metric
        # docs/EVAL.md §4 calls Brújula's differentiator. Without it `loop._names`,
        # `prompts.NOT_SOLD_TEMPLATE` and that branch of `_decide` are unmeasured.
        #
        # The model is recorded getting this one RIGHT: no substitute proposed and the
        # absence named. That is the reading least favourable to the agent — both arms
        # score clean, so the item buys coverage and no win.
        claimed_unavailable=("pace-pro",),
        # `search_products` finds PACE Pro STRAPS and no PACE Pro, which is exactly the
        # hazard `UnavailableDevice.tradeoff` is written for. Those straps are purchasable,
        # and that is what keeps `_decide` off the buy-nothing branch and on this one.
        tool_calls=((_SEARCH, {"query": "pace pro"}),),
        picks=(),
        prose="",
    ),
)


# ── running both arms ─────────────────────────────────────────────────────────


@dataclass
class Run:
    item: Item
    baseline: Judged
    agent: Judged
    baseline_ledger: Ledger
    agent_ledger: Ledger
    ctx: Context | None = None
    detail: dict[str, Rendered] = field(default_factory=dict)

    @property
    def equal_budget(self) -> bool:
        return self.baseline_ledger.spent() == self.agent_ledger.spent()


async def run_item(item: Item, products: Sequence[CatalogProduct]) -> Run:
    """Both arms, each with its own ledger, each retrieving for itself.

    Retrieval is deliberately not shared. Sharing it would make the budgets equal by
    construction and prove nothing; running it twice and comparing the ledgers is what
    turns "equal budget" into a measurement."""
    rendered: dict[str, Rendered] = {}
    ledgers: dict[str, Ledger] = {}
    retrieved: dict[str, tuple[tools.Snapshot, tuple[CatalogProduct, ...]]] = {}

    for arm in ("baseline", "agent"):
        ledger = Ledger(arm=arm)
        sink: list[trace.TraceEvent] = []
        trace.bind_sink(sink)
        try:
            # Before retrieval, where `run_turn` takes it. Nothing a declared guardrail
            # reads is emitted during retrieval today, so this changes no number — but
            # `guardrail.case_unspecified` fires in there, and a bundle that starts after
            # it is not the bundle the turn builds.
            mark = trace.mark()
            snapshot, candidates, conclusive = await _retrieve(item, products, ledger)
            ledger.replay_model()
            if arm == "baseline":
                rendered[arm] = run_baseline(item, snapshot, candidates)
            else:
                rendered[arm] = run_agent(item, snapshot, candidates, conclusive, mark)
        finally:
            tools.bind_snapshot(None)
            trace.bind_sink(None)
        ledgers[arm] = ledger
        retrieved[arm] = (snapshot, candidates)

    if retrieved["baseline"][1] != retrieved["agent"][1]:
        raise RuntimeError(
            f"{item.id}: the arms retrieved different candidates. They replay the same tool "
            "calls against the same snapshot, so this is a harness bug, and nothing scored "
            "afterwards would be a comparison."
        )

    ctx = Context(
        item=item,
        products=tuple(products),
        snapshot=retrieved["agent"][0],
        candidates=retrieved["agent"][1],
    )
    return Run(
        item=item,
        ctx=ctx,
        baseline=judge(ctx, rendered["baseline"]),
        agent=judge(ctx, rendered["agent"]),
        baseline_ledger=ledgers["baseline"],
        agent_ledger=ledgers["agent"],
        detail=rendered,
    )


# ── the report ────────────────────────────────────────────────────────────────

_W = 26


def _totals(runs: Sequence[Run], arm: str) -> dict[str, Score]:
    out = {name: Score() for name in METRICS}
    for run in runs:
        judged = getattr(run, arm)
        for name, score in judged.scores.items():
            out[name] = out[name] + score
    return out


def _like_for_like(runs: Sequence[Run]) -> list[Run]:
    """Items where BOTH arms put products on screen.

    The control for the obvious objection: an arm that refuses has nothing to be wrong
    about, so a headline win could be nothing but abstention. On this subset neither arm
    abstained, so whatever is left is the checks doing work."""
    return [r for r in runs if r.baseline.answered and r.agent.answered]


def _verdicts(baseline: dict[str, Score], agent: dict[str, Score]) -> dict[str, str]:
    verdicts = {}
    for name in METRICS:
        b, a = baseline[name], agent[name]
        if b.n == 0 and a.n == 0:
            verdicts[name] = "not exercised"
        elif a.defects < b.defects:
            verdicts[name] = "AGENT"
        elif a.defects > b.defects:
            verdicts[name] = "BASELINE"
        else:
            verdicts[name] = "tie"
    return verdicts


def report(runs: Sequence[Run], verbose: bool) -> tuple[dict[str, str], bool]:
    print("\nCOROS agent vs. retrieval-only baseline — offline, fixtures/products.json")
    print(f"{len(runs)} items · model replayed for both arms · no network\n")

    print("per item")
    header = f"  {'item':<26} {'arm':<9} " + " ".join(f"{n[:9]:>10}" for n in METRICS)
    print(header + f" {'kind':>22} {'answered':>9}")
    print("  " + "-" * (len(header) + 30))
    for run in runs:
        for arm in ("baseline", "agent"):
            judged: Judged = getattr(run, arm)
            cells = " ".join(f"{judged.scores[n].cell():>10}" for n in METRICS)
            print(
                f"  {run.item.id if arm == 'baseline' else '':<26} {arm:<9} {cells} "
                f"{judged.kind:>22} {'yes' if judged.answered else 'no':>9}"
            )
        if verbose:
            for arm in ("baseline", "agent"):
                judged = getattr(run, arm)
                for name, score in judged.scores.items():
                    for note in score.notes:
                        print(f"      {arm:<9} {name:<20} {note}")

    baseline, agent = _totals(runs, "baseline"), _totals(runs, "agent")
    verdicts = _verdicts(baseline, agent)

    print(f"\naggregate over all {len(runs)} items — false things on screen / opportunities")
    print("  a count, not a rate: the denominators differ because the arms render")
    print("  different numbers of items, which is itself one of the results.")
    print(f"\n  {'metric (= a declared check)':<{_W}} {'baseline':>10} {'agent':>10}   winner")
    print("  " + "-" * (_W + 34))
    for name in METRICS:
        print(
            f"  {name:<{_W}} {baseline[name].cell():>10} {agent[name].cell():>10}"
            f"   {verdicts[name]}"
        )

    subset = _like_for_like(runs)
    sub_b, sub_a = _totals(subset, "baseline"), _totals(subset, "agent")
    sub_verdicts = _verdicts(sub_b, sub_a)
    print(f"\nCONTROL — the {len(subset)} items where BOTH arms put products on screen")
    print("  Nobody abstained here, so no win below can be abstention.")
    print(f"\n  {'metric':<{_W}} {'baseline':>10} {'agent':>10}   winner")
    print("  " + "-" * (_W + 34))
    for name in METRICS:
        print(
            f"  {name:<{_W}} {sub_b[name].cell():>10} {sub_a[name].cell():>10}"
            f"   {sub_verdicts[name]}"
        )
    print(f"  items: {', '.join(r.item.id for r in subset)}")

    b_answered = sum(1 for r in runs if r.baseline.answered)
    a_answered = sum(1 for r in runs if r.agent.answered)
    a_accepted = sum(1 for r in runs if r.agent.accepted)
    print("\ncounters — reported, not scored: higher is not better")
    print(f"  {'items answered with products':<{_W}} {b_answered:>10} {a_answered:>10}")
    print(f"  {'evidence bundle accepted':<{_W}} {'n/a':>10} {a_accepted:>10}")
    print(
        f"  The baseline answers with products on {b_answered} of {len(runs)} and the agent on\n"
        f"  {a_answered}. That is the trade and not a win — an answer beats a refusal only when\n"
        "  it is true — but it is also the agent's real cost, and the refusals below are where\n"
        "  a reader should look before believing the table above."
    )
    _refusals_block(runs)

    equal = _budget_block(runs)
    return verdicts, equal


def _refusals_block(runs: Sequence[Run]) -> None:
    """Every item the agent declined to answer and the baseline did not, with the copy.

    Printed because the six metrics cannot see it: a refusal scores zero defects whether
    its wording fits the situation or not, and two of these do not."""
    refused = [r for r in runs if r.baseline.answered and not r.agent.answered]
    if not refused:
        return
    print(f"\nwhere the agent refused and the baseline answered ({len(refused)})")
    print("  the six metrics score these as clean. Read the copy and decide for yourself.")
    for run in refused:
        agent = run.detail["agent"]
        first = " ".join(agent.text.split())[:96]
        stock = [p for p in (run.ctx.candidates if run.ctx else ()) if p.in_stock and not p.hidden]
        print(f"\n  {run.item.id}  →  {agent.kind}")
        print(f"    need:      {run.item.need}")
        print(f"    answer:    {first}…")
        print(
            f"    retrieval: {len(run.ctx.candidates) if run.ctx else 0} candidates, "
            f"{len(stock)} of them purchasable"
            + ("  ← it had something to offer and did not" if stock else "")
        )


def _budget_block(runs: Sequence[Run]) -> bool:
    total_b, total_a = Ledger("baseline"), Ledger("agent")
    for run in runs:
        total_b = total_b + run.baseline_ledger
        total_a = total_a + run.agent_ledger

    print("\nEQUAL BUDGET — the claim this evaluation stands or falls on")
    print(f"  baseline  {total_b.line()}")
    print(f"  agent     {total_a.line()}")
    unequal = [r.item.id for r in runs if not r.equal_budget]
    if unequal or total_b.spent() != total_a.spent():
        print(f"  [FAIL] the arms did not spend the same. Diverged on: {', '.join(unequal) or '—'}")
        print("  Nothing below this line is a comparison. Fix the harness before reading it.")
        return False
    print(
        "  [OK] identical on every item and in total. The agent's five checks and its\n"
        "  evidence bundle are pure functions over the snapshot the turn already paid for\n"
        "  (guardrails.py: no I/O, no clock, no model), so the guarantees cost no calls.\n"
        f"  Caps in force: {loop.MAX_TOOL_CALLS_PER_TURN} tool calls/turn, "
        f"{loop.MAX_MODEL_CALLS} model calls/conversation, "
        f"{CATALOG_READS_PER_TURN} storefront read/turn."
    )
    return True


def _as_json(runs: Sequence[Run]) -> dict[str, Any]:
    baseline, agent = _totals(runs, "baseline"), _totals(runs, "agent")
    subset = _like_for_like(runs)
    sub_b, sub_a = _totals(subset, "baseline"), _totals(subset, "agent")
    return {
        "items": len(runs),
        "equal_budget": all(r.equal_budget for r in runs),
        "budget": {
            arm: {
                "catalog_reads": sum(getattr(r, f"{arm}_ledger").catalog_reads for r in runs),
                "tool_calls": sum(getattr(r, f"{arm}_ledger").tool_calls for r in runs),
                "model_calls": sum(getattr(r, f"{arm}_ledger").model_calls for r in runs),
                "replayed_model_stages": sum(getattr(r, f"{arm}_ledger").replays for r in runs),
            }
            for arm in ("baseline", "agent")
        },
        "answered_with_products": {
            "baseline": sum(1 for r in runs if r.baseline.answered),
            "agent": sum(1 for r in runs if r.agent.answered),
        },
        "aggregate": {
            name: {
                "baseline": {"defects": baseline[name].defects, "n": baseline[name].n},
                "agent": {"defects": agent[name].defects, "n": agent[name].n},
            }
            for name in METRICS
        },
        "like_for_like": {
            "items": [r.item.id for r in subset],
            "metrics": {
                name: {
                    "baseline": {"defects": sub_b[name].defects, "n": sub_b[name].n},
                    "agent": {"defects": sub_a[name].defects, "n": sub_a[name].n},
                }
                for name in METRICS
            },
            "verdicts": _verdicts(sub_b, sub_a),
        },
        "verdicts": _verdicts(baseline, agent),
        "per_item": [
            {
                "item": r.item.id,
                **{
                    arm: {
                        "kind": getattr(r, arm).kind,
                        "answered": getattr(r, arm).answered,
                        "scores": {
                            n: {"defects": s.defects, "n": s.n, "notes": list(s.notes)}
                            for n, s in getattr(r, arm).scores.items()
                        },
                    }
                    for arm in ("baseline", "agent")
                },
            }
            for r in runs
        ],
    }


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable result on stdout")
    parser.add_argument("--verbose", action="store_true", help="print every defect note")
    parser.add_argument("--item", action="append", default=[], help="run only these item ids")
    parser.add_argument(
        "--allow-regression",
        action="store_true",
        help="report a metric the baseline wins instead of exiting non-zero",
    )
    args = parser.parse_args(argv)

    missing = set(METRICS) ^ set(declared_checks())
    if missing:
        print(
            f"the metric table and evidence.build's declared checks disagree on {sorted(missing)}.\n"
            "Every declared check gets a metric; see AGENTS.md's maintenance contract.",
            file=sys.stderr,
        )
        return 2

    products = load_products()
    chosen = [i for i in ITEMS if not args.item or i.id in set(args.item)]
    if not chosen:
        print(f"no item matched {args.item}", file=sys.stderr)
        return 2

    runs = [await run_item(item, products) for item in chosen]

    if args.json:
        payload = _as_json(runs)
        print(json.dumps(payload, indent=1, ensure_ascii=False))
        lost = [n for n, v in payload["verdicts"].items() if v == "BASELINE"]
        if not payload["equal_budget"]:
            return 1
        return 0 if not lost or args.allow_regression else 1

    verdicts, equal = report(runs, args.verbose)
    if not equal:
        return 1

    lost = [name for name, v in verdicts.items() if v == "BASELINE"]
    tied = [name for name, v in verdicts.items() if v == "tie"]
    won = [name for name, v in verdicts.items() if v == "AGENT"]
    print(f"\nwon {len(won)} · tied {len(tied)} · lost {len(lost)}")
    if lost:
        print(f"  REGRESSION — the baseline is better on: {', '.join(lost)}")
        print("  A harness tuned until the agent wins measures nothing. Read the notes with")
        print("  --verbose and fix the pipeline, or record why the baseline should win here.")
        return 0 if args.allow_regression else 1
    print("  no metric where the unguarded pipeline does better, at identical budget.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
