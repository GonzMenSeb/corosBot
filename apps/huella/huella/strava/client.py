"""Strava OAuth 2.0 and the read half of the v3 API — the one door Huella uses.

Two paths, and keeping them apart is the point. The CREDENTIAL path (`authorize_url`,
`exchange`, `refresh`, `revoke`) hands back a frozen `TokenPair` or raises a typed error.
The DATA path (`fetch_athlete`, `fetch_activities`) hands back a `ToolResult` and never a
token: `ToolResult.data` reaches the trace, the evidence bundle and from there a model's
context, and a credential that travels that far is a credential in a log aggregator.

The shapes live in `models.py`, together with the wire facts they encode. Nothing here
reads `.env` — the credentials are looked up at CALL time, so this module imports on a
machine that has never heard of Strava and fails with a typed `StravaUnconfigured` only
when someone actually tries to connect.

Verified 30 Jul 2026 against `developers.strava.com/docs/authentication`,
`/docs/rate-limits` and `/docs/changelog`, plus a live probe of the token endpoint — see
`TOKEN_URL`, where the URL Strava documents twice is settled. These look like bugs and are
not:

  * **A refresh invalidates the previous refresh token immediately** — "Once a new refresh
    token is returned, the older refresh token is invalidated immediately" — and the new
    one "may or may not be the same" as the old. So the caller cannot tell a rotation from
    a no-op and has to persist the returned pair EVERY time. `refresh()` is one call
    returning one pair and keeps no state of its own, which is what lets `privacy.py`
    write both halves under a single lock. Half a pair on disk is an athlete locked out of
    their own history.
  * **A fetch never refreshes.** A refresh issued from inside a data call would rotate the
    refresh token where the caller cannot see it, which is the same lockout by another
    route. `TokenPair.needs_refresh()` is the caller's cue.
  * **Nothing here retries.** A timed-out refresh may already have rotated upstream, so a
    second attempt with the old token is the lockout again; and a read costs a slice of a
    window of 100. A refusal is reported and the turn decides.
  * **The 15-minute window resets on the wall clock, at :00/:15/:30/:45** — not fifteen
    minutes after the refusal — and Strava sends no `Retry-After`. `retry_after_s` is
    computed to that boundary, and a 429 latches until it: polling only spends a window we
    have already been told is spent.
  * **`X-RateLimit-*` and `X-ReadRateLimit-*` each carry `15min,daily`** as "integer,
    integer". The read pair is the binding one here — 100 per 15 min, 1 000 per day,
    against 200/2 000 overall — because Huella only ever reads.
  * **A 429 is never an empty history.** Every refusal is a typed outcome carrying detail;
    `OK` with an empty window means the athlete really has no activities in it.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from coros_core.outcomes import ToolOutcome, ToolResult
from coros_core.trace import emit
from huella.strava.models import (
    Activity,
    ActivityWindow,
    Athlete,
    RateLimit,
    RateLimits,
    TokenPair,
    TokenResponse,
)

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"

# Strava documents this path both ways and BOTH ARE LIVE. Measured 30 Jul 2026 — a POST
# carrying a bogus `client_id` to each — and `https://www.strava.com/oauth/token` and
# `https://www.strava.com/api/v3/oauth/token` answered the identical HTTP 400,
# `{"resource": "Application", "field": "client_id", "code": "invalid"}`. The docs' cURL
# examples and the swagger's `tokenUrl` show only the `/api/v3` form, so THIS SPELLING LOOKS
# WRONG AGAINST THE DOCUMENTATION AND IS NOT: it is preferred because it sits on the host
# the 4 Jan 2027 migration leaves alone, and `/api/v3/oauth/token` is the one that would
# have to change. Both grant types post here, form-encoded.
TOKEN_URL = "https://www.strava.com/oauth/token"

# The deauthorization endpoint since 1 Jun 2026 and the only one supported from 1 Jun 2027.
# It replaced `POST https://www.strava.com/oauth/deauthorize`, which took the access token
# in the query string; this one wants Basic auth over the client credentials and the token
# in the form body. Do not call `deauthorize`.
REVOKE_URL = "https://www.strava.com/oauth/revoke"

# The base for DATA reads only, spelled once so the migration is one line. Changelog,
# 1 Jun 2026: "The API base URL is changing from https://www.strava.com/api/v3 to
# https://api-v3.strava.com. The new base URL will be available starting January 4, 2027."
# Not live yet: neither that host nor the `www.api-v3.strava.com` a widely-circulated
# third-party summary invented (which also dates it Jun 2027 — both wrong, and the changelog
# is the source) resolved on 30 Jul 2026. That entry is scoped to this base URL and says
# nothing about `www.strava.com/oauth/*`; the same entry introduces a new OAuth endpoint on
# that bare host. So the three constants above are not part of this migration.
API = "https://www.strava.com/api/v3"

# Seven scopes exist: `read`, `read_all`, `profile:read_all`, `profile:write`,
# `activity:read`, `activity:read_all`, `activity:write`. Huella reads three and writes
# nothing — there is no wider history scope to reach for.
SCOPES = "read,activity:read_all,profile:read_all"

# Not `capability.ToolId` values: these name a transport call, and the model-facing tool
# is `get_training_summary`, which the agent layer builds on top of them.
TOOL_ATHLETE = "strava.athlete"
TOOL_ACTIVITIES = "strava.activities"

REQUEST_TIMEOUT = 30.0
MAX_CONCURRENCY = 4
SEM = asyncio.Semaphore(MAX_CONCURRENCY)

# The swagger documents `per_page` defaulting to 30 and states no ceiling, so 30 is what
# is asked for. The page loop therefore stops on an EMPTY page rather than on a short one:
# a server-side cap below what we asked for would make "short" mean "done" and truncate a
# history silently.
PER_PAGE = 30
MAX_PAGES = 4

WINDOW_SECONDS = 900


# ── typed refusals ─────────────────────────────────────────────────────────────


class StravaUnconfigured(RuntimeError):
    """No client id or secret. Raised at call time and never at import, so the app boots,
    renders a connect screen and passes its tests on a machine with no credentials."""


class StravaError(Exception):
    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class StravaAuthDenied(StravaError):
    """400 or 401: the code was already used, or the refresh token has been rotated away
    or revoked. Retrying cannot help — the athlete has to connect again."""


class StravaScopeDenied(StravaError):
    """403: the token is real and does not carry the scope this read needs. Not an empty
    history, and not something a retry improves."""


class StravaRateLimited(StravaError):
    def __init__(self, detail: str, retry_after_s: float, limits: RateLimits | None = None) -> None:
        super().__init__(detail, 429)
        self.retry_after_s = retry_after_s
        self.limits = limits


class StravaTimeout(StravaError):
    """We stopped waiting. Nothing was learned, and this is not a "no"."""


class StravaUnavailable(StravaError):
    """A 5xx, a dropped connection, or a body that is not the shape we were promised."""


# ── configuration, read at call time ───────────────────────────────────────────


def client_id() -> str:
    return os.environ.get("STRAVA_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("STRAVA_CLIENT_SECRET", "").strip()


def redirect_uri() -> str:
    return os.environ.get("STRAVA_REDIRECT_URI", "").strip()


def is_configured() -> bool:
    return bool(client_id() and client_secret() and redirect_uri())


def _credentials() -> tuple[str, str]:
    identifier, secret = client_id(), client_secret()
    if not identifier or not secret:
        raise StravaUnconfigured(
            "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET are missing — see .env.example. "
            "Huella runs without them; connecting an athlete does not."
        )
    return identifier, secret


def _redirect() -> str:
    found = redirect_uri()
    if not found:
        raise StravaUnconfigured(
            "STRAVA_REDIRECT_URI is missing. Strava validates it against the app's "
            "registered Authorization Callback DOMAIN rather than a full URI — `localhost` "
            "and `127.0.0.1` are white-listed, so dev needs no re-registration — but the "
            "value still has to be the URI the athlete should be sent back to."
        )
    return found


# ── transport ──────────────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None
_limits = RateLimits()
_paced_until = 0.0


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
            limits=httpx.Limits(max_connections=MAX_CONCURRENCY),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def rate_limits() -> RateLimits:
    """What the last response reported. Read by the UI's throttle notice: a `ToolResult`
    has nowhere to carry it, and the credential path must not grow a data field."""
    return _limits


def seconds_to_window_reset(now: float | None = None) -> float:
    """Strava's 15-minute limit resets at :00/:15/:30/:45 rather than fifteen minutes
    after the refusal, and it sends no `Retry-After`. Unix time ignores leap seconds, so
    epoch modulo 900 is exactly the position inside the current window."""
    stamp = time.time() if now is None else now
    return WINDOW_SECONDS - (stamp % WINDOW_SECONDS)


def is_paced(now: float | None = None) -> bool:
    return (time.time() if now is None else now) < _paced_until


def cooldown_remaining(now: float | None = None) -> float:
    return max(0.0, _paced_until - (time.time() if now is None else now))


def engage_pacing(now: float | None = None) -> float:
    """Latch until the boundary the current window actually ends at, and answer with the
    wait. Unlike `ucp.py`'s latch this one decays, because Strava's window is published
    and honest — there is a time after which sending again is correct."""
    global _paced_until
    stamp = time.time() if now is None else now
    wait = seconds_to_window_reset(stamp)
    _paced_until = stamp + wait
    return wait


def reset_pacing() -> None:
    """Tests only. In a running process the latch decays on the window's own clock."""
    global _paced_until, _limits
    _paced_until, _limits = 0.0, RateLimits()


