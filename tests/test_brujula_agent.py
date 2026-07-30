"""Brújula's agent layer: the tools the model may call and the prompts that stage it.

The question behind every test here is the one from `AGENTS.md`: is this guarantee
written in Python or only asked for in the prompt? `create_cart` is the headline —
it is not absent because the system prompt says so, it is absent because no
`ToolId` spells it and no declaration carries the name, so the model has no token
sequence that reaches a cart.

The second theme is subtler and specific to COROS. The whole catalogue is 43 visible
products in ONE storefront request, so a search that finds nothing has read
everything: "nothing matches" is *conclusive* evidence here, not the empty result of
a partial look. That is the property `check_buy_nothing(retrieval_conclusive=...)`
depends on, and it only holds while retrieval reads the snapshot rather than
navigating a paginated surface.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from google.genai import types

from brujula.agent import prompts, tools
from coros_core import catalog, devices, trace
from coros_core.capability import SURFACES, WITHHELD
from coros_core.models import CatalogProduct
from coros_core.outcomes import ToolOutcome

REPO = Path(__file__).resolve().parent.parent
FACTS = 'AGENTS.md "load-bearing facts"'

BRUJULA_TOOLS: frozenset[str] = frozenset(
    t.value for t, surfaces in SURFACES.items() if "brujula" in surfaces
)


@pytest.fixture(scope="module")
def products() -> tuple[CatalogProduct, ...]:
    """All 45, hidden included. The two gift-with-purchase shirts are in the feed and
    must not be reachable, so a snapshot that never carries them cannot prove it."""
    payload = json.loads((REPO / "fixtures" / "products.json").read_text())
    return catalog.normalize(payload, include_hidden=True)


@pytest.fixture(autouse=True)
def _snapshot(products: tuple[CatalogProduct, ...]):
    tools.bind_snapshot(tools.Snapshot(products=products))
    yield
    tools.bind_snapshot(None)


def declaration(name: str) -> types.FunctionDeclaration:
    return next(d for d in tools.DECLARATIONS if d.name == name)


class TestTheModelHasNoWayToReachACart:
    """Human-in-the-loop by omission. Every assertion here is an attempt to find a
    spelling of `create_cart` the model could emit, and to watch it not exist."""

    def test_no_declaration_carries_a_withheld_name(self) -> None:
        offered = {d.name for d in tools.DECLARATIONS}
        assert not offered & WITHHELD, (
            f"{offered & WITHHELD} is exposed as a model tool. Human-in-the-loop is enforced\n"
            "  by absence from this list, not by a prompt instruction — see AGENTS.md,\n"
            "  'module boundaries'. ucp.call_ucp() still reaches it from a click handler."
        )

    def test_no_dispatchable_tool_carries_a_withheld_name(self) -> None:
        assert not set(tools.DISPATCH) & WITHHELD

    def test_the_dispatch_table_is_exactly_brujulas_surface(self) -> None:
        assert set(tools.DISPATCH) == BRUJULA_TOOLS, (
            "capability.SURFACES and this dispatch table disagree about which tools Brújula\n"
            "  exposes. AGENTS.md's maintenance contract moves capability.py, tools.py and\n"
            "  prompts.py in one commit."
        )

    def test_every_dispatchable_tool_is_declared_to_the_model(self) -> None:
        assert {d.name for d in tools.DECLARATIONS} == set(tools.DISPATCH)

    def test_the_module_never_posts_to_ucp(self) -> None:
        """UCP is the cart surface. Nothing the model can call may touch it, which is
        also why `search_products` reads the snapshot instead of `search_catalog`."""
        source = (REPO / "apps/brujula/brujula/agent/tools.py").read_text()
        tree = ast.parse(source)
        reached = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in {"call_ucp", "rpc"}
        ]
        assert not reached, (
            f"tools.py reaches a UCP call on line(s) {reached}. `create_cart` lives behind\n"
            "  that client; a model-facing module that can post to it has re-opened the\n"
            "  door this file exists to keep shut."
        )


class TestTheCatalogueIsPartitionedAndNothingIsHidden:
    """`list_collections` answers "what does COROS Colombia actually sell". A product
    that belongs to no group is a product the model can never be shown."""

    def test_the_groups_are_registry_derived_not_feed_typed(self) -> None:
        assert [g.handle for g in tools.GROUPS] == ["relojes", "correas", "accesorios"]

    def test_every_visible_product_lands_in_exactly_one_group(
        self, products: tuple[CatalogProduct, ...]
    ) -> None:
        visible = [p for p in products if not p.hidden]
        for product in visible:
            holders = [g.handle for g in tools.GROUPS if g.holds(product)]
            assert len(holders) == 1, (
                f"{product.handle!r} lands in {holders}. The groups have to partition the\n"
                "  snapshot: two homes double-count it and none makes it unreachable."
            )

    def test_the_partition_matches_the_two_registry_tables(
        self, products: tuple[CatalogProduct, ...]
    ) -> None:
        visible = [p for p in products if not p.hidden]
        counted = {g.handle: sum(1 for p in visible if g.holds(p)) for g in tools.GROUPS}
        assert counted == {"relojes": 4, "correas": 26, "accesorios": 13}, (
            f"the snapshot partitions as {counted}. {FACTS} records 4 devices sold in\n"
            "  Colombia and 26 strap rows; the remainder is what devices.py does not name.\n"
            "  If the feed really changed, update devices.py, this test and the registry."
        )

    def test_a_group_never_reads_product_type_or_tags(self) -> None:
        """`product_type` is empty on 24 of 45 and wrong on the DURA; tags carry
        uncurated compatibility claims. `devices.py` is the only authority on both."""
        source = (REPO / "apps/brujula/brujula/agent/tools.py").read_text()
        offenders = [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute) and node.attr in {"product_type", "tags"}
        ]
        assert not offenders, (
            f"tools.py reads product_type/tags on line(s) {offenders}. {FACTS}: the field is\n"
            "  empty on PACE 4 and says `Relojes GPS` for a bike computer, and a tag like\n"
            "  `APEX Pro` on a charger is a compatibility claim devices.py has to own."
        )

    async def test_listing_the_groups_reports_a_count_per_group(self) -> None:
        result = await tools.list_collections()
        assert result.outcome is ToolOutcome.OK
        handles = {c["handle"]: c["count"] for c in result.data["collections"]}
        assert handles == {"relojes": 4, "correas": 26, "accesorios": 13}

    async def test_an_unknown_handle_is_answered_with_the_live_ones(self) -> None:
        result = await tools.get_collection_products(handle="hiking-boots")
        assert result.outcome is ToolOutcome.NOT_ELIGIBLE
        assert "relojes" in result.detail and "correas" in result.detail
        assert result.data is None

    async def test_a_known_handle_returns_that_group_and_only_that_group(self) -> None:
        result = await tools.get_collection_products(handle="relojes")
        assert result.outcome is ToolOutcome.OK
        assert result.data["count"] == 4
        assert all(p["group"] == "relojes" for p in result.data["products"])


class TestATransientFailureIsNeverReportedAsAnEmptyCatalogue:
    """The failure this whole layer exists to prevent: a storefront 429 rendered as
    "COROS has nothing like that"."""

    async def test_an_unread_snapshot_is_not_an_empty_result(self) -> None:
        tools.bind_snapshot(None)
        for call in (
            tools.list_collections(),
            tools.get_collection_products(handle="relojes"),
            tools.search_products(query="reloj"),
        ):
            result = await call
            assert result.outcome is ToolOutcome.UPSTREAM_ERROR, (
                f"{result.tool} answered {result.outcome.name} with no snapshot bound. Nothing\n"
                "  was read, so nothing can be said about the catalogue."
            )
            assert result.detail

    async def test_a_rate_limited_snapshot_stays_rate_limited(self) -> None:
        tools.bind_snapshot(
            tools.Snapshot(outcome=ToolOutcome.RATE_LIMITED, detail="storefront 429")
        )
        result = await tools.search_products(query="reloj")
        assert result.outcome is ToolOutcome.RATE_LIMITED
        assert result.outcome.is_transient and not result.outcome.is_conclusive

    def test_a_snapshot_cannot_claim_ok_and_carry_nothing(self) -> None:
        with pytest.raises(ValueError):
            tools.Snapshot(outcome=ToolOutcome.OK, products=())

    def test_a_failed_snapshot_has_to_say_why(self) -> None:
        with pytest.raises(ValueError):
            tools.Snapshot(outcome=ToolOutcome.RATE_LIMITED)


class TestAZeroResultSearchIsConclusiveBecauseTheCatalogueIsOnePage:
    """43 products in one request means a search has read all of them. This is the
    property `check_buy_nothing(retrieval_conclusive=True)` rests on, and it is why
    `search_products` never calls UCP's `search_catalog`."""

    async def test_a_search_that_matches_nothing_is_conclusive(self) -> None:
        result = await tools.search_products(query="casco de ciclismo")
        assert result.outcome is ToolOutcome.UNAVAILABLE
        assert result.outcome.is_conclusive
        assert result.detail

    async def test_a_search_reads_every_visible_product(self) -> None:
        result = await tools.search_products(query="correa", limit=99)
        assert result.data["searched"] == 43

    async def test_an_empty_query_is_refused_rather_than_answered_with_everything(self) -> None:
        result = await tools.search_products(query="   ")
        assert result.outcome is ToolOutcome.NOT_ELIGIBLE
        assert result.data is None

    async def test_a_match_is_accent_insensitive(self) -> None:
        with_accent = await tools.search_products(query="Estándar")
        without = await tools.search_products(query="estandar")
        assert with_accent.outcome is ToolOutcome.OK
        assert [p["product_id"] for p in with_accent.data["products"]] == [
            p["product_id"] for p in without.data["products"]
        ]

    async def test_every_query_token_has_to_match(self) -> None:
        """An OR match over 43 products returns most of them for any query, which reads
        as a recommendation engine and is really a shuffled catalogue."""
        both = await tools.search_products(query="correa nomad", limit=99)
        one = await tools.search_products(query="correa", limit=99)
        assert both.data["matched"] < one.data["matched"]
        assert all("nomad" in p["title"].lower() for p in both.data["products"])

    async def test_a_variant_option_is_searchable_and_not_only_the_title(self) -> None:
        """`Material de la correa: Nylon` is how the PACE 4 says it comes with a nylon
        strap. Its title does not, so a title-only haystack answers "no" to a question
        the catalogue answers "yes" to."""
        result = await tools.search_products(query="pace 4 nylon", limit=99)
        handles = {p["product_handle"] for p in result.data["products"]}
        assert "coros-pace-4" in handles

    async def test_the_result_is_capped_and_says_how_many_it_dropped(self) -> None:
        result = await tools.search_products(query="correa", limit=3)
        assert len(result.data["products"]) == 3
        assert result.data["matched"] > 3


