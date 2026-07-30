"""The ONE wrapper around COROS's UCP/MCP endpoint.

Nothing else in either app may post to `EP` — a second caller brings its own idea of
the profile, its own retry policy, and its own share of the rate limit. Pinned by a
source scan in `tests/test_ucp.py`.

Load-bearing facts encoded here (`AGENTS.md`) — these look like bugs and are not:
  * The agent profile goes in `params.arguments.meta["ucp-agent"].profile`, and EVERY
    method needs it — `initialize` and `tools/list` included. That is why `rpc()`
    sends an `arguments` key even for methods that take no arguments.
  * A JSON-RPC error arrives with HTTP **422** (or 403), so the body is read BEFORE
    `raise_for_status()`. Reversing those two lines turns COROS's own diagnostics
    into a bare `HTTPStatusError`.
  * `-32000 AuthenticationRequired` on a `tools/call` means the tool NAME is wrong.
    The read tools need no JWT; a typo is reported as an auth failure.
  * Schema rejections arrive as HTTP 200 with `result.isError` true.
  * The real body is a JSON *string* inside `result.content[0].text` — decode twice.
    An `isError` body is sometimes a bare sentence, so check the flag before decoding.
  * Prices in the decoded body are MINOR units; `money.py` is the only converter.

Divergence from DecaBot's `commerce/ucp.py`: there, `initialize` and `tools/list`
always fail with -32001 and the handshake is skipped. Here they both succeed once the
profile is passed. Nothing depends on the handshake either way, so it is not called.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

EP = "https://coros.com.co/api/ucp/mcp"

# A capability declaration, not a credential — no key, no token, no OAuth. The server
# really fetches this URL, so it must be publicly reachable over https: `localhost`
# fails with "Https required" and an unreachable host with "invalid_profile_url".
PROF = "https://shopify.dev/ucp/agent-profiles/examples/2026-04-08/valid-with-capabilities.json"
AGENT_META = {"ucp-agent": {"profile": PROF}}

CONTEXT = {"address_country": "CO", "currency": "COP"}

# No limit has been measured on COROS. This is Decathlon's number, carried over so we
# never find out what COROS's is: 40 concurrent was clean there and 100 cost ~48 min.
MAX_CONCURRENCY = 8
SEM = asyncio.Semaphore(MAX_CONCURRENCY)

# Once a 429 has been seen, every later call is serialised and spaced by this much for
# the rest of the process. Concurrency, not rate, is what re-trips a limiter, and a
# success is not recovery — single calls are served throughout a lockout. Never unlatch.
PACE_SECONDS = 1.5

_client: httpx.AsyncClient | None = None
_paced = False
_pace_lock = asyncio.Lock()
_last_send = 0.0


class UcpRateLimited(Exception):
    """A 429 that survived a spaced retry. `Retry-After` is reported rather than slept
    on: on the one endpoint where this was measured it counted down to a fixed unlock
    ~48 minutes out, which is longer than any conversation."""

    def __init__(self, retry_after: str | None = None) -> None:
        super().__init__(f"UCP rate limited (Retry-After: {retry_after})")
        self.retry_after = retry_after


class UcpToolError(Exception):
    """A JSON-RPC error at any status, an HTTP 200 carrying `isError`, or a 200 whose
    body is not the envelope we were promised. `code` is the JSON-RPC code when there
    was one — `-32001` is a rejected profile, `-32000` a tool name that does not exist."""

    def __init__(self, detail: Any, code: int | None = None) -> None:
        super().__init__(str(detail))
        self.code = code


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(45.0),
            limits=httpx.Limits(max_connections=16),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def is_paced() -> bool:
    return _paced


def engage_pacing() -> None:
    global _paced
    _paced = True


def reset_pacing() -> None:
    """Tests only. In a running process the latch is deliberately one-way."""
    global _paced, _last_send
    _paced, _last_send = False, 0.0


async def _send(payload: dict[str, Any]) -> httpx.Response:
    global _last_send
    if not _paced:
        async with SEM:
            return await client().post(EP, json=payload)

    async with _pace_lock:
        wait = PACE_SECONDS - (time.monotonic() - _last_send)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await client().post(EP, json=payload)
        finally:
            _last_send = time.monotonic()


def _envelope(r: httpx.Response) -> dict[str, Any] | None:
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def rpc(
    method: str, *, arguments: dict[str, Any] | None = None, **params: Any
) -> dict[str, Any]:
    """One JSON-RPC round trip, returning `result`. The rate limiter and the error
    taxonomy live here so `tools/list` and a fixture dump cannot bypass either."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {**params, "arguments": {"meta": AGENT_META, **(arguments or {})}},
    }

    r = await _send(payload)
    if r.status_code == 429:  # never sleep on Retry-After: it is far longer than a turn
        engage_pacing()
        r = await _send(payload)  # a trickle is served even mid-lockout
        if r.status_code == 429:
            raise UcpRateLimited(r.headers.get("Retry-After"))

    body = _envelope(r)
    if body is not None and "error" in body:  # -32001/-32000 ride on 422 and 403
        error = body["error"]
        code = error.get("code") if isinstance(error, dict) else None
        raise UcpToolError(error, code)
    r.raise_for_status()
    if body is None or not isinstance(body.get("result"), dict):
        raise UcpToolError(
            f"{method}: HTTP {r.status_code} with no JSON-RPC result — {r.text[:200]!r}"
        )
    return body["result"]


async def call_ucp(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await rpc("tools/call", name=tool, arguments=args)

    content = result.get("content") or [{}]
    block = content[0] if isinstance(content[0], dict) else {}
    if "text" not in block:
        raise UcpToolError(f"{tool}: result carried no text block — {str(result)[:200]}")
    text = block["text"]

    if result.get("isError"):  # HTTP 200, and sometimes a bare sentence rather than JSON
        raise UcpToolError(f"{tool}: {str(text)[:400]}")

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UcpToolError(f"{tool}: content was not JSON — {str(text)[:200]!r}") from exc
    if not isinstance(data, dict):
        raise UcpToolError(f"{tool}: decoded to {type(data).__name__}, expected an object")

    data.pop("ucp", None)  # ~4 KB of protocol capabilities, echoed on every call
    return data
