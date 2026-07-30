"""One `AGENTS.md` "load-bearing facts" entry per test, so a COROS-side change is
reported as a named contract break, not a mystery outage during a demo.

Offline tests run against `fixtures/products.json` and cost nothing. Live probes carry
`@pytest.mark.live`, run sequentially inside one module-scoped fixture, and are spaced
`SPACING` apart — copied from DecaBot `tests/test_contracts.py:150-175`, the same
one-call-at-a-time discipline `ucp.py` and `catalog.py` already latch to after a 429.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from google import genai
from google.genai import errors, types

from coros_core import capability, catalog, gemini, ucp
from coros_core.money import major_string_to_minor

FACTS = 'AGENTS.md "load-bearing facts"'
REPO = Path(__file__).resolve().parent.parent

# Measured 30 Jul 2026: a lone request is served throughout a storefront lockout and a
# burst is what trips one. This file never bursts, live or otherwise.
SPACING = 1.5

PACE4_PRODUCT_ID = 7752529543211
PACE4_PRODUCT_GID = f"gid://shopify/Product/{PACE4_PRODUCT_ID}"
PACE4_VARIANT_ID = 44066526363691
PACE4_VARIANT_GID = f"gid://shopify/ProductVariant/{PACE4_VARIANT_ID}"


def _payload(method: str, *, profile: bool, **params: Any) -> dict[str, Any]:
    arguments = {"meta": {"ucp-agent": {"profile": ucp.PROF}}} if profile else {}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "arguments": arguments}}


async def _probe() -> dict[str, Any]:
    out: dict[str, Any] = {"rate_limited": False}

    async with httpx.AsyncClient(timeout=45, headers={"Content-Type": "application/json"}) as raw:

        async def post(payload: dict[str, Any]) -> httpx.Response:
            await asyncio.sleep(SPACING)
            resp = await raw.post(ucp.EP, json=payload)
            if resp.status_code == 429:
                out["rate_limited"] = True
            return resp

        r = await post(_payload("initialize", profile=False))
        out["initialize_no_profile_status"] = r.status_code
        out["initialize_no_profile_body"] = r.json()

        r = await post(_payload("initialize", profile=True))
        out["initialize_with_profile_status"] = r.status_code
        out["initialize_with_profile_body"] = r.json()

        r = await post(_payload("tools/list", profile=True))
        out["tools_list_status"] = r.status_code
        out["tools_list_body"] = r.json()

    if out["rate_limited"]:
        return out

    try:
        await asyncio.sleep(SPACING)
        out["pace4"] = await ucp.call_ucp(
            "get_product", {"catalog": {"id": PACE4_PRODUCT_GID, "context": ucp.CONTEXT}}
        )
    except ucp.UcpRateLimited:
        out["rate_limited"] = True
    finally:
        await ucp.aclose()

    return out


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    return asyncio.run(_probe())


@pytest.fixture(scope="module")
def mcp(live: dict[str, Any]) -> dict[str, Any]:
    """A lockout is COROS's own limiter working as designed, not a contract
    violation — skip rather than fail, per the no-retry/no-poll convention."""
    if live["rate_limited"]:
        pytest.skip(
            "COROS UCP/storefront rate-limited — a lockout is not a contract violation. "
            f"{FACTS} says retrying or polling during one only prolongs it."
        )
    return live


class TestToolsListNeedsTheProfileNotAnInitialize:
    """`ucp.py` never calls `initialize` before a tool. What a caller actually needs
    is the profile in `arguments.meta` — not a prior handshake, and not the method
    name `initialize` itself."""

    @pytest.mark.live
    def test_initialize_fails_and_tools_list_succeeds(self, mcp: dict[str, Any]) -> None:
        assert mcp["initialize_no_profile_status"] == 422, (
            f"initialize without the profile no longer returns HTTP 422 (got "
            f"{mcp['initialize_no_profile_status']}). {FACTS} says every method, initialize "
            "included, fails without params.arguments.meta['ucp-agent'].profile — a naive "
            "handshake attempt fails for the same reason every other call would. If COROS "
            "genuinely relaxed this, update AGENTS.md and this test together."
        )
        err = mcp["initialize_no_profile_body"].get("error", {})
        assert err.get("code") == -32001, (
            f"initialize's error code changed (got {err}). {FACTS} says a missing profile is "
            "always -32001 'UCP discovery failed'. Update AGENTS.md and this test together."
        )

        assert mcp["tools_list_status"] == 200, (
            f"tools/list failed even though no initialize call preceded it in this probe (got "
            f"HTTP {mcp['tools_list_status']}: {mcp['tools_list_body']}). {FACTS} says the "
            "profile IS the whole handshake and ucp.py deliberately never calls initialize — "
            "if tools/list now needs one, ucp.py has to change, not just this test."
        )
        tools = {t["name"] for t in mcp["tools_list_body"]["result"]["tools"]}
        assert len(tools) == 13, (
            f"tools/list returned {len(tools)} tools, not 13 (got {sorted(tools)}). {FACTS} "
            "pins the count; a change here means the tool surface moved. Update AGENTS.md and "
            "this test together."
        )
        assert "create_cart" in tools and "create_checkout" in tools, (
            f"{FACTS}: create_cart and create_checkout are withheld from the MODEL's tool list "
            "by app-level omission, not because COROS hides them from the UCP surface — they "
            "must still exist here for that guardrail's premise to hold."
        )

    @pytest.mark.live
    def test_initialize_also_succeeds_once_the_profile_is_present(self, mcp: dict[str, Any]) -> None:
        """This corrects the plan's original claim (and an earlier reading of this
        registry) that `initialize` fails unconditionally, the way it does against
        DecaBot's Decathlon. Do not "fix" a passing `initialize` back to failing."""
        assert mcp["initialize_with_profile_status"] == 200, (
            f"initialize WITH the profile no longer returns HTTP 200 (got "
            f"{mcp['initialize_with_profile_status']}). {FACTS} says it succeeds here — this is "
            "a real divergence from DecaBot, where initialize fails unconditionally. Update "
            "AGENTS.md and this test together if COROS changed."
        )
        server = mcp["initialize_with_profile_body"]["result"]["serverInfo"]
        assert server["name"] == "universal-commerce", (
            f"serverInfo.name changed to {server['name']!r}. {FACTS} pins this string."
        )