class TestAmbiguityIsAQuestionNeverAPick:
    async def test_an_apex_4_with_no_case_is_asked_about(self) -> None:
        result = await tools.lookup_device_compat(device="APEX 4")
        assert result.outcome is ToolOutcome.NOT_ELIGIBLE, (
            f"{FACTS}: the APEX 4 is the only device with two cases and they take different\n"
            "  widths. Answering with one of them is a guess; answering with neither reads\n"
            "  as 'COROS sells no APEX 4 straps', which is false."
        )
        assert "42" in result.detail and "46" in result.detail
        assert result.data is None

    async def test_an_apex_4_with_a_case_is_answered(self) -> None:
        result = await tools.lookup_device_compat(device="APEX 4", case_mm=46)
        assert result.outcome is ToolOutcome.OK
        assert result.data["strap_mm"] == 24
        assert result.data["straps"]

    async def test_a_case_the_registry_does_not_have_is_refused(self) -> None:
        result = await tools.lookup_device_compat(device="APEX 4", case_mm=44)
        assert result.outcome is ToolOutcome.NOT_ELIGIBLE
        assert result.data is None

    async def test_an_unknown_device_is_refused_with_the_names_we_do_know(self) -> None:
        result = await tools.lookup_device_compat(device="Garmin Fenix 8")
        assert result.outcome is ToolOutcome.NOT_ELIGIBLE
        assert "PACE 4" in result.detail

    async def test_the_dura_reports_no_strap_rather_than_a_plausible_width(self) -> None:
        result = await tools.lookup_device_compat(device="DURA")
        assert result.outcome is ToolOutcome.OK
        assert result.data["strap_mm"] is None
        assert result.data["straps"] == []
        assert result.data["note"]


