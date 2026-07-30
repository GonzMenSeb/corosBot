"""The one wrapper around COROS's UCP/MCP endpoint, pinned.

Every wire fact below was read off `https://coros.com.co/api/ucp/mcp` on 29 Jul 2026.
Two of them contradict the DecaBot module this is descended from, and one contradicts
the plan that asked for it — see `AGENTS.md` "load-bearing facts".

The offline half simulates the transport, because the two failure modes that matter
most (a 429 lockout and a 422 carrying a JSON-RPC error) are respectively expensive
and rude to induce on someone else's storefront. The `live` half proves the
simulation still describes reality; it runs sequentially, spaced, once per module.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from coros_core import trace, ucp
from coros_core.ucp import EP, PROF, UcpRateLimited, UcpToolError

REPO = Path(__file__).resolve().parent.parent
FACTS = 'AGENTS.md "load-bearing facts"'

# Measured 29 Jul 2026: a trickle this far apart was served throughout, and COROS has
# never been observed to trip. Same number DecaBot settled on against Decathlon.
SPACING = 1.5

PRODUCT_GID = "gid://shopify/Product/8039258423531"


def _inner(**over: Any) -> str:
    body = {"products": [{"id": PRODUCT_GID}], "ucp": {"version": "2026-04-08"}}
    body.update(over)
    return json.dumps(body)


def _ok(text: str | None = None, *, is_error: bool = False) -> dict:
    content = [{"type": "text", "text": text if text is not None else _inner()}]
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": content, "isError": is_error}}


def _rpc_error(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}}


class _Transport:
    """A stand-in for `ucp.client()`. Records what was sent and when."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.queue = list(responses)
        self.sent: list[dict] = []
        self.stamps: list[float] = []
        self.is_closed = False

    async def post(self, url: str, json: dict | None = None) -> httpx.Response:  # noqa: A002
        self.sent.append(json or {})
        self.stamps.append(time.monotonic())
        response = self.queue.pop(0) if self.queue else _response(200, _ok())
        response.request = httpx.Request("POST", url)
        return response


def _response(status: int, body: dict | None = None, **headers: str) -> httpx.Response:
    request = httpx.Request("POST", EP)
    if body is None:
        return httpx.Response(status, text="<html>up</html>", headers=headers, request=request)
    return httpx.Response(status, json=body, headers=headers, request=request)


@pytest.fixture(autouse=True)
def unpaced():
    """The latch is process-global and deliberately one-way, so it has to be reset
    around every test — including the live ones, where a real 429 would otherwise
    silently pace the rest of the module."""
    pace = ucp.PACE_SECONDS
    ucp.reset_pacing()
    yield
    ucp.reset_pacing()
    ucp.PACE_SECONDS = pace


@pytest.fixture
def transport(monkeypatch):
    def install(*responses: httpx.Response) -> _Transport:
        stub = _Transport(*responses)
        monkeypatch.setattr(ucp, "client", lambda: stub)
        return stub

    return install


class TestTheProfileIsTheWholeHandshake:
    """The profile is a capability declaration, not a credential — there is no key,
    no token, no OAuth. But it is not optional either, and it does not go where a
    reader of the MCP spec would put it."""

    async def test_the_profile_rides_inside_arguments_next_to_the_call_arguments(self, transport):
        stub = transport()

        await ucp.call_ucp("search_catalog", {"catalog": {"query": "reloj"}})

        arguments = stub.sent[0]["params"]["arguments"]
        assert arguments["meta"]["ucp-agent"]["profile"] == PROF
        assert arguments["catalog"] == {"query": "reloj"}, (
            "the tool's own arguments sit BESIDE meta, not under it — `{'meta': ..., **args}`"
        )

    async def test_the_tool_name_is_a_sibling_of_arguments_not_a_member(self, transport):
        stub = transport()

        await ucp.call_ucp("get_product", {"catalog": {"id": PRODUCT_GID}})

        params = stub.sent[0]["params"]
        assert params["name"] == "get_product"
        assert "name" not in params["arguments"]

    async def test_a_method_that_takes_no_arguments_still_carries_the_profile(self, transport):
        """`tools/list` needs an `arguments` key it has no arguments for. This looks
        like a bug in our code and is the only shape the server accepts."""
        stub = transport(_response(200, {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}))

        await ucp.rpc("tools/list")

        assert stub.sent[0]["params"] == {"arguments": {"meta": {"ucp-agent": {"profile": PROF}}}}

    def test_the_profile_is_a_public_url_because_the_server_really_fetches_it(self):
        assert PROF.startswith("https://"), "the server refuses http and cannot reach localhost"

    def test_the_shopping_context_is_colombian_pesos(self):
        assert ucp.CONTEXT == {"address_country": "CO", "currency": "COP"}