class TestThePriceScaleIsNotADisplayBug:
    """Storefront and UCP publish the same variant at two scales, exactly 100x
    apart. `money.py` is the only place that conversion may happen."""

    @pytest.mark.live
    def test_ucp_amount_is_one_hundred_times_the_feed_price(self, mcp: dict[str, Any]) -> None:
        variant = next(
            v for v in mcp["pace4"]["product"]["variants"] if v["id"] == PACE4_VARIANT_GID
        )
        ucp_minor = variant["price"]["amount"]

        feed = json.loads((REPO / "fixtures" / "products.json").read_text())
        product = next(p for p in feed["products"] if p["id"] == PACE4_PRODUCT_ID)
        feed_variant = next(v for v in product["variants"] if v["id"] == PACE4_VARIANT_ID)
        feed_major = feed_variant["price"]

        assert ucp_minor == round(float(feed_major) * 100), (
            f"UCP's amount ({ucp_minor}) is no longer exactly 100x the feed's major price "
            f"({feed_major!r}). {FACTS} says this is a unit difference, not a display bug: "
            "storefront `variants[].price` is a MAJOR decimal string and UCP's variant `price` "
            "is a MINOR integer, for the SAME variant. If COROS unified the units, money.py's "
            "two converters collapse into one — update AGENTS.md and this test together."
        )
        assert ucp_minor == major_string_to_minor(feed_major), (
            "coros_core.money.major_string_to_minor no longer agrees with the live UCP amount "
            f"for the same variant. {FACTS} — update AGENTS.md and this test together."
        )


