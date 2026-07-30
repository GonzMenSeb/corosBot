"""The Strava OAuth callback: a Starlette route on Reflex's `api_transformer`.

**Why this is not a Reflex page.** Strava redirects the athlete's browser to a plain HTTP
GET carrying `code` and `state`. A Reflex page cannot answer it: the page is compiled JSX
served by a catch-all mount, and by the time any handler of ours could run, the `code` has
been rendered into a document. So the callback is a Starlette route, and the whole design
rests on one measured fact — `App.__call__` mounts Reflex's own router (event channel,
`/ping`, and in prod the compiled frontend's catch-all) into our `api_transformer` as a
single `Mount("")`, and Starlette matches in list order. Our route is earlier, so it wins.
Proved end to end under granian by `scripts/spike_api_transformer.py`, 30 Jul 2026.

**The transformer's own `lifespan=` is dead code and this file does not use one.** Reflex
mounts the transformer inside a Starlette it owns, and a mounted ASGI app is never sent a
lifespan scope — the spike's marker file was never written. `app.py` registers the sweeper
through `app.register_lifespan_task`, which is the hook that does fire.

**How a plain HTTP request finds the browser session that started the flow.** This is the
only genuinely hard part. Brújula keys everything by `router.session.client_token`, and
the callback has no Reflex session to read one from. So the association is made at the
*start* of the flow, in the one place that does have it: `begin(key)` asks
`privacy.issue_state(key)` for an unguessable single-use `state`, records `state -> key`
here, and hands the state to Strava. The callback gets the state back, trades it for the
key, and `privacy.consume_state(key, state)` — single-use, constant-time — is still the
CSRF check. `_PENDING` is not a second CSRF mechanism; it is a routing table, and it holds
two opaque identifiers and no credential.

Three properties make that safe.

  * **A forged callback cannot reach anybody else's session.** An attacker who has not
    seen a `state` has nothing to look up — `secrets.token_urlsafe(24)` is 192 bits — and
    an attacker who uses their OWN state attaches their OWN Strava account to their OWN
    session, which is the login-CSRF this parameter exists to stop. There is no path by
    which a request arriving with a state maps to a session that did not issue it.
  * **A replay is refused twice.** `_PENDING.pop` is single-use and so is
    `consume_state`, which clears the stored value whether or not it matched.
  * **Nothing readable comes back.** The answer is a redirect with an empty body, so a
    mirrored CORS policy has nothing to hand a foreign origin even if `rxconfig.py`'s
    narrowing regressed.

**The browser really does come back as the same session.** Reflex keeps the client token
in `sessionStorage` under `"token"` (`.web/utils/state.js:86-89`), which is scoped to the
tab and the origin — so navigating out to strava.com and back leaves it in place and the
event channel reconnects as the session that issued the state. Measured 30 Jul 2026 over
CDP against two local origins: token set on A, `null` on B, and the same token on A after
coming back. A cookie was the alternative and is not needed; not setting one is one fewer
thing to scope, expire and leak.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

from coros_core.trace import emit
from huella import privacy
from huella.strava import client

ROUTE = "/oauth/strava/callback"

# Re-exported so `state.py` can name the one failure a connect button has to explain
# without importing `huella.strava` itself. The credential path has one door and this is it.
Unconfigured = client.StravaUnconfigured

# Where the athlete is put back. The single page reads `?strava=` on load and says what
# happened; nothing about the credential travels in it.
LANDING = "/"

# An athlete who opened Strava's authorize page and went to lunch. Longer than any real
# decision and short enough that a process serving a public URL for weeks is not holding a
# drawer full of dangling handles.
PENDING_TTL_SECONDS = 900

MAX_PENDING = 200

# What the landing page is told, and the whole vocabulary of it. A refusal never says
# "connected" and a rate limit is never a denial: they are different sentences upstream and
# they stay different here.
OK = "ok"
DENIED = "denied"
BAD_STATE = "state"
NO_CODE = "no_code"
UNCONFIGURED = "unconfigured"
EXPIRED = "expired"
RATE_LIMITED = "rate_limited"
UNREACHABLE = "unreachable"

OUTCOMES: frozenset[str] = frozenset(
    {OK, DENIED, BAD_STATE, NO_CODE, UNCONFIGURED, EXPIRED, RATE_LIMITED, UNREACHABLE}
)

# state -> (reflex client token, issued at). No credential, no athlete, no payload: two
# opaque identifiers, which is the whole reason this map can live outside privacy.py.
_PENDING: OrderedDict[str, tuple[str, float]] = OrderedDict()


def is_configured() -> bool:
    """Whether a connect button can do anything at all. `state.py` asks this rather than
    reaching into `huella.strava` itself — the credential path has one door."""
    return client.is_configured()


def sweep(*, now: float | None = None) -> int:
    stamp = time.time() if now is None else now
    stale = [s for s, (_, at) in _PENDING.items() if stamp - at > PENDING_TTL_SECONDS]
    for state in stale:
        _PENDING.pop(state, None)
    while len(_PENDING) > MAX_PENDING:
        _PENDING.popitem(last=False)
        stale.append("")
    if stale:
        emit("oauth.swept", {"dropped": len(stale), "pending": len(_PENDING)})
    return len(stale)


def pending_count() -> int:
    return len(_PENDING)


def begin(key: str, *, now: float | None = None) -> str:
    """Start a flow for one browser session and answer with the URL to send it to.

    Raises `client.StravaUnconfigured` when the app has no credentials — the caller decides
    what the athlete is told, because "we are not set up" and "Strava said no" are different
    admissions.
    """
    state = privacy.issue_state(key)
    url = client.authorize_url(state=state)
    sweep(now=now)
    _PENDING[state] = (key, time.time() if now is None else now)
    emit("oauth.started", {"pending": len(_PENDING)})
    return url


def _claim(state: str, *, now: float | None = None) -> str:
    """The state's one and only redemption. Empty string when it is unknown, expired or
    already spent — all three are the same answer to the caller and none of them says which."""
    if not state:
        return ""
    sweep(now=now)
    found = _PENDING.pop(state, None)
    return "" if found is None else found[0]


def _land(outcome: str) -> RedirectResponse:
    """A redirect and an empty body. CORS is a browser policy and `rxconfig.py`'s narrowing
    is defence in depth, so the guarantee that nothing leaks here is that there is nothing
    to read: the code was exchanged server-side and the answer is one word about what
    happened, to a browser that already knows it started a flow."""
    return RedirectResponse(f"{LANDING}?strava={outcome}", status_code=303)


async def callback(request: Request) -> RedirectResponse:
    params = request.query_params
    state = params.get("state") or ""
    key = _claim(state)
    # Before anything else, including Strava's own `error`: the state is single-use and a
    # denial that skipped consuming it would leave a redeemable handle behind.
    if not key or not privacy.consume_state(key, state):
        emit("oauth.refused", {"reason": BAD_STATE, "had_state": bool(state)}, "guardrail")
        return _land(BAD_STATE)

    if params.get("error"):
        # `error=access_denied` is the athlete pressing "Cancel" on Strava's own screen. A
        # refusal, not a failure, and the message they get says so.
        emit("oauth.refused", {"reason": DENIED}, "guardrail")
        return _land(DENIED)

    code = params.get("code") or ""
    if not code:
        emit("oauth.refused", {"reason": NO_CODE}, "guardrail")
        return _land(NO_CODE)

    try:
        await privacy.connect(key, code, scope=params.get("scope") or "")
    except client.StravaUnconfigured:
        emit("oauth.refused", {"reason": UNCONFIGURED}, "error")
        return _land(UNCONFIGURED)
    except client.StravaAuthDenied:
        # The code was already spent, or it was never ours. Retrying cannot help.
        emit("oauth.refused", {"reason": EXPIRED}, "error")
        return _land(EXPIRED)
    except client.StravaRateLimited:
        emit("oauth.refused", {"reason": RATE_LIMITED}, "error")
        return _land(RATE_LIMITED)
    except client.StravaError:
        # The class only. A detail here is Strava's own prose about a credential exchange,
        # and this string ends up in a URL bar.
        emit("oauth.refused", {"reason": UNREACHABLE}, "error")
        return _land(UNREACHABLE)

    emit("oauth.connected", {"pending": len(_PENDING)})
    return _land(OK)


# GET only. A POST answering 405 rather than 404 is one of the spike's probes: a 404 would
# mean the frontend mount had swallowed the path.
ROUTES: list[Route] = [Route(ROUTE, callback, methods=["GET"])]