class TestAFailureIsNeverMistakenForAnAnswer:
    """Four different ways this endpoint says no, and every one of them arrives
    somewhere a naive reader would call success."""

    async def test_a_json_rpc_error_is_typed_even_though_it_arrives_as_http_422(self, transport):
        """The divergence that bites: `raise_for_status()` before reading the body
        turns COROS's own diagnostics into a bare `HTTPStatusError`."""
        transport(_response(422, _rpc_error(-32001, "UCP discovery failed")))

        with pytest.raises(UcpToolError) as exc:
            await ucp.call_ucp("get_product", {"catalog": {}})

        assert exc.value.code == -32001
        assert "UCP discovery failed" in str(exc.value)

    async def test_a_typo_in_a_tool_name_comes_back_as_an_authentication_failure(self, transport):
        """HTTP 403 `-32000 AuthenticationRequired` — not `MethodNotFound`. Verified
        live: the read tools need no JWT, so a 403 here means the NAME is wrong."""
        transport(_response(403, _rpc_error(-32000, "AuthenticationRequired")))

        with pytest.raises(UcpToolError) as exc:
            await ucp.call_ucp("serch_catalog", {"catalog": {}})

        assert exc.value.code == -32000

    async def test_a_rejected_call_arrives_as_http_200_with_is_error(self, transport):
        transport(_response(200, _ok("Missing required arguments: catalog", is_error=True)))

        with pytest.raises(UcpToolError) as exc:
            await ucp.call_ucp("get_product", {})

        assert "Missing required arguments" in str(exc.value)

    async def test_an_is_error_payload_is_not_decoded_before_it_is_believed(self, transport):
        """An `isError` body is sometimes a bare sentence and sometimes a full JSON
        envelope. Decoding first would raise `JSONDecodeError` for half of them and
        lose the message for the other half."""
        transport(_response(200, _ok("Missing required arguments: catalog", is_error=True)))

        with pytest.raises(UcpToolError):
            await ucp.call_ucp("get_product", {})

    async def test_an_upstream_html_error_page_is_still_an_error(self, transport):
        transport(_response(502))

        with pytest.raises(httpx.HTTPStatusError):
            await ucp.call_ucp("search_catalog", {"catalog": {}})

    async def test_a_200_that_is_not_json_is_refused_rather_than_returned_empty(self, transport):
        transport(_response(200))

        with pytest.raises(UcpToolError):
            await ucp.call_ucp("search_catalog", {"catalog": {}})

    @pytest.mark.parametrize(
        "result",
        [{"content": []}, {"content": ["text"]}, {"content": [{"type": "image"}]}, {}],
        ids=["empty", "not-an-object", "no-text-key", "no-content-key"],
    )
    async def test_a_malformed_result_envelope_raises_instead_of_returning_nothing(
        self, transport, result
    ):
        transport(_response(200, {"jsonrpc": "2.0", "id": 1, "result": result}))

        with pytest.raises(UcpToolError):
            await ucp.call_ucp("search_catalog", {"catalog": {}})

    @pytest.mark.parametrize("result", [None, [], "ok"], ids=["null", "list", "string"])
    async def test_a_result_that_is_not_an_object_is_refused(self, transport, result):
        transport(_response(200, {"jsonrpc": "2.0", "id": 1, "result": result}))

        with pytest.raises(UcpToolError):
            await ucp.rpc("tools/list")


