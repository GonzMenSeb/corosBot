"""The capability map.

One question runs through every test here: when this system cannot serve a request, does
it say so, or does it hand back an empty list that reads like "COROS has nothing for you"?
The map exists to make the second impossible, so most of these tests are attempts to
produce a silent empty result and watch them fail.

The other half is the distinction that carries B1: **capability is not availability.**
COROS Colombia sells a bike cadence sensor that is out of stock — that is a stock answer.
It sells no cycling power meter at all — that is a capability answer. Collapsing them
tells a person COROS does not make a cadence sensor, which is false.
"""

from __future__ import annotations

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from coros_core import trace
from coros_core.capability import (
    DEAD_ENDS,
    MAP,
    SURFACES,
    WITHHELD,
    CapabilityRequest,
    CapabilityVerdict,
    DeadEnd,
    Need,
    Surface,
    ToolId,
    as_result,
    capable_tools,
    check_capability,
)
from coros_core.outcomes import ToolOutcome

DECLARED: tuple[str, ...] = get_args(Need)
SURFACE_NAMES: tuple[str, ...] = get_args(Surface)


def ask(need: str, surface: Surface = "brujula") -> CapabilityRequest:
    return CapabilityRequest(need=need, surface=surface)


class TestAnEmptyMapIsNeverASilentEmptyResult:
    def test_a_verdict_with_no_tools_cannot_be_built_without_a_reason(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityVerdict(need="cycling_power", surface="brujula")

    def test_a_verdict_cannot_carry_tools_and_a_dead_end_at_once(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityVerdict(
                need="product_lookup",
                surface="brujula",
                tools=(ToolId.SEARCH_PRODUCTS,),
                dead_end=DEAD_ENDS["cycling_power"],
            )

    def test_an_undeclared_need_is_a_dead_end_and_not_an_empty_search(self) -> None:
        verdict = check_capability(ask("cycling_power_meter_maybe"))
        assert verdict.is_dead_end
        assert verdict.dead_end is not None
        assert verdict.dead_end.reason == "undeclared"
        assert verdict.dead_end.outcome is ToolOutcome.NO_CAPABILITY

    def test_a_dead_end_becomes_a_typed_result_and_never_an_empty_list(self) -> None:
        result = as_result(check_capability(ask("cycling_power")))
        assert result.outcome is ToolOutcome.NO_CAPABILITY
        assert result.outcome.needs_escalation
        assert not result.outcome.is_conclusive, (
            "NO_CAPABILITY must not read as a conclusive negative — nothing was searched. "
            "Update coros_core/outcomes.py and this test together."
        )
        assert result.detail

    def test_a_capable_need_becomes_an_ok_result_naming_its_tools(self) -> None:
        result = as_result(check_capability(ask("product_lookup")))
        assert result.outcome is ToolOutcome.OK
        assert result.data == [ToolId.SEARCH_PRODUCTS.value]

    def test_a_blank_need_is_a_dead_end_not_a_wildcard(self) -> None:
        verdict = check_capability(ask("   "))
        assert verdict.is_dead_end
        assert verdict.tools == ()


class TestCyclingPowerIsAHardDeadEnd:
    """COROS Colombia's whole cycling range is a cadence sensor and a speed sensor.
    Verified live 30 Jul 2026 over all 45 feed products: nothing measures power."""

    def test_no_tool_can_find_a_cycling_power_meter(self) -> None:
        assert capable_tools(ask("cycling_power")) == ()
        assert check_capability(ask("cycling_power")).dead_end is not None

    def test_it_is_a_dead_end_on_both_apps(self) -> None:
        for surface in SURFACE_NAMES:
            assert capable_tools(ask("cycling_power", surface)) == ()

    def test_the_dead_end_names_no_substitute(self) -> None:
        dead_end = DEAD_ENDS["cycling_power"]
        said = f"{dead_end.statement} {dead_end.tradeoff}".lower()
        for swap in ("cadencia", "velocidad", "pod", "reloj", "en su lugar", "alternativ"):
            assert swap not in said, (
                f"the cycling-power dead end offers {swap!r}. A capability we do not have "
                "is not answered with a product that does something else."
            )

    def test_the_dead_end_carries_no_field_a_substitute_could_travel_in(self) -> None:
        for field in ("alternative", "instead", "closest", "suggestion"):
            assert field not in DeadEnd.model_fields

    def test_a_dead_end_rejects_an_invented_field(self) -> None:
        with pytest.raises(ValidationError):
            DeadEnd(
                need="cycling_power",
                outcome=ToolOutcome.NO_CAPABILITY,
                reason="no_product",
                statement="x",
                instead="el sensor de velocidad",
            )

    def test_running_power_is_a_dead_end_because_coros_says_so(self) -> None:
        dead_end = DEAD_ENDS["running_power"]
        assert capable_tools(ask("running_power")) == ()
        assert dead_end.reason == "vendor_says_no"
        assert "pod 2" in dead_end.evidence.lower(), (
            "the running-power dead end has to carry COROS's own denial, which names the "
            "POD 2. Update AGENTS.md and this test together."
        )

    def test_ant_plus_is_a_dead_end(self) -> None:
        assert capable_tools(ask("ant_plus")) == ()
        assert DEAD_ENDS["ant_plus"].reason == "vendor_says_no"

    def test_every_dead_end_says_something_and_says_where_it_read_it(self) -> None:
        for need, dead_end in DEAD_ENDS.items():
            assert dead_end.statement.strip(), f"{need} has no statement"
            assert dead_end.evidence.strip(), f"{need} claims nothing backs it"


class TestOutOfStockIsNotADeadEnd:
    def test_cadence_is_capable_even_though_it_is_out_of_stock(self) -> None:
        assert capable_tools(ask("cycling_cadence")) == (ToolId.SEARCH_PRODUCTS,), (
            "COROS Colombia sells a bike cadence sensor and it is out of stock. That is a "
            "stock verdict, not a capability one — a dead end here would tell a person "
            "COROS does not make the thing it makes."
        )
        assert "cycling_cadence" not in DEAD_ENDS

    def test_speed_is_capable(self) -> None:
        assert capable_tools(ask("cycling_speed")) == (ToolId.SEARCH_PRODUCTS,)

    def test_a_dead_end_cannot_be_minted_for_something_merely_out_of_stock(self) -> None:
        for outcome in (ToolOutcome.OK, ToolOutcome.UNAVAILABLE, ToolOutcome.RATE_LIMITED):
            with pytest.raises(ValidationError):
                DeadEnd(
                    need="cycling_cadence",
                    outcome=outcome,
                    reason="no_product",
                    statement="agotado",
                    evidence="feed",
                )


class TestCheckoutNeedsAPersonNotATool:
    def test_placing_an_order_is_needs_human_and_not_no_capability(self) -> None:
        dead_end = DEAD_ENDS["place_order"]
        assert dead_end.outcome is ToolOutcome.NEEDS_HUMAN
        assert dead_end.reason == "human_gated"
        assert as_result(check_capability(ask("place_order"))).outcome is ToolOutcome.NEEDS_HUMAN

    def test_the_withheld_tools_are_absent_from_the_registry(self) -> None:
        registered = {t.value for t in ToolId}
        assert WITHHELD & registered == set(), (
            "create_cart/create_checkout became ToolIds. Human-in-the-loop here is "
            "enforced by absence from the tool list, so they must not be nameable as tools."
        )
        assert {"create_cart", "create_checkout"} <= WITHHELD

    def test_no_need_maps_to_a_withheld_tool(self) -> None:
        for need, tools in MAP.items():
            assert not ({t.value for t in tools} & WITHHELD), need


class TestTheMapIsScopedToTheAppThatIsRunning:
    def test_brujula_cannot_read_a_training_history(self) -> None:
        verdict = check_capability(ask("training_history", "brujula"))
        assert verdict.tools == ()
        assert verdict.dead_end is not None
        assert verdict.dead_end.reason == "other_surface"
        assert verdict.dead_end.outcome is ToolOutcome.NO_CAPABILITY

    def test_huella_can(self) -> None:
        assert capable_tools(ask("training_history", "huella")) == (ToolId.GET_TRAINING_SUMMARY,)

    def test_a_surface_dead_end_is_still_a_dead_end_and_not_an_empty_answer(self) -> None:
        result = as_result(check_capability(ask("training_history", "brujula")))
        assert result.outcome is ToolOutcome.NO_CAPABILITY
        assert result.detail

    def test_every_tool_is_exposed_on_at_least_one_surface(self) -> None:
        for tool in ToolId:
            assert SURFACES[tool], f"{tool.value} is registered and reachable from nowhere"
            assert set(SURFACES[tool]) <= set(SURFACE_NAMES)

    def test_an_unknown_surface_is_refused_at_the_boundary(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityRequest(need="product_lookup", surface="decabot")


class TestTheMapIsPureAndTotal:
    def test_capable_tools_leaves_no_trace_and_touches_nothing(self) -> None:
        before = len(trace.current())
        capable_tools(ask("cycling_power"))
        capable_tools(ask("product_recommendation"))
        assert len(trace.current()) == before, (
            "capable_tools is the pure map — check_capability is what emits. A pure "
            "function that logs cannot be called from a test or a prompt preview."
        )

    def test_every_declared_need_is_either_mapped_or_a_dead_end(self) -> None:
        mapped, dead = set(MAP), set(DEAD_ENDS)
        assert mapped | dead == set(DECLARED), (
            f"needs declared but unhandled: {set(DECLARED) - mapped - dead}. A need in the "
            "Literal that neither table answers is a silent empty result."
        )
        assert mapped & dead == set(), f"needs in both tables: {mapped & dead}"

    def test_no_mapped_need_is_mapped_to_nothing(self) -> None:
        for need, tools in MAP.items():
            assert tools, f"{need} is mapped to no tools — that belongs in DEAD_ENDS"

    def test_every_mapped_tool_is_a_registry_member(self) -> None:
        for need, tools in MAP.items():
            for tool in tools:
                assert isinstance(tool, ToolId), f"{need} names {tool!r}"

    def test_every_dead_end_is_keyed_by_its_own_need(self) -> None:
        for need, dead_end in DEAD_ENDS.items():
            assert dead_end.need == need

    def test_the_answer_does_not_change_between_two_calls(self) -> None:
        first = capable_tools(ask("product_recommendation"))
        second = capable_tools(ask("product_recommendation"))
        assert first == second
        assert isinstance(first, tuple)

    def test_a_caller_cannot_edit_the_map_through_what_it_is_handed(self) -> None:
        tools = capable_tools(ask("product_recommendation"))
        with pytest.raises((AttributeError, TypeError)):
            tools.append(ToolId.GET_TRAINING_SUMMARY)  # type: ignore[attr-defined]
        assert capable_tools(ask("product_recommendation")) == tools


class TestEveryDecisionLeavesATrace:
    def test_check_capability_emits_exactly_one_guardrail_event(self) -> None:
        check_capability(ask("cycling_power"))
        events = [e for e in trace.current() if e.event == "guardrail.capability"]
        assert len(events) == 1
        assert events[0].level == "guardrail"

    def test_a_cleared_need_is_traced_too(self) -> None:
        check_capability(ask("product_recommendation"))
        events = [e for e in trace.current() if e.event == "guardrail.capability"]
        assert len(events) == 1
        assert events[0].payload["tools"] == [t.value for t in MAP["product_recommendation"]]

    def test_the_event_never_carries_the_persons_own_words(self) -> None:
        secret = "potenciometro de mi vecino juan"
        check_capability(ask(secret))
        blob = json.dumps(
            [e.as_dict() for e in trace.current() if e.event == "guardrail.capability"],
            ensure_ascii=False,
        )
        assert "juan" not in blob and "vecino" not in blob, (
            "an undeclared need is free text a person typed. The trace records that it was "
            "undeclared and how long it was, never the text — an evidence bundle pastes "
            "trace payloads back into a model's context."
        )
        assert "undeclared" in blob


class TestAnAmbiguousNeedIsNormalisedNotGuessed:
    @pytest.mark.parametrize(
        "raw", ["Product_Lookup", "  product lookup  ", "PRODUCT   LOOKUP", "product-lookup"]
    )
    def test_case_spacing_and_dashes_normalise_to_the_declared_form(self, raw: str) -> None:
        assert capable_tools(ask(raw)) == (ToolId.SEARCH_PRODUCTS,)

    def test_an_accent_does_not_hide_a_declared_need(self) -> None:
        assert CapabilityRequest(need="ánt_plus", surface="brujula").need == "ant_plus"

    def test_a_spanish_synonym_nobody_declared_is_a_dead_end_not_a_guess(self) -> None:
        verdict = check_capability(ask("potenciometro"))
        assert verdict.dead_end is not None
        assert verdict.dead_end.reason == "undeclared", (
            "'potenciómetro' is ambiguous between cycling and running power, and COROS "
            "answers those two differently. An alias table that picks one is a guess."
        )