class TestProductTypeCannotIdentifyAWatch:
    """`product_type` is empty on roughly half the feed, and outright wrong on one
    row (`coros-dura`, a bike computer, says "Relojes GPS"). `devices.py` never
    keys on it."""

    def test_pace_4_has_an_empty_product_type(self) -> None:
        feed = json.loads((REPO / "fixtures" / "products.json").read_text())
        product = next(p for p in feed["products"] if p["id"] == PACE4_PRODUCT_ID)

        assert product["product_type"] == "", (
            f"COROS PACE 4's product_type is no longer empty (got {product['product_type']!r}). "
            f"{FACTS} says this is exactly why devices.py joins the device registry by product "
            "id and handle and never by product_type — a non-empty value here does not make the "
            "field trustworthy elsewhere (coros-dura's product_type is non-empty and wrong). "
            "Update AGENTS.md and this test together before changing the join key."
        )

        normalized = catalog.map_product(product)
        assert normalized.product_type == "", (
            f"catalog.map_product no longer carries product_type through untouched. {FACTS} "
            "says it passes through empty for PACE 4 unchanged. Update AGENTS.md and this test "
            "together."
        )


class TestTheSdkBreaksOnABareFunctionDeclaration:
    """google-genai 2.14.0 raises `AttributeError` from inside itself, before any HTTP
    call, when `tools=` holds a `FunctionDeclaration` instead of a `Tool`. It costs no
    network to pin, and it is the reason `gemini.as_tools()` exists — if a release ever
    accepts the bare shape, the wrapper becomes optional, not wrong."""

    def _client(self) -> Any:
        # Never sends: the shape error is raised while building the request. A real key
        # here would still not reach Google.
        return genai.Client(api_key="not-a-key", http_options=types.HttpOptions(timeout=gemini.TIMEOUT_MS))

    @pytest.mark.parametrize("shape", ["typed", "dict"])
    def test_a_bare_declaration_never_reaches_the_wire(self, shape: str) -> None:
        declaration = types.FunctionDeclaration(name="list_collections", description="live COROS collections")
        config: Any = (
            types.GenerateContentConfig(tools=[declaration]) if shape == "typed" else {"tools": [declaration]}
        )

        held = self._client()
        with pytest.raises(AttributeError) as caught:
            held.models.generate_content(model=gemini.MODEL, contents="hola", config=config)

        assert "function_declarations" in str(caught.value), (
            f"google-genai no longer rejects a bare FunctionDeclaration ({caught.value}). {FACTS} "
            "says the Tool wrapper is mandatory and that this fails before any HTTP call. Update "
            "AGENTS.md, gemini.as_tools and this test together."
        )

    def test_a_client_nobody_holds_has_already_closed_its_transport(self) -> None:
        """`genai.Client.__del__` closes the httpx transport, and `client.models` does not
        keep the client alive — so the wrapper has to be held. This is why `gemini.client`
        is `lru_cache`d and why `generate()` binds the client rather than `.aio.models`."""
        with pytest.raises(RuntimeError) as caught:
            self._client().models.generate_content(model=gemini.MODEL, contents="hola")

        assert "has been closed" in str(caught.value), (
            f"a dropped genai.Client no longer closes its transport (got {caught.value!r}). "
            f"{FACTS} says it does, which is what the lru_cache in gemini.client is holding "
            "open. Update AGENTS.md and this test together."
        )

    @pytest.mark.live
    def test_the_wrapper_is_the_shape_that_gets_as_far_as_the_credential(self) -> None:
        """Marked live because proving the request was *built* means letting it leave. It
        spends no quota — the key is deliberately invalid."""
        declaration = types.FunctionDeclaration(name="list_collections", description="live COROS collections")
        config = types.GenerateContentConfig(tools=gemini.as_tools([declaration]))
        held = self._client()

        with pytest.raises(errors.ClientError) as caught:
            held.models.generate_content(model=gemini.MODEL, contents="hola", config=config)

        assert caught.value.code == 400, (
            "the wrapped shape no longer gets as far as being rejected for a bad API key "
            f"(got {caught.value}). That 400 is what proves the request was built, not refused "
            "locally. Update AGENTS.md and this test together."
        )