class TestADeviceWeDoNotSellIsNamedNeverSwapped:
    async def test_a_watch_absent_from_colombia_is_reported_as_absent(self) -> None:
        result = await tools.lookup_device_compat(device="PACE 3")
        assert result.outcome is ToolOutcome.OK
        assert result.data["sold_locally"] is False

    async def test_its_straps_are_still_listed_because_they_are_really_here(self) -> None:
        result = await tools.lookup_device_compat(device="PACE 3")
        assert result.data["straps"], (
            f"{FACTS}: ten devices are strap-only in this catalogue. 'we do not sell that\n"
            "  watch' and 'we sell nothing for it' are different sentences."
        )

    async def test_no_field_offers_a_substitute(self) -> None:
        result = await tools.lookup_device_compat(device="VERTIX 2")
        assert not {"alternative", "instead", "closest", "similar"} & set(result.data)


class TestNothingUntrustedOrUnitAmbiguousReachesTheModel:
    def test_slim_emits_a_fixed_whitelist(self, products: tuple[CatalogProduct, ...]) -> None:
        expected = {
            "product_id",
            "product_handle",
            "title",
            "product_url",
            "image_url",
            "price_minor",
            "in_stock",
            "group",
            "device",
            "option_names",
            "variants",
        }
        for product in products:
            assert set(tools._slim(product)) == expected

    def test_no_vendor_prose_is_forwarded(self, products: tuple[CatalogProduct, ...]) -> None:
        """`body_html`'s largest entry is 36 KB of a BeeFree email template, and even
        sanitised it is the injection surface plus an invitation to read a spec out of
        marketing copy. Specs render from data; the registry carries the widths."""
        described = next(p for p in products if p.description)
        assert "description" not in tools._slim(described)

    def test_a_price_reaches_the_model_only_in_minor_units(
        self, products: tuple[CatalogProduct, ...]
    ) -> None:
        for product in products:
            slim = tools._slim(product)
            assert not [k for k in slim if k.startswith("price") and k != "price_minor"], (
                f"{FACTS}: the feed's `price` is MAJOR and UCP's is MINOR, 100x apart. Only\n"
                "  money.py converts, and only minor units cross into a prompt."
            )
            for variant in slim["variants"]:
                assert set(variant) == {"variant_id", "label", "price_minor", "available"}

    def test_a_placeholder_variant_label_is_not_offered_as_a_choice(
        self, products: tuple[CatalogProduct, ...]
    ) -> None:
        """`Default Title` is Shopify's placeholder on 11 of these products. Rendered
        verbatim it reads to a shopper as a choice they have to make."""
        sensor = next(p for p in products if p.handle == "coros-bike-speed-sensor")
        assert tools._slim(sensor)["variants"][0]["label"] == ""
        assert tools._slim(sensor)["option_names"] == []

    def test_a_title_is_truncated(self) -> None:
        long = CatalogProduct(
            product_id="1",
            handle="h" * 400,
            title="t" * 400,
            product_url="https://coros.com.co/products/x",
        )
        slim = tools._slim(long)
        assert len(slim["title"]) == tools.TITLE_CHARS
        assert len(slim["product_handle"]) == tools.TITLE_CHARS

    def test_the_variant_list_is_capped(self, products: tuple[CatalogProduct, ...]) -> None:
        widest = max(products, key=lambda p: len(p.variants))
        assert len(tools._slim(widest)["variants"]) <= tools.MAX_VARIANTS

    def test_a_registry_device_is_named_by_slug_not_guessed_from_a_title(
        self, products: tuple[CatalogProduct, ...]
    ) -> None:
        pace4 = next(p for p in products if p.handle == "coros-pace-4")
        assert tools._slim(pace4)["device"] == "pace-4"
        assert devices.get("pace-4") is not None


