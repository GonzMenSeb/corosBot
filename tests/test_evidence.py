"""What an accepted recommendation is allowed to claim.

The bundle is built from the trace and from the advice, and from nothing the agent says
about itself. Every test here is the same adversarial question in a different costume: if
a stage lies about having verified something, does the bundle still say it was verified?

KB `Code As Agent Harness.pdf` §5.2.2 is the shape being implemented — "every accepted
action carr[ies] an evidence bundle containing the checks run, the assumptions
preserved, the untested regions, and the remaining risks", each artifact declaring "what
it verifies, what it cannot verify, and what confidence it provides".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coros_core import evidence, trace
from coros_core.guardrails import (
    check_budget,
    check_buy_nothing,
    check_local_availability,
    check_provenance,
    check_stock,
    scrub_prose,
)
from coros_core.models import Advice, AdviceItem, Budget, CatalogProduct, CatalogVariant

PACE_4_ID = "7752529543211"
PACE_4_VARIANT = "44066526363691"
PACE_4_PRICE = 109_900_000
SPEC_DIGEST = "9f3c" * 16

REQUIRED_FOR_A_RECOMMENDATION = (
    "provenance",
    "stock",
    "budget",
    "local_availability",
    "buy_nothing",
)


def product(
    product_id: str = PACE_4_ID,
    handle: str = "coros-pace-4",
    title: str = "COROS PACE 4",
    *,
    available: bool = True,
    price_minor: int = PACE_4_PRICE,
) -> CatalogProduct:
    return CatalogProduct(
        product_id=product_id,
        handle=handle,
        title=title,
        product_url=f"https://coros.com.co/products/{handle}",
        variants=(
            CatalogVariant(
                variant_id=PACE_4_VARIANT,
                label="Negro",
                price_minor=price_minor,
                available=available,
            ),
        ),
    )


def candidate(product_id: str = PACE_4_ID) -> dict[str, str]:
    return {
        "product_id": product_id,
        "variant_id": PACE_4_VARIANT,
        "rationale": "el reloj que la actividad pide",
    }


def run_pipeline(
    catalog: tuple[CatalogProduct, ...],
    candidates: tuple[dict[str, str], ...],
    *,
    budget: Budget | None = None,
    text: str = "",
) -> tuple[AdviceItem, ...]:
    """The five checks a turn runs before it presents anything, in pipeline order."""
    renderable = check_provenance(candidates, catalog).renderable
    items = check_stock(renderable, catalog).items
    check_budget(items, budget)
    check_local_availability(text, catalog)
    check_buy_nothing(catalog, budget=budget)
    return items


def advice(items: tuple[AdviceItem, ...] = (), **kw) -> Advice:
    fields = {"kind": "recommend" if items else "buy_nothing", "spec_digest": SPEC_DIGEST}
    fields.update(kw)
    return Advice(items=items, **fields)


class TestNothingIsAcceptedWithoutEvidenceThatItWasChecked:
    def test_a_bundle_with_an_empty_trace_accepts_nothing(self) -> None:
        bundle = evidence.build(advice((sample_item(),)))

        assert bundle.accepted is False
        assert {c.name for c in bundle.checks if c.outcome == "not_run"} >= set(
            REQUIRED_FOR_A_RECOMMENDATION
        )

    def test_the_checks_that_did_not_run_are_named_in_the_blocking_list(self) -> None:
        bundle = evidence.build(advice((sample_item(),)))

        for name in REQUIRED_FOR_A_RECOMMENDATION:
            assert any(name in reason for reason in bundle.blocking), (
                f"{name} never ran and the bundle did not say so.\n"
                "  Termination is governed by verification, not by the model deciding it is\n"
                "  finished — a check with no event is a check that did not happen."
            )

    def test_a_turn_that_ran_every_check_is_accepted(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice(items))

        assert bundle.accepted is True, bundle.render()
        assert bundle.blocking == ()
        assert {c.name for c in bundle.checks if c.outcome == "pass"} >= set(
            REQUIRED_FOR_A_RECOMMENDATION
        )

    def test_only_the_events_of_this_turn_count(self) -> None:
        """A sink spans a session. Turn two's bundle must not inherit turn one's clearance."""
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        mark = trace.mark()

        assert evidence.build(advice(items), trace.since(mark)).accepted is False


class TestTheAdviceCannotContradictTheVerdicts:
    def test_an_item_the_stock_check_rejected_cannot_ride_into_an_accepted_bundle(self) -> None:
        """The verdict is real and the advice is dishonest: an app that keeps a rejected
        item is the failure this bundle exists to catch, not a case to trust."""
        catalog = (product(available=False),)
        run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice((sample_item(),)))

        assert bundle.accepted is False
        assert bundle.check("stock").outcome == "fail"
        assert any(PACE_4_ID in reason for reason in bundle.blocking)

    def test_an_item_provenance_dropped_cannot_ride_in_either(self) -> None:
        catalog = (product(product_id="7704999428139", handle="coros-apex-4", title="APEX 4"),)
        run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice((sample_item(),)))

        assert bundle.accepted is False
        assert bundle.check("provenance").outcome == "fail"

    def test_more_items_than_the_checks_cleared_is_a_contradiction(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        inflated = items + (sample_item(product_id="9999999999999"),)

        bundle = evidence.build(advice(inflated))

        assert bundle.accepted is False
        assert bundle.check("provenance").outcome == "fail"

    def test_a_recommendation_over_an_inconclusive_retrieval_is_blocked(self) -> None:
        """A 429 is not a fact about stock. `retrieval_conclusive=False` is the one signal
        that says the catalogue was never read, and a recommendation on top of it invents
        an inventory."""
        catalog = (product(),)
        items = check_stock(check_provenance((candidate(),), catalog).renderable, catalog).items
        check_provenance((candidate(),), catalog)
        check_budget(items, None)
        check_local_availability("", catalog)
        check_buy_nothing((), retrieval_conclusive=False)

        bundle = evidence.build(advice(items))

        assert bundle.accepted is False
        assert bundle.check("buy_nothing").outcome == "fail"
        assert any("inconclusive" in reason for reason in bundle.blocking)

    def test_the_same_inconclusive_turn_is_accepted_as_insufficient_evidence(self) -> None:
        check_buy_nothing((), retrieval_conclusive=False)

        bundle = evidence.build(advice(kind="insufficient_evidence"))

        assert bundle.accepted is True, bundle.render()

    def test_an_absent_watch_the_advice_never_names_is_blocked(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),), text="quiero un VERTIX 2")

        bundle = evidence.build(advice(items))

        assert bundle.accepted is False
        assert bundle.check("local_availability").outcome == "fail"
        assert any("vertix-2" in reason for reason in bundle.blocking), bundle.blocking

    def test_naming_the_absent_watch_by_its_registry_slug_clears_the_check(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),), text="quiero un VERTIX 2")

        bundle = evidence.build(advice(items, unavailable_devices=("vertix-2",)))

        assert bundle.accepted is True, bundle.render()

    def test_a_selection_the_budget_verdict_says_fits_nothing_cannot_be_presented(self) -> None:
        """A cheap strap keeps the buy-nothing verdict at "recommend", so the budget rule is
        the only thing that can block here: nothing_fits with items is a contradiction."""
        catalog = (
            product(),
            product("2", "correa-nylon", "Correa de nylon", price_minor=10_000_000),
        )
        items = run_pipeline(catalog, (candidate(),), budget=Budget(max_total_minor=50_000_000))

        bundle = evidence.build(advice(items))

        assert bundle.check("buy_nothing").outcome == "pass"
        assert bundle.check("budget").outcome == "fail"
        assert bundle.accepted is False
        assert any("nothing fits" in reason for reason in bundle.blocking), bundle.blocking


class TestAnHonestBadAnswerIsStillAcceptable:
    def test_over_budget_reported_as_over_budget_is_accepted_and_flagged(self) -> None:
        """An outcome is about the advice agreeing with the verdict, not about the verdict
        being good news."""
        catalog = (product(), product(product_id="2", handle="apex-4", title="APEX 4"))
        items = run_pipeline(
            catalog,
            (candidate(), candidate("2")),
            budget=Budget(max_total_minor=150_000_000),
        )

        bundle = evidence.build(advice(items))

        assert bundle.accepted is True, bundle.render()
        assert any("presupuesto" in r or "budget" in r for r in bundle.risks), bundle.risks

    def test_a_discarded_field_the_model_invented_is_carried_as_a_risk(self) -> None:
        catalog = (product(),)
        lying = dict(candidate(), title="COROS PACE 4 Pro Max", price_minor="1099000")
        items = check_stock(check_provenance((lying,), catalog).renderable, catalog).items
        check_budget(items, None)
        check_local_availability("", catalog)
        check_buy_nothing(catalog)

        bundle = evidence.build(advice(items))

        assert bundle.accepted is True, bundle.render()
        assert any("title" in risk for risk in bundle.risks), bundle.risks


class TestTheBundleSaysWhatItCannotSee:
    def test_every_check_declares_what_it_verifies_and_what_it_cannot(self) -> None:
        bundle = evidence.build(advice((sample_item(),)))

        for check in bundle.checks:
            assert check.verifies and check.cannot_verify, (
                f"{check.name} declares no scope. KB §5.2.2: each artifact says what it\n"
                "  verifies, what it cannot verify, and what confidence it provides."
            )

    def test_silence_from_the_prose_scrub_is_an_untested_region_not_a_pass(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice(items, explanation="El PACE 4 aguanta tu semana."))

        assert bundle.check("prose").outcome == "not_run"
        assert any("prose" in region for region in bundle.untested), bundle.untested

    def test_a_scrub_that_fired_is_recorded_as_having_run(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        scrubbed = scrub_prose("Sumergible hasta 100 m, así que sirve.", items)

        bundle = evidence.build(advice(items, explanation=scrubbed))

        assert bundle.check("prose").outcome == "pass"
        assert "100 m" not in scrubbed

    def test_a_rate_limited_storefront_is_named_as_untested_without_blocking(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        trace.emit("catalog.rate_limited", {"url": "products.json", "retry_after": "60"}, "error")

        bundle = evidence.build(advice(items))

        assert bundle.accepted is True, bundle.render()
        assert any("catalog.rate_limited" in region for region in bundle.untested), bundle.untested

    def test_events_lost_off_the_end_of_the_ring_are_reported(self) -> None:
        for i in range(trace.RING + 3):
            trace.emit("agent.tick", {"i": i})
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice(items))

        assert bundle.accepted is True, bundle.render()
        assert any("ring" in region for region in bundle.untested), bundle.untested

    def test_a_run_with_no_spec_digest_cannot_be_replayed_and_says_so(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice(items, spec_digest=""))

        assert any("replay" in risk for risk in bundle.risks), bundle.risks


class TestAssumptionsAreCarriedAndNotJustArtifacts:
    """KB §5.2.4: these mechanisms often synchronize artifacts but not assumptions."""

    def test_the_price_boundary_assumption_names_where_it_is_pinned(self) -> None:
        bundle = evidence.build(advice((sample_item(),)))

        money = next(a for a in bundle.assumptions if "money.py" in a.basis)
        assert "minor" in money.statement

    def test_an_assumption_the_trace_confirms_is_marked_held(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice(items))

        stock = next(a for a in bundle.assumptions if "stock" in a.statement)
        assert stock.held is True

    def test_an_assumption_the_trace_refutes_is_marked_broken(self) -> None:
        catalog = (product(available=False),)
        run_pipeline(catalog, (candidate(),))

        bundle = evidence.build(advice((sample_item(),)))

        stock = next(a for a in bundle.assumptions if "stock" in a.statement)
        assert stock.held is False

    def test_an_assumption_nothing_re_derived_is_unknown_rather_than_true(self) -> None:
        bundle = evidence.build(advice((sample_item(),)))

        assert any(a.held is None for a in bundle.assumptions)


class TestTheBundleIsARecordAndNotProse:
    def test_it_is_frozen_once_built(self) -> None:
        bundle = evidence.build(advice((sample_item(),)))
        with pytest.raises(ValidationError):
            bundle.accepted = True

    def test_it_survives_a_json_round_trip(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        bundle = evidence.build(advice(items))

        assert evidence.EvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle

    def test_the_rendering_carries_every_section_a_reviewer_reads(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        text = evidence.build(advice(items)).render()

        for heading in ("ACCEPTED", "checks", "assumptions", "untested", "risks"):
            assert heading in text, text

    def test_a_blocked_bundle_renders_the_reason_it_was_blocked(self) -> None:
        text = evidence.build(advice((sample_item(),))).render()

        assert "BLOCKED" in text
        assert "provenance" in text


class TestBuildingTheBundleLeavesItsOwnTrace:
    def test_the_bundle_is_recorded_at_guardrail_level(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        bundle = evidence.build(advice(items))

        last = trace.events()[-1]
        assert last.event == "evidence.bundle"
        assert last.level == "guardrail"
        assert last.payload["accepted"] is bundle.accepted

    def test_the_bundle_does_not_count_itself_as_evidence(self) -> None:
        catalog = (product(),)
        items = run_pipeline(catalog, (candidate(),))
        evidence.build(advice(items))

        second = evidence.build(advice(items))
        assert [c.outcome for c in second.checks] == [
            c.outcome for c in evidence.build(advice(items)).checks
        ]


def sample_item(product_id: str = PACE_4_ID) -> AdviceItem:
    return AdviceItem(
        product_id=product_id,
        title="COROS PACE 4",
        product_url="https://coros.com.co/products/coros-pace-4",
        variant_id=PACE_4_VARIANT,
        price_minor=PACE_4_PRICE,
    )
