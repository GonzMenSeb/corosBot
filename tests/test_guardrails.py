"""The deterministic layer between the model and the screen.

Every test here answers the same question: does this hold when the model is wrong on
purpose? A guardrail that only passes on cooperative input is a prompt with extra steps.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from coros_core import trace
from coros_core.guardrails import (
    BudgetVerdict,
    BuyNothingVerdict,
    LocalAvailabilityVerdict,
    ProvenanceVerdict,
    SpecClaim,
    StockVerdict,
    check_budget,
    check_buy_nothing,
    check_local_availability,
    check_provenance,
    check_stock,
    devices_named,
    find_unbacked_claims,
    scrub_prose,
)
from coros_core.models import AdviceItem, Budget, CatalogProduct, CatalogVariant

PACE_4_ID = "7752529543211"
PACE_4_VARIANT = "44066526363691"
PACE_4_PRICE = 109_900_000
APEX_4_ID = "7704999428139"


def product(
    product_id: str,
    handle: str,
    title: str,
    *,
    description: str = "",
    variants: tuple[tuple[str, str, int, bool], ...] = (("v1", "Negro", PACE_4_PRICE, True),),
) -> CatalogProduct:
    return CatalogProduct(
        product_id=product_id,
        handle=handle,
        title=title,
        product_url=f"https://coros.com.co/products/{handle}",
        description=description,
        variants=tuple(
            CatalogVariant(variant_id=v, label=label, price_minor=price, available=ok)
            for v, label, price, ok in variants
        ),
    )


PACE_4 = product(
    PACE_4_ID,
    "coros-pace-4",
    "COROS PACE 4",
    description="Batería de larga duración con hasta 38 horas de GPS. Titanio ligero.",
    variants=((PACE_4_VARIANT, "Nylon / Blanco", PACE_4_PRICE, True),),
)
APEX_4 = product(
    APEX_4_ID,
    "coros-apex-4",
    "COROS APEX 4",
    variants=(("a42", "42mm / Negro", 209_900_000, True), ("a46", "46mm / Negro", 209_900_000, True)),
)
SOLD_OUT = product(
    "9001", "coros-nomad", "COROS NOMAD", variants=(("n1", "Negro", 125_000_000, False),)
)
CATALOG = (PACE_4, APEX_4, SOLD_OUT)


def item(price_minor: int = PACE_4_PRICE, **over: object) -> AdviceItem:
    base: dict[str, object] = {
        "product_id": PACE_4_ID,
        "title": "COROS PACE 4",
        "product_url": "https://coros.com.co/products/coros-pace-4",
        "variant_id": PACE_4_VARIANT,
        "price_minor": price_minor,
    }
    return AdviceItem(**{**base, **over})  # type: ignore[arg-type]


def guardrail_events() -> list[trace.TraceEvent]:
    return [e for e in trace.events() if e.level == "guardrail"]


class TestEveryVerdictLeavesATrace:
    """A verdict a reviewer cannot see fire is indistinguishable from one that never ran."""

    def test_each_check_emits_exactly_one_guardrail_event(self) -> None:
        check_provenance([{"product_id": PACE_4_ID, "variant_id": PACE_4_VARIANT}], CATALOG)
        check_stock([item()], CATALOG)
        check_budget([item()], Budget(max_total_minor=200_000_000))
        check_local_availability("quiero un VERTIX 2")
        check_buy_nothing([PACE_4])
        scrub_prose("Dura 90 horas.", [PACE_4])

        assert [e.event for e in guardrail_events()] == [
            "guardrail.provenance",
            "guardrail.stock",
            "guardrail.budget",
            "guardrail.local_availability",
            "guardrail.buy_nothing",
            "guardrail.prose",
        ], "a new check needs its trace event and a row in AGENTS.md's guardrail table"

    def test_a_verdict_is_never_emitted_below_guardrail_level(self) -> None:
        check_budget([item()], Budget())
        assert [e.level for e in trace.events()] == ["guardrail"]


class TestAnAbsentWatchIsReportedNotSubstituted:
    """B1's whole differentiator. COROS Colombia sells four devices and lists straps for
    ten more; the trap is a search that finds the straps and reads as availability."""

    def test_a_watch_coros_colombia_does_not_sell_is_named_as_absent(self) -> None:
        report = check_local_availability("¿me sirve el VERTIX 2 para trail?")
        assert not report.ok
        assert [u.slug for u in report.unavailable] == ["vertix-2"]
        assert "VERTIX 2" in report.unavailable[0].statement

    def test_the_report_never_offers_another_device_in_its_place(self) -> None:
        """Read back with the same scanner the check uses, so `COROS VERTIX` being a
        substring of `COROS VERTIX 2` cannot pass for a second device or fail as one."""
        report = check_local_availability("quiero un VERTIX 2")
        prose = " ".join(u.statement + " " + u.tradeoff for u in report.unavailable)
        assert [d.slug for d in devices_named(prose)] == ["vertix-2"], (
            "the refusal path named a replacement device. A substitution is the failure "
            "mode this check exists to prevent — see AGENTS.md, guardrail principle."
        )

    def test_the_report_carries_no_field_a_substitute_could_travel_in(self) -> None:
        fields = set(type(check_local_availability("VERTIX 2").unavailable[0]).model_fields)
        assert not fields & {"instead", "alternative", "substitute", "replacement", "closest"}

    def test_a_cleared_verdict_cannot_be_minted_for_an_absent_watch(self) -> None:
        with pytest.raises(ValidationError):
            LocalAvailabilityVerdict(slug="vertix-2", name="COROS VERTIX 2", is_available=False)  # type: ignore[arg-type]

    def test_straps_in_the_catalogue_do_not_make_the_watch_available(self) -> None:
        report = check_local_availability("VERTIX 2")
        assert report.unavailable[0].straps_listed is True
        assert not report.cleared

    def test_a_device_with_neither_watch_nor_strap_says_so(self) -> None:
        report = check_local_availability("mi COROS VERTIX de primera generación")
        assert report.unavailable[0].slug == "vertix"
        assert report.unavailable[0].straps_listed is False

    def test_a_watch_that_is_sold_here_clears(self) -> None:
        report = check_local_availability("estoy mirando el PACE 4")
        assert report.ok
        assert [c.slug for c in report.cleared] == ["pace-4"]
        assert report.cleared[0].is_available is True

    def test_a_model_number_coros_never_launched_resolves_to_nothing(self) -> None:
        """`apex 5` must not come back as the gen-1 APEX — devices.py refuses a numeric
        suffix on an unnumbered alias, and the prose scan has to preserve that."""
        report = check_local_availability("¿y el APEX 5?")
        assert report.ok
        assert not report.unavailable and not report.cleared

    def test_the_2s_is_not_the_2(self) -> None:
        assert [u.slug for u in check_local_availability("VERTIX 2S").unavailable] == ["vertix-2s"]

    def test_two_absent_watches_in_one_sentence_are_both_reported(self) -> None:
        report = check_local_availability("compara el VERTIX 2 con el PACE Pro")
        assert {u.slug for u in report.unavailable} == {"vertix-2", "pace-pro"}

    def test_a_millimetre_figure_is_not_read_as_a_model_number(self) -> None:
        assert check_local_availability("una correa APEX 22 mm").unavailable == ()

    def test_a_candidate_product_is_joined_by_id_and_not_by_its_title(self) -> None:
        renamed = product(PACE_4_ID, "coros-pace-4", "Reloj deportivo")
        assert [c.slug for c in check_local_availability("", [renamed]).cleared] == ["pace-4"]


class TestBuyNothingIsAVerdictNotASentence:
    def test_nothing_purchasable_is_buy_nothing(self) -> None:
        verdict = check_buy_nothing([SOLD_OUT])
        assert verdict.buy_nothing
        assert verdict.reason == "nothing_in_stock"
        assert verdict.advice_kind == "buy_nothing"

    def test_an_inconclusive_retrieval_is_not_a_buy_nothing(self) -> None:
        """A 429 is not a fact about stock. Reporting one as "nothing fits" fabricates
        an inventory claim out of a request that never completed."""
        verdict = check_buy_nothing([], retrieval_conclusive=False)
        assert not verdict.buy_nothing
        assert verdict.advice_kind == "insufficient_evidence"

    def test_everything_over_budget_is_buy_nothing_and_never_an_upsell(self) -> None:
        verdict = check_buy_nothing(CATALOG, budget=Budget(max_total_minor=50_000_000))
        assert verdict.buy_nothing
        assert verdict.reason == "over_budget"
        assert "$500.000" in verdict.detail

    def test_a_device_already_owned_is_buy_nothing(self) -> None:
        verdict = check_buy_nothing([PACE_4], owned=("pace-4",))
        assert verdict.buy_nothing
        assert verdict.reason == "already_owned"

    def test_owning_one_device_does_not_veto_a_different_one(self) -> None:
        assert not check_buy_nothing(CATALOG, owned=("pace-4",)).buy_nothing

    def test_something_that_fits_survives_as_a_recommendation(self) -> None:
        verdict = check_buy_nothing([PACE_4], budget=Budget(max_total_minor=PACE_4_PRICE))
        assert not verdict.buy_nothing
        assert verdict.reason == ""
        assert verdict.advice_kind == "recommend"

    def test_the_detail_is_never_blank_when_the_answer_is_buy_nothing(self) -> None:
        for verdict in (
            check_buy_nothing([]),
            check_buy_nothing([SOLD_OUT]),
            check_buy_nothing([PACE_4], owned=("pace-4",)),
            check_buy_nothing([PACE_4], budget=Budget(max_total_minor=1)),
        ):
            assert verdict.detail, f"{verdict.reason} reached the UI with nothing to say"


def test_buy_nothing_is_a_verdict_not_a_sentence() -> None:
    """`check_buy_nothing` returns a `BuyNothingVerdict`, and `reason` is a closed
    `Literal` — a model cannot mint its own excuse for "buy nothing" the way it could
    write one into a sentence. Only `check_buy_nothing`'s own code path can produce
    `buy_nothing=True`."""
    verdict = check_buy_nothing([SOLD_OUT])
    assert isinstance(verdict, BuyNothingVerdict)
    with pytest.raises(ValidationError):
        BuyNothingVerdict(buy_nothing=True, reason="el modelo decidio que no hay nada")  # type: ignore[arg-type]


class TestNothingRendersThatTheCatalogDoesNotBack:
    def test_a_product_id_the_feed_never_had_is_dropped(self) -> None:
        verdict = check_provenance([{"product_id": "0000", "variant_id": "v1"}], CATALOG)
        assert not verdict.ok
        assert verdict.renderable == ()
        assert verdict.dropped[0].reason == "not_in_catalog"

    def test_the_rendered_item_is_built_from_the_feed_and_not_from_the_model(self) -> None:
        verdict = check_provenance(
            [
                {
                    "product_id": PACE_4_ID,
                    "variant_id": PACE_4_VARIANT,
                    "title": "COROS PACE 4 Pro Max",
                    "price_minor": 1,
                    "product_url": "https://example.com/pace-4",
                    "rationale": "batería para tu volumen semanal",
                }
            ],
            CATALOG,
        )
        rendered = verdict.renderable[0]
        assert rendered.title == "COROS PACE 4"
        assert rendered.price_minor == PACE_4_PRICE
        assert rendered.product_url == "https://coros.com.co/products/coros-pace-4"
        assert rendered.rationale == "batería para tu volumen semanal"

    def test_a_price_the_model_invented_is_recorded_rather_than_quietly_corrected(self) -> None:
        verdict = check_provenance(
            [{"product_id": PACE_4_ID, "variant_id": PACE_4_VARIANT, "price_minor": 1}], CATALOG
        )
        assert [(m.field, m.claimed, m.actual) for m in verdict.mismatches] == [
            ("price_minor", "1", str(PACE_4_PRICE))
        ]

    def test_a_variant_the_product_does_not_have_is_dropped(self) -> None:
        verdict = check_provenance([{"product_id": PACE_4_ID, "variant_id": "made-up"}], CATALOG)
        assert verdict.dropped[0].reason == "unknown_variant"

    def test_a_product_with_two_variants_and_none_named_is_a_question_not_a_pick(self) -> None:
        verdict = check_provenance([{"product_id": APEX_4_ID}], CATALOG)
        assert verdict.dropped[0].reason == "variant_ambiguous"

    def test_a_single_variant_product_needs_no_variant_id(self) -> None:
        verdict = check_provenance([{"product_id": PACE_4_ID}], CATALOG)
        assert verdict.renderable[0].variant_id == PACE_4_VARIANT

    def test_a_hidden_gift_with_purchase_line_is_not_merchandise(self) -> None:
        gwp = product("8500", "regalo", "Regalo COROS").model_copy(update={"hidden": True})
        verdict = check_provenance([{"product_id": "8500"}], (*CATALOG, gwp))
        assert verdict.dropped[0].reason == "not_merchandise"


class TestOutOfStockIsReportedNotRaised:
    def test_a_sold_out_variant_is_rejected_with_a_reason(self) -> None:
        verdict = check_stock([item(product_id="9001", variant_id="n1", price_minor=125_000_000)], CATALOG)
        assert not verdict.ok
        assert verdict.items == ()
        assert verdict.rejected[0].reason == "out_of_stock"
        assert "COROS NOMAD" in verdict.rejected[0].detail

    def test_stock_is_read_from_the_catalog_and_never_off_the_candidate(self) -> None:
        """The adversarial case: the model asserts availability it does not have."""
        lying = {
            "product_id": "9001",
            "variant_id": "n1",
            "title": "COROS NOMAD",
            "product_url": "https://coros.com.co/products/coros-nomad",
            "price_minor": 125_000_000,
            "available": True,
        }
        assert check_stock([lying], CATALOG).rejected[0].reason == "out_of_stock"

    def test_an_item_the_catalog_does_not_hold_is_rejected_not_assumed_in_stock(self) -> None:
        verdict = check_stock([item(product_id="0000", variant_id="nope")], CATALOG)
        assert verdict.rejected[0].reason == "not_in_catalog"

    def test_an_in_stock_item_passes_through_unchanged(self) -> None:
        confirmed = item()
        assert check_stock([confirmed], CATALOG).items == (confirmed,)


class TestBudgetArithmeticHappensInCodeNotInTheModel:
    def test_no_budget_still_reports_a_total(self) -> None:
        verdict = check_budget([item()], Budget())
        assert verdict.ok
        assert verdict.total_minor == PACE_4_PRICE
        assert "$1.099.000" in verdict.message

    def test_a_selection_under_budget_reports_the_remainder(self) -> None:
        verdict = check_budget([item()], Budget(max_total_minor=150_000_000))
        assert verdict.ok
        assert verdict.over_by_minor == 0
        assert "$401.000" in verdict.message

    def test_over_budget_names_the_swap_that_closes_the_gap_first(self) -> None:
        items = [item(price_minor=209_900_000, product_id=APEX_4_ID), item()]
        verdict = check_budget(items, Budget(max_total_minor=150_000_000))
        assert not verdict.ok
        assert verdict.over_by_minor == 169_800_000
        assert [s.price_minor for s in verdict.swap_candidates] == [209_900_000]

    def test_nothing_fits_is_a_different_answer_from_over_by(self) -> None:
        verdict = check_budget([item()], Budget(max_total_minor=50_000_000))
        assert verdict.nothing_fits
        assert not verdict.swap_candidates

    def test_an_empty_selection_under_a_budget_is_nothing_fits_not_money_saved(self) -> None:
        verdict = check_budget([], Budget(max_total_minor=50_000_000))
        assert not verdict.ok
        assert verdict.nothing_fits
        assert "$500.000" in verdict.message

    def test_no_message_ever_shows_raw_minor_units(self) -> None:
        for verdict in (
            check_budget([item()], Budget()),
            check_budget([item()], Budget(max_total_minor=150_000_000)),
            check_budget([item()], Budget(max_total_minor=50_000_000)),
            check_budget([], Budget(max_total_minor=50_000_000)),
        ):
            assert "109900000" not in verdict.message
            assert "5000000" not in verdict.message

    def test_the_budget_is_never_widened_to_make_a_selection_fit(self) -> None:
        budget = Budget(max_total_minor=50_000_000)
        check_budget([item()], budget)
        assert budget.max_total_minor == 50_000_000


def test_a_hostile_turn_that_widens_the_budget_and_relabels_stock_is_fully_rejected() -> None:
    """Release blocker per KB `docs/lifeseek/SPEC.md` §1.5, the same citation
    `Budget.narrowed_to` carries: a textual "don't do this" was violated 0.46% of the
    time even when written down (Zelikman et al., STOP). The guarantee has to hold when
    both attacks land in the SAME turn, not one at a time in isolation."""
    budget = Budget(max_total_minor=50_000_000)

    lying_stock = {
        "product_id": "9001",
        "variant_id": "n1",
        "title": "COROS NOMAD",
        "product_url": "https://coros.com.co/products/coros-nomad",
        "price_minor": 125_000_000,
        "available": True,  # the relabel: the feed says agotado, the "model" says not
    }
    stock_verdict = check_stock([lying_stock], CATALOG)
    assert stock_verdict.items == ()
    assert stock_verdict.rejected[0].reason == "out_of_stock"

    with pytest.raises(ValueError):
        budget.narrowed_to(500_000_000)  # the widen
    assert budget.max_total_minor == 50_000_000, "the attempt to widen it left no trace"

    verdict = check_buy_nothing(CATALOG, budget=budget)
    assert verdict.buy_nothing
    assert verdict.reason == "over_budget", (
        "neither attack should have made anything purchasable: the lie about stock "
        "never reaches check_buy_nothing (it reads the feed), and the budget never widened"
    )


class TestPropertiesAreWhatTheModelInvents:
    """Retrieval stops it inventing products. Nothing stops it inventing their specs."""

    def test_a_battery_figure_no_description_states_is_unbacked(self) -> None:
        claims = find_unbacked_claims("Te da 90 horas de GPS.", [PACE_4])
        assert [(c.kind, c.text) for c in claims] == [("battery", "90 horas")]

    def test_a_battery_figure_the_description_states_is_backed(self) -> None:
        assert find_unbacked_claims("Hasta 38 horas de GPS.", [PACE_4]) == []

    def test_a_water_resistance_depth_is_unbacked(self) -> None:
        assert [c.kind for c in find_unbacked_claims("Sumergible hasta 100 m.", [PACE_4])] == [
            "water"
        ]

    def test_a_material_the_description_states_is_backed(self) -> None:
        assert find_unbacked_claims("Caja de titanio.", [PACE_4]) == []

    def test_a_material_it_does_not_state_is_not(self) -> None:
        assert [c.kind for c in find_unbacked_claims("Caja de acero inoxidable.", [PACE_4])] == [
            "material"
        ]

    def test_a_price_the_code_computed_is_backed(self) -> None:
        assert find_unbacked_claims("Cuesta $1.099.000.", [PACE_4]) == []

    def test_a_price_the_code_never_computed_is_unbacked(self) -> None:
        claims = find_unbacked_claims("Cuesta $980.000.", [PACE_4])
        assert [(c.kind, c.text) for c in claims] == [("price", "$980.000")]

    def test_the_colombian_dot_is_read_as_a_thousands_separator(self) -> None:
        """`$1.099.000` is a million pesos here, not one peso and change."""
        assert find_unbacked_claims("Cuesta $1.099.000 COP.", [PACE_4]) == []

    def test_a_budget_the_caller_whitelists_is_backed(self) -> None:
        assert (
            find_unbacked_claims("Tu tope de $2.000.000.", [PACE_4], allowed_minor=[200_000_000])
            == []
        )

    def test_a_weight_written_with_a_spanish_decimal_comma_is_backed(self) -> None:
        pod = product("8800", "pod-2", "COROS POD 2", description="Con un peso de solo 5,6 g.")
        assert find_unbacked_claims("Pesa 5,6 g.", [pod]) == []

    def test_the_model_cannot_back_a_claim_with_its_own_rationale(self) -> None:
        invented = item(rationale="tiene 200 horas de batería")
        assert [c.kind for c in find_unbacked_claims("Te da 200 horas.", [invented])] == ["battery"]

    def test_a_discount_is_unbacked_by_construction(self) -> None:
        """`compare_at_price` is deliberately unmapped, so no pre-discount number can
        reach the model at all. Even a true one has no source in this pipeline."""
        assert [c.kind for c in find_unbacked_claims("Está 20% de descuento.", [PACE_4])] == [
            "discount"
        ]

    def test_overlapping_matches_are_reported_once(self) -> None:
        claims = find_unbacked_claims("Aguanta 100 m de profundidad.", [PACE_4])
        assert len(claims) == 1

    def test_a_claim_carries_the_offsets_it_was_found_at(self) -> None:
        text = "Te da 90 horas de GPS."
        claim = find_unbacked_claims(text, [PACE_4])[0]
        assert text[claim.start : claim.end] == claim.text
        assert isinstance(claim, SpecClaim)


class TestScrubbingKeepsTheReasoningAndDropsTheClaim:
    def test_the_claim_goes_and_the_sentence_around_it_stays(self) -> None:
        out = scrub_prose("Es liviano, y aguanta 200 horas, ideal para trail.", [PACE_4])
        assert "200 horas" not in out
        assert "ideal para trail" in out

    def test_clean_prose_comes_back_unchanged(self) -> None:
        text = "Es el reloj más liviano de los que COROS vende aquí."
        assert scrub_prose(text, [PACE_4]) == text

    def test_empty_prose_is_empty_and_not_an_error(self) -> None:
        assert scrub_prose("", [PACE_4]) == ""

    def test_the_scrubbed_claims_reach_the_trace(self) -> None:
        scrub_prose("Sumergible hasta 100 m.", [PACE_4])
        payload = guardrail_events()[-1].payload
        assert payload["kinds"] == ["water"]


class TestTheVerdictsAreDataAndNotProse:
    def test_every_trace_payload_survives_the_wire(self) -> None:
        """The trace panel is a Reflex component, so a payload that will not serialise is
        a verdict the reviewer never sees."""
        check_provenance([{"product_id": PACE_4_ID}], CATALOG)
        check_stock([item(product_id="9001", variant_id="n1")], CATALOG)
        check_budget([item()], Budget(max_total_minor=50_000_000))
        check_local_availability("VERTIX 2")
        check_buy_nothing([SOLD_OUT])
        scrub_prose("Sumergible hasta 100 m.", [PACE_4])

        for event in guardrail_events():
            json.dumps(event.as_dict())

    def test_a_verdict_is_a_model_and_not_a_sentence(self) -> None:
        for verdict in (BudgetVerdict(), BuyNothingVerdict(), ProvenanceVerdict(), StockVerdict()):
            assert isinstance(verdict.model_dump(), dict)