class TestEveryToolCallLeavesATrace:
    async def test_a_search_emits_counts_and_never_the_query(self, sink) -> None:
        await tools.search_products(query="correa de nylon morada")
        event = next(e for e in sink if e.event == "tool.search_products")
        assert "morada" not in json.dumps(event.payload)
        assert event.payload["matched"] >= 0

    async def test_a_rejected_handle_is_a_guardrail_event(self, sink) -> None:
        await tools.get_collection_products(handle="zapatos")
        event = next(e for e in sink if e.event == "guardrail.handle_rejected")
        assert event.level == "guardrail"

    async def test_an_unanswered_case_question_is_a_guardrail_event(self, sink) -> None:
        await tools.lookup_device_compat(device="apex 4")
        event = next(e for e in sink if e.event == "guardrail.case_unspecified")
        assert event.level == "guardrail"


class TestTheStagedPipelineIsWrittenDownStageByStage:
    """Brújula is a staged pipeline, not one tool loop: a stage that dies resumes,
    and each stage's contract is a prompt someone can review."""

    STAGES = (
        "GATE_PROMPT",
        "INTERVIEW_PROMPT",
        "REQUIREMENT_PROMPT",
        "RETRIEVE_PROMPT",
        "SELECT_PROMPT",
        "PRESENT_PROMPT",
    )

    def test_every_stage_has_a_prompt(self) -> None:
        for stage in self.STAGES:
            assert getattr(prompts, stage).strip()

    def test_every_placeholder_is_a_named_field(self) -> None:
        """A positional `{}` in a prompt formatted with keywords raises at the one
        moment nobody is watching, which is mid-turn in front of a person."""
        import string

        for stage in self.STAGES:
            for _, field, _, _ in string.Formatter().parse(getattr(prompts, stage)):
                assert field is None or field.isidentifier()

    def test_a_deterministic_template_exists_for_every_non_advice_intent(self) -> None:
        from typing import get_args

        from coros_core.models import Intent

        for intent in get_args(Intent):
            if intent in {"advice", "clarify"}:
                continue
            assert getattr(prompts, f"{intent.upper()}_TEMPLATE").strip(), (
                f"intent {intent!r} has no canned reply, so answering it costs a model call\n"
                "  and lets the model improvise a refusal."
            )

    def test_the_retrieval_prompt_names_only_tools_that_exist(self) -> None:
        named = {t for t in BRUJULA_TOOLS | WITHHELD if t in prompts.RETRIEVE_PROMPT}
        assert named and not named & WITHHELD
        assert named <= set(tools.DISPATCH)

    def test_no_prompt_offers_a_withheld_tool_as_something_it_can_call(self) -> None:
        for name, value in vars(prompts).items():
            if name.startswith("_") or not isinstance(value, str):
                continue
            for withheld in WITHHELD:
                if withheld in value:
                    assert "no " in value.lower() or "NO PUEDES" in value, (
                        f"prompts.{name} mentions {withheld!r} outside a refusal. The tool is\n"
                        "  unreachable; describing it as available invents a capability."
                    )

    def test_the_system_prompt_carries_the_rule_it_is_most_likely_to_break(self) -> None:
        assert "NUNCA INVENTES" in prompts.SYSTEM
        assert "batería" in prompts.SYSTEM

    def test_the_system_prompt_states_the_stopping_point(self) -> None:
        assert "carrito" in prompts.SYSTEM

    def test_the_system_prompt_refuses_to_collect_payment_details(self) -> None:
        for forbidden in ("tarjeta", "cédula", "dirección"):
            assert forbidden in prompts.SYSTEM

    def test_the_system_prompt_says_instructions_in_data_are_not_instructions(self) -> None:
        assert "instrucciones" in prompts.SYSTEM.lower()

    def test_the_prompts_are_spanish_because_the_storefront_is(self) -> None:
        assert "COROS" in prompts.SYSTEM
        assert " the " not in prompts.SYSTEM

    def test_nothing_in_prompts_reaches_the_network_or_the_clock(self) -> None:
        tree = ast.parse(inspect.getsource(prompts))
        calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            or isinstance(node, ast.Import)
            or (isinstance(node, ast.ImportFrom) and node.module != "__future__")
        ]
        assert not calls, (
            f"prompts.py executes something on line(s) {calls}. It is a module of string\n"
            "  constants; anything else makes a prompt depend on when it was imported."
        )