class TestTheRealBodyIsOneDecodeFurtherDown:
    async def test_the_payload_is_a_json_string_inside_the_content_block(self, transport):
        transport(_response(200, _ok(_inner(pagination={"has_next_page": False}))))

        data = await ucp.call_ucp("search_catalog", {"catalog": {}})

        assert data["products"] == [{"id": PRODUCT_GID}]
        assert data["pagination"] == {"has_next_page": False}

    async def test_the_capability_echo_is_stripped_before_a_model_can_read_it(self, transport):
        transport(_response(200, _ok()))

        data = await ucp.call_ucp("search_catalog", {"catalog": {}})

        assert "ucp" not in data, (
            "the `ucp` block echoes ~4 KB of protocol capabilities on every single call; "
            "left in, it is the largest thing in the model's context and means nothing to it"
        )

    async def test_an_empty_answer_is_data_and_not_a_failure(self, transport):
        transport(_response(200, _ok(json.dumps({"products": [], "ucp": {}}))))

        data = await ucp.call_ucp("search_catalog", {"catalog": {}})

        assert data == {"products": []}, (
            "'nothing matched' is an answer; only raise when we could not look"
        )

    async def test_rpc_hands_back_the_result_for_methods_that_are_not_calls(self, transport):
        tools = [{"name": "search_catalog"}, {"name": "get_product"}]
        transport(_response(200, {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}))

        result = await ucp.rpc("tools/list")

        assert result["tools"] == tools


class TestARateLimitIsAPauseNotADeadEnd:
    """COROS has not been observed to trip, so this is Decathlon's measured policy
    carried over rather than a limit we have seen. Simulated: a real lockout there
    cost ~48 minutes, and the point of pacing is to never find out COROS's number."""

    async def test_a_429_latches_pacing_and_the_spaced_retry_is_served(self, transport):
        stub = transport(_response(429, None, **{"Retry-After": "1503"}), _response(200, _ok()))
        ucp.PACE_SECONDS = 0.05

        data = await ucp.call_ucp("search_catalog", {"catalog": {}})

        assert data["products"] == [{"id": PRODUCT_GID}]
        assert len(stub.sent) == 2, "a 429 is retried once, spaced — not surfaced immediately"
        assert ucp.is_paced() is True, (
            "the latch must survive the success: single calls are served THROUGHOUT a lockout, "
            "so one success proves the bucket holds one token and nothing more"
        )

    async def test_a_429_surviving_the_spaced_retry_is_typed_and_keeps_retry_after(self, transport):
        stub = transport(
            _response(429, None, **{"Retry-After": "1503"}),
            _response(429, None, **{"Retry-After": "1499"}),
        )
        ucp.PACE_SECONDS = 0.05

        with pytest.raises(UcpRateLimited) as exc:
            await ucp.call_ucp("search_catalog", {"catalog": {}})

        assert exc.value.retry_after == "1499"
        assert len(stub.sent) == 2, "never grind on a limiter that is already refusing"

    async def test_pacing_serialises_calls_because_concurrency_trips_a_limiter(self, transport):
        stub = transport()
        ucp.engage_pacing()
        ucp.PACE_SECONDS = 0.08

        await asyncio.gather(*(ucp.call_ucp("search_catalog", {"catalog": {}}) for _ in range(3)))

        gaps = [b - a for a, b in zip(stub.stamps, stub.stamps[1:])]
        assert len(stub.stamps) == 3
        assert all(gap >= 0.07 for gap in gaps), f"paced calls overlapped: {gaps}"

    async def test_the_happy_path_is_never_slowed_by_a_limit_that_has_not_happened(self, transport):
        stub = transport()
        ucp.PACE_SECONDS = 5.0

        await asyncio.gather(*(ucp.call_ucp("search_catalog", {"catalog": {}}) for _ in range(3)))

        assert max(stub.stamps) - min(stub.stamps) < 1.0
        assert ucp.is_paced() is False

    def test_concurrency_is_capped_below_the_burst_that_was_measured_to_trip(self):
        assert ucp.MAX_CONCURRENCY == 8
        assert ucp.SEM._value <= ucp.MAX_CONCURRENCY