class TestTheCyclingRangeIsTwoSensorsAndNeitherMeasuresPower:
    """The whole reason `capability.py` has a `no_product` dead end. COROS Colombia's
    cycling range is a cadence sensor and a speed sensor; no product in the feed measures
    power, so a power search can only ever end empty. Offline over the fixture — the live
    half rides `tests/test_catalog.py`'s single feed fetch rather than opening a second."""

    POWER_TOKENS = ("potenci", "power", "vatio", "watt")

    def _feed(self) -> list[dict[str, Any]]:
        return json.loads((REPO / "fixtures" / "products.json").read_text())["products"]

    def test_nothing_in_the_catalogue_is_a_power_meter(self) -> None:
        named = [
            p["handle"]
            for p in self._feed()
            if any(t in f"{p['title']} {p['handle']}".lower() for t in self.POWER_TOKENS)
        ]
        assert named == [], (
            f"products naming power now exist: {named}. {FACTS} and capability.py's "
            "`cycling_power` dead end both say COROS Colombia sells none — if one appeared, "
            "the need moves from DEAD_ENDS to MAP. Update AGENTS.md, capability.py and this "
            "test together."
        )

    def test_the_two_bike_sensors_are_cadence_and_speed(self) -> None:
        sensors = sorted(p["handle"] for p in self._feed() if "bike" in p["handle"])
        assert sensors == ["coros-bike-cadence-sensor", "coros-bike-speed-sensor"], (
            f"the bike range is now {sensors}. {FACTS} pins these two — update AGENTS.md and "
            "this test together."
        )

    def test_the_cadence_sensor_is_out_of_stock_and_that_is_a_stock_answer(self) -> None:
        """Recorded from the feed on 30 Jul 2026. A restock is expected and is NOT a
        contract break, which is why this reads the frozen fixture: the fact being pinned
        is that `cycling_cadence` stays *capable* while out of stock, so the person hears
        "agotado" from check_stock and never "COROS no hace eso" from the capability map."""
        cadence = next(p for p in self._feed() if p["handle"] == "coros-bike-cadence-sensor")
        speed = next(p for p in self._feed() if p["handle"] == "coros-bike-speed-sensor")

        assert not any(v["available"] for v in cadence["variants"])
        assert any(v["available"] for v in speed["variants"])
        assert "cycling_cadence" in capability.MAP and "cycling_cadence" not in capability.DEAD_ENDS, (
            "an out-of-stock product became a capability dead end. Stock is check_stock's "
            f"answer; {FACTS} says COROS makes this sensor. Update AGENTS.md and this test "
            "together."
        )

    def test_neither_sensor_speaks_ant_plus(self) -> None:
        for handle in ("coros-bike-cadence-sensor", "coros-bike-speed-sensor"):
            product = next(p for p in self._feed() if p["handle"] == handle)
            assert "ANT+" in product["body_html"], f"{handle} stopped mentioning ANT+ at all"
            assert "No compatible con dispositivos ANT+" in product[
                "body_html"
            ] or "no es compatible" in product["body_html"].lower(), (
                f"{handle} no longer denies ANT+. {FACTS} and capability.py's `ant_plus` dead "
                "end rest on COROS's own sentence — update AGENTS.md and this test together."
            )

    QUESTION = "¿El POD 2 mide la potencia de funcionamiento?"

    def test_coros_denies_running_power_in_its_own_words(self) -> None:
        pod = next(p for p in self._feed() if p["handle"] == "coros-pod-2")
        prose = " ".join(catalog.strip_untrusted(pod["body_html"], 40_000).split())
        assert self.QUESTION in prose, (
            f"the POD 2 FAQ no longer asks about running power. {FACTS} quotes it as the "
            "evidence behind capability.py's `running_power` dead end."
        )
        answer = prose.split(self.QUESTION, 1)[1][:120]
        assert "No, el POD 2 usa Effort Pace" in answer, (
            f"COROS's answer changed: {answer!r}. If the POD 2 now measures running power, "
            "`running_power` moves out of DEAD_ENDS. Update AGENTS.md, capability.py and this "
            "test together."
        )

    def test_that_denial_never_reaches_the_model(self) -> None:
        """Why the dead end has to be code. COROS answers this question itself, 4 916
        characters into a 36 KB BeeFree template — and `DESCRIPTION_CHARS` is 400, so the
        prose a model sees stops long before the FAQ. A model reasoning from the catalogue
        cannot know the POD 2 does not measure running power."""
        pod = catalog.map_product(next(p for p in self._feed() if p["handle"] == "coros-pod-2"))
        assert self.QUESTION not in pod.description
        assert "Effort Pace" not in pod.description, (
            f"the POD 2's FAQ now survives truncation. {FACTS} records that it does not, "
            "which is the reason capability.py carries the denial as a typed dead end "
            "instead of trusting retrieval to surface it."
        )