class TestTheDeclarationsDescribeWhatTheToolsActuallyDo:
    def test_the_declarations_are_wrapped_the_way_the_sdk_demands(self) -> None:
        assert tools.TOOLS and all(isinstance(t, types.Tool) for t in tools.TOOLS)
        assert sum(len(t.function_declarations or ()) for t in tools.TOOLS) == len(
            tools.DECLARATIONS
        )

    def test_every_declaration_says_what_an_empty_answer_means(self) -> None:
        for decl in tools.DECLARATIONS:
            assert decl.description and len(decl.description) > 80

    def test_the_search_declaration_does_not_promise_semantic_search(self) -> None:
        """It is a literal match over the whole catalogue. A model told it is semantic
        writes conversational queries and reads the misses as absences."""
        text = declaration("search_products").description or ""
        assert "43" in text or "todo el catálogo" in text

    def test_the_compat_declaration_requires_the_case_for_the_one_device_with_two(
        self,
    ) -> None:
        text = declaration("lookup_device_compat").description or ""
        assert "APEX 4" in text

    def test_no_declaration_takes_a_price_or_a_budget_from_the_model(self) -> None:
        """Budget arithmetic is integer arithmetic in code. A tool that accepts a
        max price lets the model widen one by passing a bigger number."""
        for decl in tools.DECLARATIONS:
            props = (decl.parameters.properties or {}) if decl.parameters else {}
            assert not {k for k in props if "price" in k or "budget" in k}


