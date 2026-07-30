"""The shared contract, pinned. Everything asserted here is a guarantee another lane
is allowed to build on without reading `coros_core`, so a failure means the contract
moved and the lanes that trusted it have to be told.
"""

from __future__ import annotations

import inspect
import types
import typing

import pytest
from pydantic import BaseModel, ConfigDict, HttpUrl, ValidationError
from reflex.utils.format import json_dumps

from coros_core import models, outcomes
from coros_core.models import (
    Advice,
    AdviceItem,
    AdviceSpec,
    Budget,
    CatalogProduct,
    CatalogVariant,
    Device,
    DeviceCase,
    Requirement,
    Url,
)
from coros_core.outcomes import ToolOutcome, ToolResult

PRODUCT_URL = "https://coros.com.co/products/coros-pace-4"
IMAGE_URL = "https://cdn.shopify.com/s/files/1/0000/0000/pace-4.jpg"


def item(**over) -> AdviceItem:
    base = {
        "product_id": "7839703466046",
        "title": "COROS PACE 4",
        "product_url": PRODUCT_URL,
        "image_url": IMAGE_URL,
        "price_minor": 1_099_000,
    }
    return AdviceItem(**{**base, **over})


def declared_models() -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    for module in (models, outcomes):
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == module.__name__:
                found.append(obj)
    return found


def frozen_models() -> list[type[BaseModel]]:
    return [m for m in declared_models() if m.model_config.get("frozen")]


class TestAUrlIsAStringOnTheWire:
    def test_a_raw_HttpUrl_serialises_to_null_through_reflex(self) -> None:
        class Raw(BaseModel):
            u: HttpUrl

        assert json_dumps({"u": Raw(u=PRODUCT_URL).u}) == '{"u": null}', (
            "Reflex 0.9.7 stopped emptying pydantic HttpUrl on the wire.\n"
            "If that is really fixed the Url alias can go — but change AGENTS.md and this\n"
            "test in the same commit, and re-check every product link and photo first."
        )

    def test_the_url_alias_survives_the_same_encoder(self) -> None:
        assert json_dumps({"u": item().product_url}) == '{"u": "%s"}' % PRODUCT_URL
        assert "null" not in json_dumps(item().model_dump())

    def test_the_url_alias_still_validates(self) -> None:
        class M(BaseModel):
            u: Url

        with pytest.raises(ValidationError):
            M(u="coros.com.co/products/pace-4")
        assert isinstance(M(u=PRODUCT_URL).u, str)


class TestAFrozenPolicyObjectIsFrozenAllTheWayDown:
    def test_assigning_to_a_frozen_field_raises(self) -> None:
        budget = Budget(max_total_minor=1_500_000)
        with pytest.raises(ValidationError):
            budget.max_total_minor = 9_000_000

    def test_a_list_field_would_have_stayed_mutable(self) -> None:
        """Why every collection below is a tuple: frozen=True overrides __setattr__ and
        nothing else, so a frozen model holding a list is not actually frozen."""

        class WouldBeFrozen(BaseModel):
            model_config = ConfigDict(frozen=True)
            xs: list[int] = []

        leaky = WouldBeFrozen(xs=[1])
        leaky.xs.append(99)
        assert leaky.xs == [1, 99]

    @pytest.mark.parametrize("model", frozen_models(), ids=lambda m: m.__name__)
    def test_no_frozen_model_declares_a_mutable_collection(self, model: type[BaseModel]) -> None:
        for name, field in model.model_fields.items():
            origin = typing.get_origin(field.annotation)
            assert origin not in (list, set, dict), (
                f"{model.__name__}.{name} is a {origin.__name__} on a frozen model.\n"
                "frozen=True blocks rebinding the attribute, not mutating what it points\n"
                "at. Use a tuple — pydantic coerces an incoming list for you."
            )

    @pytest.mark.parametrize("model", frozen_models(), ids=lambda m: m.__name__)
    def test_a_frozen_model_is_hashable(self, model: type[BaseModel]) -> None:
        assert model.__hash__ is not None

    def test_an_unknown_field_is_rejected_not_ignored(self) -> None:
        with pytest.raises(ValidationError):
            Budget(max_total_minor=1_000, ceiling_override=999_999_999)