class TestEveryTripToTheEndpointLeavesATrace:
    """`evidence.py` treats a `ucp.*` failure event as "this turn's retrieval is not a
    verified fact". A refusal that emits nothing is a refusal nothing downstream sees."""

    async def test_a_served_call_records_the_tool_and_not_its_arguments(self, transport):
        transport(_response(200, _ok()))

        await ucp.call_ucp("get_product", {"product_id": PRODUCT_GID, "catalog": {}})

        call = trace.events()[-1]
        assert call.event == "ucp.call" and call.level == "info"
        assert call.payload["tool"] == "get_product"
        assert call.payload["arguments"] == ["catalog", "product_id"], (
            "argument NAMES only. A cart id, an address or an email travels in the values,\n"
            "  and an evidence bundle built from these events is pasted into a model."
        )
        assert PRODUCT_GID not in json.dumps(call.payload)

    async def test_a_json_rpc_error_is_recorded_with_its_code(self, transport):
        transport(_response(422, _rpc_error(-32001, "UCP discovery failed")))

        with pytest.raises(UcpToolError):
            await ucp.call_ucp("search_catalog", {"catalog": {}})

        failures = [e for e in trace.events("error") if e.event == "ucp.tool_error"]
        assert len(failures) == 1
        assert failures[0].payload["code"] == -32001

    async def test_a_schema_rejection_on_http_200_is_recorded_too(self, transport):
        transport(_response(200, _ok("Missing required arguments: catalog", is_error=True)))

        with pytest.raises(UcpToolError):
            await ucp.call_ucp("search_catalog", {})

        failures = [e for e in trace.events("error") if e.event == "ucp.tool_error"]
        assert failures and failures[-1].payload["tool"] == "search_catalog"
        assert not any(e.event == "ucp.call" for e in trace.events()), (
            "an isError result was recorded as a served call. HTTP 200 is not success here."
        )

    async def test_both_halves_of_a_rate_limit_are_recorded(self, transport):
        transport(
            _response(429, None, **{"Retry-After": "1503"}),
            _response(429, None, **{"Retry-After": "1499"}),
        )
        ucp.PACE_SECONDS = 0.05

        with pytest.raises(UcpRateLimited):
            await ucp.call_ucp("search_catalog", {"catalog": {}})

        refusals = [e for e in trace.events("error") if e.event == "ucp.rate_limited"]
        assert [e.payload["retrying"] for e in refusals] == [True, False], (
            "the latch engaging and the give-up are different facts: one says a retry was\n"
            "  spaced and served, the other that COROS is refusing a trickle."
        )