class TestSlimmingIsWhatBoundsThePrompt:
    async def test_a_whole_group_of_straps_stays_small(self) -> None:
        result = await tools.get_collection_products(handle="correas", limit=99)
        rendered = json.dumps(result.data, ensure_ascii=False)
        assert len(rendered) < 30_000, (
            f"the strap group renders to {len(rendered)} characters. _slim() is what keeps a\n"
            "  retrieval stage inside a prompt; an unbounded one silently truncates upstream."
        )

    async def test_the_group_listing_is_a_handful_of_lines(self) -> None:
        result = await tools.list_collections()
        assert len(json.dumps(result.data)) < 2_000


class TestTheSnapshotIsPerTurnAndNotProcessWide:
    def test_binding_none_forgets_the_previous_turn(self) -> None:
        tools.bind_snapshot(None)
        assert tools.snapshot() is None

    async def test_two_turns_do_not_share_a_catalogue(
        self, products: tuple[CatalogProduct, ...]
    ) -> None:
        tools.bind_snapshot(tools.Snapshot(products=products[:1]))
        first = await tools.list_collections()
        tools.bind_snapshot(tools.Snapshot(products=products))
        second = await tools.list_collections()
        assert first.data["total"] != second.data["total"]


class TestAToolResponseIsSafeToHandBackToTheModel:
    async def test_a_response_carries_the_outcome_so_a_refusal_is_not_silence(self) -> None:
        tools.bind_snapshot(None)
        result = await tools.search_products(query="reloj")
        response = tools.as_response(result)
        assert response["outcome"] == ToolOutcome.UPSTREAM_ERROR.value
        assert response["detail"]
        assert "data" not in response

    async def test_a_response_is_json_serialisable(self) -> None:
        result = await tools.get_collection_products(handle="relojes")
        json.dumps(tools.as_response(result))

    async def test_a_dispatch_by_name_returns_a_typed_result(self) -> None:
        for name, fn in tools.DISPATCH.items():
            result = await fn(**_ARGS[name])
            assert result.tool == name
            assert isinstance(result.outcome, ToolOutcome)

    async def test_an_unknown_argument_is_ignored_rather_than_crashing_the_turn(self) -> None:
        result = await tools.search_products(query="correa", nonsense=True, limit=2)
        assert result.outcome is ToolOutcome.OK


_ARGS: dict[str, dict[str, Any]] = {
    "list_collections": {},
    "get_collection_products": {"handle": "relojes"},
    "search_products": {"query": "correa"},
    "lookup_device_compat": {"device": "PACE 4"},
}


@pytest.fixture
def sink():
    events: list[trace.TraceEvent] = []
    trace.bind_sink(events)
    yield events
    trace.bind_sink(None)