class TestABudgetCanOnlyBeNarrowed:
    def test_narrowing_returns_a_new_budget(self) -> None:
        original = Budget(max_total_minor=2_000_000)
        assert original.narrowed_to(1_200_000).max_total_minor == 1_200_000
        assert original.max_total_minor == 2_000_000

    def test_widening_raises(self) -> None:
        with pytest.raises(ValueError):
            Budget(max_total_minor=1_200_000).narrowed_to(3_000_000)

    def test_an_unlimited_budget_allows_anything_and_can_still_be_narrowed(self) -> None:
        unlimited = Budget()
        assert unlimited.is_unlimited and unlimited.allows(10**12)
        assert unlimited.narrowed_to(500_000).max_total_minor == 500_000

    def test_a_negative_ceiling_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Budget(max_total_minor=-1)

    def test_allows_is_inclusive_of_the_ceiling(self) -> None:
        budget = Budget(max_total_minor=1_099_000)
        assert budget.allows(1_099_000)
        assert not budget.allows(1_099_001)


class TestAnAdviceSpecPinsTheRun:
    def test_equal_specs_share_a_digest_and_different_ones_do_not(self) -> None:
        a = AdviceSpec(question="¿Qué reloj para trail?")
        b = AdviceSpec(question="¿Qué reloj para trail?")
        c = AdviceSpec(question="¿Qué reloj para trail?", discipline="trail")
        assert a.digest == b.digest
        assert a.digest != c.digest

    def test_adding_a_requirement_leaves_the_original_untouched(self) -> None:
        spec = AdviceSpec(question="¿Qué correa para mi APEX 4?")
        grown = spec.with_requirements(Requirement(key="strap_mm", value=22, source="user"))
        assert spec.requirements == ()
        assert grown.requirements[0].value == 22
        assert grown.digest != spec.digest

    def test_the_budget_cannot_be_widened_through_the_spec(self) -> None:
        spec = AdviceSpec(question="algo barato", budget=Budget(max_total_minor=400_000))
        with pytest.raises(ValidationError):
            spec.budget = Budget(max_total_minor=9_000_000)
        with pytest.raises(ValueError):
            spec.budget.narrowed_to(9_000_000)

    def test_a_spec_can_be_used_as_a_dict_key(self) -> None:
        spec = AdviceSpec(question="hola", requirements=(Requirement(key="discipline", value="trail"),))
        assert {spec: "cached"}[spec] == "cached"


class TestOnlyPrimitivesCrossThePrivacyBoundary:
    @pytest.mark.parametrize(
        "value",
        [
            {"activities": [{"id": 1, "start_latlng": [4.6, -74.0]}]},
            ["ride", "run"],
            4.7523,
            None,
        ],
    )
    def test_only_primitives_fit_in_the_value(self, value) -> None:
        with pytest.raises(ValidationError):
            Requirement(key="sport_mix", value=value)

    def test_an_unlisted_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Requirement(key="resting_heart_rate", value=48)

    def test_an_extra_field_on_a_requirement_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Requirement(key="discipline", value="trail", strava_athlete_id=12345)

    def test_a_derived_requirement_must_declare_how_much_data_it_came_from(self) -> None:
        with pytest.raises(ValidationError):
            Requirement(key="weekly_hours_band", value="6-9", source="strava", derived=True)
        ok = Requirement(
            key="weekly_hours_band", value="6-9", source="strava", derived=True, sample_size=34, window_days=90
        )
        assert ok.derived and ok.sample_size == 34

    def test_a_stated_requirement_needs_no_sample(self) -> None:
        assert Requirement(key="budget_minor", value=1_500_000, source="user").sample_size == 0


class TestBuyNothingIsATypedOutcomeNotASentence:
    def test_a_recommendation_without_products_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Advice(kind="recommend", explanation="nada te sirve")

    def test_a_buy_nothing_carrying_products_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Advice(kind="buy_nothing", items=(item(),))

    def test_not_sold_locally_must_name_the_device(self) -> None:
        with pytest.raises(ValidationError):
            Advice(kind="not_sold_locally", explanation="ese no se vende aquí")
        named = Advice(kind="not_sold_locally", unavailable_devices=("pace-pro",))
        assert named.unavailable_devices == ("pace-pro",)

    def test_a_recommendation_totals_its_items_in_minor_units(self) -> None:
        advice = Advice(kind="recommend", items=(item(), item(price_minor=250_000)))
        assert advice.total_minor == 1_349_000


class TestTheCatalogShapeDoesNotAssumeDecathlon:
    def test_a_product_with_no_product_type_is_valid(self) -> None:
        """~52% of the COROS feed has an empty product_type, PACE 4 included."""
        product = CatalogProduct(product_id="1", handle="coros-pace-4", title="PACE 4", product_url=PRODUCT_URL)
        assert product.product_type == ""

    def test_out_of_stock_variants_leave_no_minimum_price(self) -> None:
        product = CatalogProduct(
            product_id="1",
            handle="coros-dura",
            title="DURA",
            product_url=PRODUCT_URL,
            variants=(CatalogVariant(variant_id="v1", price_minor=1_299_000, available=False),),
        )
        assert product.min_price_minor is None
        assert not product.in_stock