def _counters(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _family(headers: Mapping[str, str], prefix: str) -> RateLimit | None:
    limit = _counters(headers.get(f"{prefix}-Limit"))
    usage = _counters(headers.get(f"{prefix}-Usage"))
    if limit is None or usage is None:
        return None
    return RateLimit(
        fifteen_min_limit=limit[0],
        fifteen_min_usage=usage[0],
        daily_limit=limit[1],
        daily_usage=usage[1],
    )


def parse_rate_limits(headers: Mapping[str, str]) -> RateLimits:
    return RateLimits(
        overall=_family(headers, "X-RateLimit"), read=_family(headers, "X-ReadRateLimit")
    )


def _fault(response: httpx.Response) -> str:
    """Strava's documented error envelope is `{"message": …, "errors": [{code, field,
    resource}]}`. The message and the codes are what a person can act on."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        raw = body.get("errors")
        errors = raw if isinstance(raw, list) else []
        codes = [str(e.get("code")) for e in errors if isinstance(e, dict) and e.get("code")]
        parts = [str(body.get("message") or ""), ", ".join(codes)]
        joined = " — ".join(part for part in parts if part)
        if joined:
            return joined[:200]
    return str(body)[:200]


def _refuse(what: str, response: httpx.Response) -> StravaError:
    status = response.status_code
    detail = f"{what}: HTTP {status} — {_fault(response)}"
    if status == 429:
        wait = engage_pacing()
        emit(
            "strava.rate_limited",
            {
                "what": what,
                "retry_after_s": round(wait, 1),
                "latched": False,
                "limits": _limits.model_dump(),
            },
            "error",
        )
        return StravaRateLimited(
            f"{detail}. La ventana de 15 minutos se reinicia en {wait:.0f} s.", wait, _limits
        )
    emit("strava.failed", {"what": what, "status": status, "detail": detail}, "error")
    if status in (400, 401):
        return StravaAuthDenied(detail, status)
    if status == 403:
        return StravaScopeDenied(detail, status)
    return StravaUnavailable(detail, status)


async def _send(
    request: Callable[[], Awaitable[httpx.Response]], *, what: str
) -> httpx.Response:
    global _limits
    try:
        async with SEM:
            response = await request()
    except httpx.TimeoutException as exc:
        emit("strava.failed", {"what": what, "status": 0, "detail": str(exc)[:200]}, "error")
        raise StravaTimeout(f"{what}: {type(exc).__name__} after {REQUEST_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        emit("strava.failed", {"what": what, "status": 0, "detail": str(exc)[:200]}, "error")
        raise StravaUnavailable(f"{what}: {type(exc).__name__}: {exc}"[:200]) from exc

    found = parse_rate_limits(response.headers)
    if found.overall is not None or found.read is not None:
        _limits = found
        if found.exhausted:
            # The window is spent, so the next call would be a 429. Latching now keeps it
            # off the network instead of buying the refusal to find out.
            engage_pacing()
    return response


async def _read(path: str, pair: TokenPair, params: dict[str, Any], *, what: str) -> Any:
    # Only the DATA path honours the cooldown. The latch is set off a read window, and
    # refusing a reconnect because a read was throttled would strand an athlete over a
    # counter that does not govern the token endpoint. A 429 there still latches.
    if is_paced():
        wait = cooldown_remaining()
        emit(
            "strava.rate_limited",
            {"what": what, "retry_after_s": round(wait, 1), "latched": True},
            "error",
        )
        raise StravaRateLimited(
            f"{what}: Strava rehusó hace poco y la ventana se reinicia en {wait:.0f} s; "
            "no se consulta dentro del enfriamiento.",
            wait,
            _limits,
        )

    started = time.monotonic()
    response = await _send(
        lambda: client().get(
            f"{API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {pair.access_token}"},
        ),
        what=what,
    )
    if response.is_error:
        raise _refuse(what, response)
    try:
        body = response.json()
    except ValueError as exc:
        detail = f"{what}: response was not JSON — {response.text[:120]!r}"
        emit(
            "strava.failed",
            {"what": what, "status": response.status_code, "detail": detail},
            "error",
        )
        raise StravaUnavailable(detail) from exc

    read = _limits.read
    emit(
        "strava.call",
        {
            "what": what,
            "path": path,
            "status": response.status_code,
            "ms": round((time.monotonic() - started) * 1000),
            "page": params.get("page"),
            "read_usage": read.fifteen_min_usage if read else None,
            "read_limit": read.fifteen_min_limit if read else None,
        },
    )
    return body


def _as_refusal(tool: str, exc: StravaError) -> ToolResult:
    if isinstance(exc, StravaRateLimited):
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.RATE_LIMITED,
            detail=exc.detail,
            retry_after_s=exc.retry_after_s,
        )
    if isinstance(exc, StravaAuthDenied):
        # An answer exists and a person has to authorise it again. A 401 cannot tell an
        # expired access token from a revoked grant, so the detail says both.
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.NEEDS_HUMAN,
            detail=(
                f"{exc.detail}. Refresca el par de tokens; si vuelve a fallar, el atleta "
                "tiene que reconectar su cuenta."
            ),
        )
    if isinstance(exc, StravaScopeDenied):
        return ToolResult(tool=tool, outcome=ToolOutcome.NOT_ELIGIBLE, detail=exc.detail)
    if isinstance(exc, StravaTimeout):
        return ToolResult(tool=tool, outcome=ToolOutcome.TIMEOUT, detail=exc.detail)
    return ToolResult(tool=tool, outcome=ToolOutcome.UPSTREAM_ERROR, detail=exc.detail)


# ── the credential path ────────────────────────────────────────────────────────


def authorize_url(*, state: str = "", scope: str = SCOPES, approval_prompt: str = "auto") -> str:
    """Where the "Connect with Strava" button points. `state` is opaque here — comparing
    it back is the callback route's job, and it is the only CSRF check this flow has."""
    identifier, _ = _credentials()
    params = {
        "client_id": identifier,
        "redirect_uri": _redirect(),
        "response_type": "code",
        "approval_prompt": approval_prompt,
        "scope": scope,
    }
    if state:
        params["state"] = state
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def _token(form: dict[str, str], *, what: str) -> TokenResponse:
    response = await _send(lambda: client().post(TOKEN_URL, data=form), what=what)
    if response.is_error:
        raise _refuse(what, response)
    try:
        return TokenResponse.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        emit("strava.failed", {"what": what, "status": 200, "detail": "unreadable"}, "error")
        raise StravaUnavailable(
            f"{what}: the token response is not a credential — {type(exc).__name__}"
        ) from exc


async def exchange(code: str, *, scope: str = "") -> TokenPair:
    """The callback's `code` for a pair.

    `scope` is the callback's own `scope` parameter, and it is the fallback rather than the
    authority: the reference documents a `scope` in the token body and the getting-started
    example omits it, so whichever arrives is used and the callback's copy covers the case
    where the body has none.
    """
    identifier, secret = _credentials()
    body = await _token(
        {
            "client_id": identifier,
            "client_secret": secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        what="oauth.exchange",
    )
    pair = TokenPair(
        access_token=body.access_token,
        refresh_token=body.refresh_token,
        expires_at=body.expires_at,
        scope=body.scope or scope,
        athlete_id=body.athlete_id,
    )
    emit(
        "strava.token",
        {
            "grant": "authorization_code",
            "athlete_id": pair.athlete_id,
            "scopes": list(pair.scopes),
            "expires_at": pair.expires_at,
            "seconds_left": pair.seconds_left(),
        },
    )
    return pair


async def refresh(pair: TokenPair) -> TokenPair:
    """A NEW pair. The old one may already be dead the instant this returns, so store what
    comes back — both halves, together — before doing anything else with it.

    Verbatim, because this is the most expensive thing here to get wrong: "Please expect
    that this value can change anytime you retrieve a new access token"; once a new refresh
    token is returned "the older code will no longer work"; applications should "persist the
    refresh token contained in the response, and always use the most recent refresh token".

    The response carries no `athlete` and no `scope`, so both ride forward off `pair`.
    """
    identifier, secret = _credentials()
    body = await _token(
        {
            "client_id": identifier,
            "client_secret": secret,
            "grant_type": "refresh_token",
            "refresh_token": pair.refresh_token,
        },
        what="oauth.refresh",
    )
    fresh = TokenPair(
        access_token=body.access_token,
        refresh_token=body.refresh_token,
        expires_at=body.expires_at,
        scope=body.scope or pair.scope,
        athlete_id=body.athlete_id if body.athlete_id is not None else pair.athlete_id,
    )
    emit(
        "strava.token",
        {
            "grant": "refresh_token",
            "athlete_id": fresh.athlete_id,
            "scopes": list(fresh.scopes),
            "expires_at": fresh.expires_at,
            # Strava may hand the same refresh token back. The caller cannot branch on
            # this and must persist either way; it is here so a lockout is diagnosable.
            "rotated": fresh.refresh_token != pair.refresh_token,
        },
    )
    return fresh


async def revoke(pair: TokenPair) -> None:
    """Hand the grant back. Revoking the refresh token takes the access tokens with it,
    which is why this is one call and not two.

    A 200 arrives whether or not the token existed, so it is not proof there was anything
    to revoke. `disconnect()` has to drop its session entry regardless of what this
    raises — the local copy is the part we control.
    """
    identifier, secret = _credentials()
    basic = base64.b64encode(f"{identifier}:{secret}".encode()).decode()
    response = await _send(
        lambda: client().post(
            REVOKE_URL,
            data={"token": pair.refresh_token, "token_type_hint": "refresh_token"},
            headers={"Authorization": f"Basic {basic}"},
        ),
        what="oauth.revoke",
    )
    if response.is_error:
        raise _refuse("oauth.revoke", response)
    emit("strava.revoked", {"athlete_id": pair.athlete_id})


# ── the data path ──────────────────────────────────────────────────────────────


async def fetch_athlete(pair: TokenPair) -> ToolResult:
    """`data` is an `Athlete`. Needs no client credentials — only the access token — so it
    works in a process that could not have issued the token itself."""
    try:
        body = await _read("/athlete", pair, {}, what=TOOL_ATHLETE)
    except StravaError as exc:
        return _as_refusal(TOOL_ATHLETE, exc)
    if not isinstance(body, dict):
        return ToolResult(
            tool=TOOL_ATHLETE,
            outcome=ToolOutcome.UPSTREAM_ERROR,
            detail=f"/athlete decoded to {type(body).__name__}, expected an object",
        )
    try:
        return ToolResult(
            tool=TOOL_ATHLETE, outcome=ToolOutcome.OK, data=Athlete.model_validate(body)
        )
    except ValidationError as exc:
        return ToolResult(
            tool=TOOL_ATHLETE,
            outcome=ToolOutcome.UPSTREAM_ERROR,
            detail=(
                f"/athlete did not parse: {exc.error_count()} field(s), "
                f"first at {exc.errors()[0]['loc']}"
            ),
        )


async def fetch_activities(
    pair: TokenPair,
    *,
    after: int | None = None,
    before: int | None = None,
    per_page: int = PER_PAGE,
    max_pages: int = MAX_PAGES,
) -> ToolResult:
    """`data` is an `ActivityWindow`. `after`/`before` are epoch seconds, Strava's own
    parameters — there is no clock in here deciding what "recent" means.

    An athlete with no activities is `OK` with an empty window: that is an answer about
    training, and it is the one thing a 429 must never be allowed to look like. A page
    that fails to parse costs the whole window rather than shortening it silently.
    """
    size = max(1, int(per_page))
    pages_wanted = max(1, int(max_pages))
    collected: list[Activity] = []
    pages = 0
    truncated = False

    for page in range(1, pages_wanted + 1):
        params: dict[str, Any] = {"page": page, "per_page": size}
        if after is not None:
            params["after"] = int(after)
        if before is not None:
            params["before"] = int(before)
        try:
            body = await _read("/athlete/activities", pair, params, what=TOOL_ACTIVITIES)
        except StravaError as exc:
            return _as_refusal(TOOL_ACTIVITIES, exc)
        if not isinstance(body, list):
            return ToolResult(
                tool=TOOL_ACTIVITIES,
                outcome=ToolOutcome.UPSTREAM_ERROR,
                detail=(
                    f"/athlete/activities page {page} decoded to {type(body).__name__}, "
                    "expected an array"
                ),
            )
        pages = page
        if not body:
            break
        try:
            collected.extend(Activity.model_validate(item) for item in body)
        except ValidationError as exc:
            emit(
                "strava.unparsable",
                {"page": page, "errors": exc.error_count(), "loc": str(exc.errors()[0]["loc"])},
                "error",
            )
            return ToolResult(
                tool=TOOL_ACTIVITIES,
                outcome=ToolOutcome.UPSTREAM_ERROR,
                detail=(
                    f"una actividad de la página {page} no se pudo leer "
                    f"({exc.error_count()} campo(s), {exc.errors()[0]['loc']}). "
                    "No se entrega una ventana incompleta como si estuviera completa."
                ),
            )
        truncated = page == pages_wanted

    window = ActivityWindow(
        activities=tuple(collected),
        pages=pages,
        truncated=truncated,
        after=after,
        before=before,
    )
    emit(
        "strava.activities",
        {
            "count": window.count,
            "pages": window.pages,
            "truncated": window.truncated,
            "after": after,
            "before": before,
        },
    )
    return ToolResult(tool=TOOL_ACTIVITIES, outcome=ToolOutcome.OK, data=window)