def _docstrings(tree: ast.Module) -> set[int]:
    """Prose that merely *mentions* the endpoint is documentation, not a second caller."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            found.add(id(first.value))
    return found


class TestThisIsTheOnlyDoorToTheEndpoint:
    def test_no_other_module_names_the_mcp_endpoint_in_code(self):
        offenders = []
        for root in (REPO / "packages", REPO / "apps", REPO / "scripts"):
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.py")):
                if path.name == "ucp.py":
                    continue
                tree = ast.parse(path.read_text())
                prose = _docstrings(tree)
                offenders += [
                    f"{path.relative_to(REPO)}:{node.lineno}: {node.value!r}"
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "/api/ucp/mcp" in node.value
                    and id(node) not in prose
                ]
        assert not offenders, (
            "the UCP endpoint is spelled outside coros_core.ucp:\n  "
            + "\n  ".join(offenders)
            + "\nGo through `call_ucp` / `rpc` instead. A second caller has its own idea of "
            "the profile,\nits own retry policy, and its own share of the rate limit."
        )


async def _probe() -> dict[str, Any]:
    """One pass over every wire fact, spaced, sequential. Never `asyncio.gather` here."""
    meta = {"meta": {"ucp-agent": {"profile": PROF}}}
    handshake = {
        "protocolVersion": "2026-04-08",
        "capabilities": {},
        "clientInfo": {"name": "corosbot-test", "version": "0"},
    }
    query = {"query": "reloj gps", "context": ucp.CONTEXT, "pagination": {"limit": 3}}
    out: dict[str, Any] = {"rate_limited": False}

    async def spaced(coro):
        await asyncio.sleep(SPACING)
        return await coro

    async def raw(method: str, params: dict) -> httpx.Response:
        """Non-conforming payloads on purpose — `rpc()` cannot produce them by
        construction, which is the property under test."""
        await asyncio.sleep(SPACING)
        frame = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        return await ucp.client().post(EP, json=frame)

    try:
        out["initialize_without_profile"] = (await raw("initialize", handshake)).json()
        out["tools_list_without_profile"] = (await raw("tools/list", {})).json()
        out["profile_in_params"] = (await raw("tools/list", meta)).json()

        out["initialize"] = await spaced(ucp.rpc("initialize", **handshake))
        out["tools"] = [t["name"] for t in (await spaced(ucp.rpc("tools/list")))["tools"]]

        envelope = await raw(
            "tools/call",
            {"name": "search_catalog", "arguments": {**meta, "catalog": query}},
        )
        out["call_envelope"] = envelope.json()

        try:
            await spaced(ucp.call_ucp("get_product", {}))
        except UcpToolError as exc:
            out["schema_error"] = str(exc)
        try:
            await spaced(ucp.call_ucp("serch_catalog", {"catalog": {}}))
        except UcpToolError as exc:
            out["typo_error_code"] = exc.code
    except UcpRateLimited:
        out["rate_limited"] = True
    finally:
        await ucp.aclose()

    return out


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    return asyncio.run(_probe())


@pytest.fixture(scope="module")
def mcp(live) -> dict[str, Any]:
    if live["rate_limited"]:
        pytest.skip(
            "UCP rate limited — a lockout is not a contract violation. Do NOT poll with a "
            "single call to decide it has cleared: one call is served throughout."
        )
    return live


@pytest.mark.live
def test_every_method_needs_the_profile_in_arguments_meta(mcp):
    for method in ("initialize", "tools/list"):
        key = method.replace("tools/list", "tools_list").replace("/", "_") + "_without_profile"
        error = mcp[key].get("error", {})
        assert error.get("code") == -32001, (
            f"`{method}` stopped requiring the agent profile. {FACTS} says every method on this\n"
            f"  endpoint fails -32001 'UCP discovery failed' without it. Got: {mcp[key]}"
        )

    assert mcp["profile_in_params"].get("error", {}).get("code") == -32001, (
        f"the profile is now accepted at params.meta. {FACTS} says the ONLY placement that\n"
        f"  works is params.arguments.meta['ucp-agent'].profile, even for tools/list.\n"
        f"  Got: {mcp['profile_in_params']}"
    )


@pytest.mark.live
def test_initialize_and_tools_list_both_succeed_once_the_profile_is_passed(mcp):
    """DecaBot's endpoint fails both of these unconditionally, and the plan for this
    module said `initialize` fails here too. It does not — the profile is the whole
    difference. Nothing depends on the handshake either way; we simply do not lie."""
    assert mcp["initialize"]["serverInfo"]["name"] == "universal-commerce", (
        f"`initialize` no longer succeeds. {FACTS} records it returning a serverInfo block\n"
        f"  when the profile is passed. Update the registry and this test together.\n"
        f"  Got: {mcp['initialize']}"
    )
    assert "search_catalog" in mcp["tools"] and "get_product" in mcp["tools"], (
        f"`tools/list` no longer lists the read tools. {FACTS} pins the 13-tool schema.\n"
        f"  Got: {mcp['tools']}"
    )


@pytest.mark.live
def test_the_body_is_double_encoded_and_echoes_the_capability_block(mcp):
    result = mcp["call_envelope"]["result"]
    text = result["content"][0]["text"]
    assert isinstance(text, str), (
        f"the response stopped being double-encoded. {FACTS} says the real body is a JSON\n"
        f"  STRING at result.content[0].text and needs a second json.loads()."
    )
    data = json.loads(text)
    assert "ucp" in data, (
        f"the capability echo is gone, so `data.pop('ucp')` in ucp.py is now dead code. {FACTS}"
    )
    assert "products" in data


@pytest.mark.live
def test_a_rejected_call_still_arrives_as_http_200(mcp):
    assert "Missing required arguments" in mcp["schema_error"], (
        f"a schema rejection changed shape. {FACTS} says it arrives as HTTP 200 with\n"
        f"  result.isError true, which naive handling reads as success.\n"
        f"  Got: {mcp.get('schema_error')!r}"
    )


@pytest.mark.live
def test_a_wrong_tool_name_is_reported_as_an_auth_failure(mcp):
    assert mcp["typo_error_code"] == -32000, (
        f"a typo'd tool name no longer reads as -32000 AuthenticationRequired. {FACTS} warns\n"
        f"  that a 403 here means the NAME is wrong, not that a credential is missing — the\n"
        f"  read tools need no JWT. Got: {mcp.get('typo_error_code')!r}"
    )