class TestTheDeviceRegistryKnowsWhatIsNotSoldHere:
    def test_a_watch_with_straps_but_no_watch_sku_is_representable(self) -> None:
        pace_pro = Device(slug="pace-pro", name="COROS PACE Pro", sold_locally=False)
        assert not pace_pro.sold_locally and pace_pro.cases == ()

    def test_strap_width_is_looked_up_by_case_size(self) -> None:
        apex_4 = Device(
            slug="apex-4",
            name="COROS APEX 4",
            sold_locally=True,
            cases=(DeviceCase(case_mm=42, strap_mm=22), DeviceCase(case_mm=46, strap_mm=24)),
        )
        assert apex_4.strap_mm_for(42) == 22
        assert apex_4.strap_mm_for(46) == 24
        assert apex_4.strap_mm_for(47) is None

    def test_a_watch_with_one_case_states_a_width_and_no_case_size(self) -> None:
        """COROS states a case size only for the APEX 4, the one device that ships in two.
        A strap width with `case_mm=None` is the normal row, not a half-filled one."""
        pace_4 = Device(
            slug="pace-4",
            name="COROS PACE 4",
            sold_locally=True,
            cases=(DeviceCase(strap_mm=22),),
        )
        assert pace_4.strap_mm_for() == 22
        assert pace_4.strap_mm_for(42) is None

    def test_a_case_needs_a_strap_width_even_when_it_has_no_case_size(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCase(case_mm=42)

    def test_a_device_carries_the_product_ids_it_is_joined_by(self) -> None:
        """Ids first, handles second: Shopify ids are immutable and one live handle
        describes a different product than its own title does."""
        nomad = Device(
            slug="nomad",
            name="COROS NOMAD",
            sold_locally=True,
            product_ids=("7426932899883",),
            handles=("coros-nomad",),
        )
        assert nomad.product_ids == ("7426932899883",)


class TestWeCouldNotCheckIsNotTheSameAsThereIsNothing:
    def test_a_timeout_is_not_evidence_of_absence(self) -> None:
        assert not ToolOutcome.TIMEOUT.is_conclusive
        assert ToolOutcome.TIMEOUT.is_transient

    def test_an_empty_upstream_answer_is_conclusive(self) -> None:
        assert ToolOutcome.UNAVAILABLE.is_conclusive
        assert not ToolOutcome.UNAVAILABLE.is_transient

    def test_no_capability_is_neither_a_result_nor_a_retry(self) -> None:
        assert not ToolOutcome.NO_CAPABILITY.is_conclusive
        assert not ToolOutcome.NO_CAPABILITY.is_transient

    def test_every_outcome_falls_in_exactly_one_class(self) -> None:
        for outcome in ToolOutcome:
            classes = [outcome.is_conclusive, outcome.is_transient, outcome.needs_escalation]
            assert sum(classes) == 1, (
                f"{outcome.name} is in {sum(classes)} classes, not 1.\n"
                "Callers branch on exactly one of conclusive / transient / needs_escalation;\n"
                "an outcome in none of them silently falls through every branch."
            )

    def test_the_eight_outcomes_are_the_eight_that_were_agreed(self) -> None:
        assert {o.name for o in ToolOutcome} == {
            "OK",
            "NOT_ELIGIBLE",
            "UNAVAILABLE",
            "RATE_LIMITED",
            "TIMEOUT",
            "UPSTREAM_ERROR",
            "NO_CAPABILITY",
            "NEEDS_HUMAN",
        }

    def test_a_failed_result_must_say_why(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(tool="search_products", outcome=ToolOutcome.UPSTREAM_ERROR)

    def test_an_ok_result_must_carry_data(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(tool="search_products", outcome=ToolOutcome.OK)
        assert ToolResult(tool="search_products", outcome=ToolOutcome.OK, data=[]).data == []

    def test_an_empty_list_is_a_result_and_not_an_error(self) -> None:
        result = ToolResult(tool="search_products", outcome=ToolOutcome.OK, data=[])
        assert result.outcome.is_ok and result.data == []


class TestEveryOptionalFieldHasAnExplicitDefault:
    @pytest.mark.parametrize("model", declared_models(), ids=lambda m: m.__name__)
    def test_a_nullable_field_is_never_required(self, model: type[BaseModel]) -> None:
        for name, field in model.model_fields.items():
            nullable = type(None) in typing.get_args(field.annotation) or isinstance(
                field.annotation, types.NoneType
            )
            assert not (nullable and field.is_required()), (
                f"{model.__name__}.{name} is `| None` with no default.\n"
                "In pydantic v2 that is a REQUIRED field that happens to accept None, which\n"
                "is never what anyone means. Give it an explicit default."
            )
