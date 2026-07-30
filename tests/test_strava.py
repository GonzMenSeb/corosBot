"""Huella's one door to Strava, pinned.

Every wire fact below comes from `apps/huella/huella/strava/client.py` and `models.py` —
which record where each one was read from on 30 Jul 2026 — and from `AGENTS.md`
"### Strava integration (Huella only)". Nothing here was invented: the request bodies are
built from the fields the models actually declare and the aliases they actually read, and
the response bodies carry only those fields plus the three extras `models.py` names as
present in Strava's own `SummaryActivity` example and absent from its schema.

The offline half fakes the transport entirely, and it has to: a read costs a slice of a
window of 100 per 15 minutes, that window resets on the wall clock rather than fifteen
minutes after the refusal, and a 429 latches the whole process until the boundary. So the
`sealed` fixture below makes an unstubbed call an immediate, named failure instead of a
request. The `live` half proves the fake still describes reality; it skips entirely while
`STRAVA_CLIENT_ID` is unset, which it is — Standard Tier now needs a paid subscription and
the repo owner has registered no app yet (`AGENTS.md`).
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from coros_core import trace
from coros_core.outcomes import ToolOutcome
from huella.strava import client as strava
from huella.strava.client import (
    API,
    AUTHORIZE_URL,
    REVOKE_URL,
    SCOPES,
    TOKEN_URL,
    WINDOW_SECONDS,
    StravaAuthDenied,
    StravaRateLimited,
    StravaScopeDenied,
    StravaUnavailable,
    StravaUnconfigured,
)
from huella.strava.models import (
    REFRESH_SKEW_SECONDS,
    Activity,
    ActivityWindow,
    Athlete,
    RateLimit,
    TokenPair,
)

REPO = Path(__file__).resolve().parent.parent
MODELS = REPO / "apps" / "huella" / "huella" / "strava" / "models.py"
CLIENT = REPO / "apps" / "huella" / "huella" / "strava" / "client.py"
FACTS = 'AGENTS.md "### Strava integration (Huella only)"'

# Strava publishes 100 reads per 15 minutes, so a sustained trickle is one call every 9 s
# and a handful spaced this far apart spends a few percent of one window. Sequential and
# spaced for the same reason ucp.py and catalog.py are: a burst is what trips a limiter.
# Never `asyncio.gather` in the live probe.
SPACING = 1.5

# Forty hex characters is the shape Strava issues. The length matters: pydantic truncates
# long values in its error messages, so a short stand-in would prove less than it looks.
ACCESS = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
REFRESH = "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"

# The three scopes Huella asks for, and the full set of seven that exist. Both from
# `AGENTS.md`; `client.SCOPES` is checked against them rather than restated.
STRAVA_SCOPES = frozenset(
    {
        "read",
        "read_all",
        "profile:read_all",
        "profile:write",
        "activity:read",
        "activity:read_all",
        "activity:write",
    }
)

# The changelog's 30 Apr 2026 entry added six values to a 56-value list that grows by
# design. Nothing that follows is a real one — that is the point.
UNHEARD_OF_SPORT = "UnderwaterPadelOnHorseback"


def pair(**over: Any) -> TokenPair:
    fields: dict[str, Any] = {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_at": int(time.time()) + 21_600,
        "scope": SCOPES,
        "athlete_id": 98_765,
    }
    fields.update(over)
    return TokenPair(**fields)


def token_body(**over: Any) -> dict[str, Any]:
    """The token endpoint's response, built from what `TokenResponse` declares plus the
    `token_type`/`expires_in` the exchange also sends and this module deliberately ignores
    (`expires_at` is carried as Strava's own absolute epoch, not recomputed)."""
    body: dict[str, Any] = {
        "token_type": "Bearer",
        "expires_at": int(time.time()) + 21_600,
        "expires_in": 21_600,
        "refresh_token": "c0ffeec0ffeec0ffeec0ffeec0ffeec0ffeec0ff",
        "access_token": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    body.update(over)
    return body


def athlete_body(**over: Any) -> dict[str, Any]:
    """Exactly the eight fields `Athlete` declares, by their wire names."""
    body: dict[str, Any] = {
        "id": 98_765,
        "username": "ana_r",
        "firstname": "Ana",
        "lastname": "Restrepo",
        "city": "Bogotá",
        "state": "Cundinamarca",
        "country": "Colombia",
        "measurement_preference": "meters",
    }
    body.update(over)
    return body


def activity_body(identifier: int = 14_820_001, **over: Any) -> dict[str, Any]:
    """Exactly the twelve fields `Activity` declares, by their wire names — `id`,
    `distance`, `moving_time`, `elapsed_time` and `total_elevation_gain` are the aliased
    ones. `start_date_local` carries the same `Z` it has not earned that the swagger's own
    example does: 11:15:09Z as an instant, 06:15:09Z as local wall time."""
    body: dict[str, Any] = {
        "id": identifier,
        "name": "Fondo dominical",
        "sport_type": "Run",
        "distance": 21_097.5,
        "moving_time": 6_240,
        "elapsed_time": 6_480,
        "total_elevation_gain": 312.0,
        "start_date": "2026-07-20T11:15:09Z",
        "start_date_local": "2026-07-20T06:15:09Z",
        "trainer": False,
        "commute": False,
        "manual": False,
    }
    body.update(over)
    return body


def _response(
    status: int,
    body: Any = None,
    *,
    url: str = f"{API}/athlete",
    method: str = "GET",
    **headers: str,
) -> httpx.Response:
    request = httpx.Request(method, url)
    if body is None:
        return httpx.Response(status, text="<html>strava</html>", headers=headers, request=request)
    return httpx.Response(status, json=body, headers=headers, request=request)


def _read_headers(usage: str = "3,41", limit: str = "100,1000") -> dict[str, str]:
    return {"X-ReadRateLimit-Limit": limit, "X-ReadRateLimit-Usage": usage}


class _Transport:
    """Stands in for `strava.client()`. Records every request it was asked to make, which
    is how "the second call never touched the network" is asserted rather than assumed."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.queue: list[httpx.Response | Exception] = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.is_closed = False

    def _next(self, method: str, url: str, payload: dict[str, Any]) -> httpx.Response:
        self.calls.append((method, url, payload))
        item = self.queue.pop(0) if self.queue else _response(200, [], url=url, method=method)
        if isinstance(item, Exception):
            raise item
        return item

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._next("GET", url, {"params": params or {}, "headers": headers or {}})

    async def post(
        self,
        url: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._next("POST", url, {"data": data or {}, "headers": headers or {}})

    @property
    def urls(self) -> list[str]:
        return [url for _method, url, _payload in self.calls]


@pytest.fixture(autouse=True)
def sealed(request, monkeypatch):
    """The release blocker for this file: nothing in the offline suite may reach Strava.

    A stub that is forgotten, or a code path that grows a second request nobody stubbed,
    would otherwise spend a real slice of a 100-per-15-minutes window from CI — and a 429
    there latches this module's process-global cooldown for up to fifteen wall-clock
    minutes. So the real transport is removed rather than trusted: any request that
    escapes a stub dies here, named, instead of leaving the machine.
    """
    if request.node.get_closest_marker("live"):
        yield
        return

    def refuse(self, request_, *args, **kwargs):  # noqa: ANN001
        raise AssertionError(
            f"an offline test reached the network: {request_.method} {request_.url}\n"
            "  Every test in this file has to install the `transport` stub. A real read\n"
            "  spends a slice of Strava's 100-per-15-minutes window and a 429 latches\n"
            "  `strava._paced_until` until the next wall-clock quarter-hour."
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    yield


@pytest.fixture(autouse=True)
def unpaced():
    """`_paced_until` and `_limits` are process-global. The latch decays on the window's
    own wall clock, so 900 s of decay inside a suite is indistinguishable from a hang —
    it is reset around every test, live ones included, where a real 429 would otherwise
    silently pace the rest of the module."""
    strava.reset_pacing()
    yield
    strava.reset_pacing()


@pytest.fixture
def credentials(monkeypatch):
    """What a registered app looks like. `redirect_uri` is checked by Strava against the
    app's callback DOMAIN and localhost is white-listed, so this is the dev value."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "173829")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "5c1e2a9b7d3f4068a1b2c3d4e5f60718293a4b5c")
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")


@pytest.fixture
def transport(monkeypatch):
    def install(*responses: httpx.Response | Exception) -> _Transport:
        stub = _Transport(*responses)
        monkeypatch.setattr(strava, "client", lambda: stub)
        return stub

    return install


def _function_bodies(tree: ast.Module) -> set[int]:
    """Every node that only runs when something is called."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside.update(id(child) for child in ast.walk(node))
    return inside


class TestUnconfiguredIsARefusalAndNotAnImportError:
    """The whole reason nothing here reads `.env`: Huella boots, renders a connect screen
    and passes its suite on a machine that has never heard of Strava. The failure arrives
    when someone tries to connect, typed, and never at import."""

    def test_the_module_reads_no_environment_variable_at_import_time(self):
        tree = ast.parse(CLIENT.read_text())
        inside = _function_bodies(tree)
        offenders = [
            f"client.py:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in ("environ", "getenv")
            and id(node) not in inside
        ]
        assert not offenders, (
            f"client.py reads the environment at import time ({offenders}). The credentials\n"
            "  are looked up at CALL time on purpose — a module-level read makes the import\n"
            "  itself depend on a registered Strava app, and every test in this repo, the\n"
            f"  Reflex app's boot and the connect screen with it. See {FACTS}."
        )

    def test_importing_it_with_no_credentials_works_and_calling_it_refuses(self):
        """A subprocess, because the claim is about a fresh interpreter: `sys.modules`
        already holds this module by the time any in-process test could ask."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("STRAVA_")}
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO), str(REPO / "packages"), str(REPO / "apps" / "huella")]
        )
        script = (
            "from huella.strava import client\n"
            "assert client.is_configured() is False\n"
            "try:\n"
            "    client.authorize_url()\n"
            "except client.StravaUnconfigured as exc:\n"
            "    print(type(exc).__name__)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, (
            "importing huella.strava.client with no STRAVA_* variables set failed:\n"
            f"  {done.stderr.strip()[-600:]}"
        )
        assert done.stdout.strip() == "StravaUnconfigured", (
            f"the refusal is not typed at call time. Got {done.stdout.strip()!r} — "
            f"{FACTS} and client.py's docstring both pin StravaUnconfigured."
        )

    def test_every_reader_of_the_credentials_answers_empty_rather_than_raising(
        self, monkeypatch
    ):
        for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REDIRECT_URI"):
            monkeypatch.delenv(name, raising=False)
        assert (strava.client_id(), strava.client_secret(), strava.redirect_uri()) == ("", "", "")
        assert strava.is_configured() is False

    @pytest.mark.parametrize(
        "missing", ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REDIRECT_URI"]
    )
    def test_any_one_missing_variable_is_enough_to_be_unconfigured(
        self, credentials, monkeypatch, missing
    ):
        monkeypatch.delenv(missing)
        assert strava.is_configured() is False

    def test_whitespace_only_credentials_do_not_count_as_configured(self, monkeypatch):
        monkeypatch.setenv("STRAVA_CLIENT_ID", "   ")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "\t")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", " ")
        assert strava.is_configured() is False

    async def test_the_whole_credential_path_refuses_without_a_registered_app(self, monkeypatch):
        for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REDIRECT_URI"):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(StravaUnconfigured):
            strava.authorize_url()
        with pytest.raises(StravaUnconfigured):
            await strava.exchange("abc123")
        with pytest.raises(StravaUnconfigured):
            await strava.refresh(pair())
        with pytest.raises(StravaUnconfigured):
            await strava.revoke(pair())

    async def test_a_missing_redirect_uri_alone_still_refuses_before_a_url_is_built(
        self, monkeypatch, credentials
    ):
        monkeypatch.delenv("STRAVA_REDIRECT_URI")
        with pytest.raises(StravaUnconfigured) as caught:
            strava.authorize_url()
        assert "Authorization Callback" in str(caught.value), (
            f"the refusal stopped explaining what Strava validates. {FACTS}: the redirect is\n"
            "  checked against the app's callback DOMAIN, not a registered URI, which is why\n"
            "  no exact-URI-match logic exists anywhere and dev needs no re-registration."
        )

    async def test_a_read_needs_only_an_access_token_and_no_client_credentials(
        self, monkeypatch, transport
    ):
        """`fetch_athlete` runs in a process that could not have issued the token itself."""
        for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REDIRECT_URI"):
            monkeypatch.delenv(name, raising=False)
        transport(_response(200, athlete_body()))

        result = await strava.fetch_athlete(pair())

        assert result.outcome is ToolOutcome.OK
        assert result.data.athlete_id == 98_765


class TestTheOAuthUrlsAreTheOnesTheMigrationLeavesAlone:
    """Four constants, and three of them look wrong against Strava's own documentation.
    The measurement is what this repo records; re-measure before "fixing" any of them."""

    def test_the_token_endpoint_is_the_oauth_one_and_not_the_api_v3_one(self):
        assert TOKEN_URL == "https://www.strava.com/oauth/token", (
            f"TOKEN_URL was changed to {TOKEN_URL!r}. This is the entry {FACTS} exists for.\n"
            "  BOTH endpoints are live: measured 30 Jul 2026, a POST carrying a bogus\n"
            "  client_id to /oauth/token and to /api/v3/oauth/token returned byte-identical\n"
            '  400 bodies ({"resource":"Application","field":"client_id","code":"invalid"}).\n'
            "  The docs' curl examples and the swagger tokenUrl show only the /api/v3 form,\n"
            "  and a Strava community thread claims /oauth/token does not work — neither is\n"
            "  what the measurement says. The REASON for this spelling is the migration: the\n"
            "  4 Jan 2027 base-URL change moves /api/v3 to api-v3.strava.com and leaves the\n"
            "  OAuth host alone, so this is the path that never has to change. Do not\n"
            "  'correct' it to /api/v3/oauth/token without re-measuring both."
        )
        assert not TOKEN_URL.startswith(API), (
            "the token endpoint now sits under the API base, which is the host the\n"
            "  4 Jan 2027 migration moves. That is precisely what TOKEN_URL avoids."
        )

    def test_the_api_base_is_still_the_pre_migration_host_spelled_once(self):
        assert API == "https://www.strava.com/api/v3", (
            f"API is {API!r}. {FACTS}: the base moves to https://api-v3.strava.com on\n"
            "  4 Jan 2027 and is not live yet. A widely-circulated third-party summary gives\n"
            "  the host as www.api-v3.strava.com with a Jun 2027 date and both are wrong —\n"
            "  the changelog is the source. Update AGENTS.md and this test together."
        )
        source = CLIENT.read_text()
        assert source.count('"https://www.strava.com/api/v3"') == 1, (
            "the API base is spelled more than once. It is a module constant so the\n"
            "  4 Jan 2027 migration is one line; a second spelling is a line someone misses."
        )

    def test_revoking_is_revoke_and_never_the_retired_deauthorize(self):
        assert REVOKE_URL == "https://www.strava.com/oauth/revoke", (
            f"REVOKE_URL is {REVOKE_URL!r}. {FACTS}: revoke replaced POST /oauth/deauthorize\n"
            "  on 1 Jun 2026 and deauthorize is retired 1 Jun 2027. Never call deauthorize."
        )
        assert "deauthorize" not in REVOKE_URL

    def test_the_authorize_url_is_the_oauth_host_too(self):
        assert AUTHORIZE_URL == "https://www.strava.com/oauth/authorize"

    def test_the_three_scopes_asked_for_are_real_strava_scopes(self):
        asked = SCOPES.split(",")
        assert asked == ["read", "activity:read_all", "profile:read_all"], (
            f"the requested scopes changed to {asked}. {FACTS} pins these three; Huella reads\n"
            "  and writes nothing, and there is no wider history scope to reach for."
        )
        unknown = set(asked) - STRAVA_SCOPES
        assert not unknown, (
            f"{sorted(unknown)} is not a Strava scope. The seven that exist are\n"
            f"  {sorted(STRAVA_SCOPES)} — a scope Strava does not know makes the whole\n"
            "  authorize URL a 400 the athlete sees, not a narrower grant."
        )
        assert not any(scope.endswith(":write") for scope in asked), (
            "a write scope appeared in Huella's request. Huella only ever reads."
        )


class TestTheAuthorizeUrlCarriesTheWholeHandshake:
    def test_it_carries_the_client_the_callback_the_grant_type_and_the_scopes(
        self, credentials
    ):
        url = strava.authorize_url()
        base, _, query = url.partition("?")
        params = dict(part.split("=", 1) for part in query.split("&"))

        assert base == AUTHORIZE_URL
        assert params["client_id"] == "173829"
        assert params["response_type"] == "code", (
            "response_type must be `code`: this is the authorization-code flow and the only "
            "one Strava offers."
        )
        assert params["redirect_uri"] == "http%3A%2F%2Flocalhost%3A8001%2Foauth%2Fstrava%2Fcallback"
        assert params["scope"] == "read%2Cactivity%3Aread_all%2Cprofile%3Aread_all"
        assert params["approval_prompt"] == "auto"

    def test_state_rides_along_when_given_and_is_absent_when_not(self, credentials):
        assert "state=nonce-7f3a" in strava.authorize_url(state="nonce-7f3a")
        assert "state=" not in strava.authorize_url(), (
            "an empty state must not be sent as `state=`. Comparing it back is the callback\n"
            "  route's job and it is the only CSRF check this flow has — an empty value the\n"
            "  route then 'matches' is the check quietly passing on nothing."
        )

    def test_a_narrower_scope_and_a_forced_prompt_are_both_callable(self, credentials):
        url = strava.authorize_url(scope="read", approval_prompt="force")
        assert "scope=read&" in url or url.endswith("scope=read")
        assert "approval_prompt=force" in url

    def test_no_secret_is_ever_put_in_a_url_the_athlete_sees(self, credentials):
        assert strava.client_secret() not in strava.authorize_url(state="s"), (
            "the client secret reached the authorize URL. That URL is pasted into a browser\n"
            "  address bar, logged by every proxy in between and kept in history."
        )


class TestARefreshInvalidatesThePairYouHad:
    """The most expensive fact in the module. Verbatim from Strava: "Please expect that
    this value can change anytime you retrieve a new access token"; once a new refresh
    token is returned "the older code will no longer work"; applications must "persist the
    refresh token contained in the response, and always use the most recent refresh
    token". So the old pair is dead the instant this returns, whether or not the token
    came back changed, and half a pair on disk is an athlete locked out of their history.
    """

    async def test_it_hands_back_a_new_pair_and_leaves_the_old_one_untouched(
        self, credentials, transport
    ):
        old = pair()
        transport(_response(200, token_body(), url=TOKEN_URL, method="POST"))

        fresh = await strava.refresh(old)

        assert fresh is not old
        assert fresh.access_token == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        assert fresh.refresh_token == "c0ffeec0ffeec0ffeec0ffeec0ffeec0ffeec0ff"
        assert (old.access_token, old.refresh_token) == (ACCESS, REFRESH), (
            "refresh() mutated the pair it was given. Strava: once a new refresh token is\n"
            '  returned "the older code will no longer work" — so the caller needs BOTH the\n'
            "  old and the new pair in hand to persist the new one atomically. Mutating in\n"
            "  place is how one half reaches disk while the other stays in memory."
        )

    def test_a_pair_cannot_be_mutated_at_all_because_a_rotation_is_a_new_pair(self):
        held = pair()
        with pytest.raises(Exception) as caught:  # pydantic raises ValidationError
            held.refresh_token = "rotated-in-place"
        assert "frozen" in str(caught.value).lower(), (
            "TokenPair stopped being frozen. A rotation is a NEW pair: an in-place edit is\n"
            "  how one half of a credential reaches disk while the other stays in memory,\n"
            "  and by then Strava has invalidated whichever half got left behind."
        )
        assert held.refresh_token == REFRESH

    async def test_the_same_refresh_token_coming_back_is_still_a_rotation_to_persist(
        self, credentials, transport
    ):
        """Strava may hand the same value back, so the caller cannot tell a rotation from
        a no-op and has to persist either way."""
        old = pair()
        transport(
            _response(200, token_body(refresh_token=REFRESH), url=TOKEN_URL, method="POST")
        )

        fresh = await strava.refresh(old)

        assert fresh.refresh_token == REFRESH
        assert fresh is not old
        rotations = [e for e in trace.events() if e.event == "strava.token"]
        assert rotations[-1].payload["rotated"] is False, (
            "the `rotated` flag is what makes a lockout diagnosable after the fact. It is\n"
            "  NOT a branch the caller may take: persist the returned pair either way."
        )

    async def test_the_scope_and_the_athlete_ride_forward_because_the_response_has_neither(
        self, credentials, transport
    ):
        old = pair(scope="read,activity:read_all", athlete_id=4_242)
        transport(_response(200, token_body(), url=TOKEN_URL, method="POST"))

        fresh = await strava.refresh(old)

        assert fresh.scope == "read,activity:read_all", (
            "the granted scope was lost on refresh. The refresh response carries no `scope`\n"
            "  and no `athlete`, so both have to be carried forward off the old pair — an\n"
            "  empty scope reads downstream as a grant that no longer covers the read."
        )
        assert fresh.athlete_id == 4_242

    async def test_both_grants_post_form_encoded_to_the_same_token_endpoint(
        self, credentials, transport
    ):
        stub = transport(
            _response(200, token_body(scope="read,activity:read_all"), url=TOKEN_URL, method="POST"),
            _response(200, token_body(), url=TOKEN_URL, method="POST"),
        )

        await strava.exchange("returned-by-the-callback")
        await strava.refresh(pair())

        assert stub.urls == [TOKEN_URL, TOKEN_URL]
        exchange_form, refresh_form = (call[2]["data"] for call in stub.calls)
        assert exchange_form["grant_type"] == "authorization_code"
        assert exchange_form["code"] == "returned-by-the-callback"
        assert refresh_form["grant_type"] == "refresh_token"
        assert refresh_form["refresh_token"] == REFRESH, (
            "the refresh grant must send the REFRESH token, never the access token."
        )
        assert "code" not in refresh_form

    async def test_the_exchange_prefers_the_bodys_scope_and_falls_back_to_the_callbacks(
        self, credentials, transport
    ):
        transport(
            _response(200, token_body(scope="read,activity:read_all"), url=TOKEN_URL, method="POST"),
            _response(200, token_body(), url=TOKEN_URL, method="POST"),
        )

        from_body = await strava.exchange("code-1", scope="read")
        from_callback = await strava.exchange("code-2", scope="read,profile:read_all")

        assert from_body.scope == "read,activity:read_all"
        assert from_callback.scope == "read,profile:read_all", (
            "the reference documents a `scope` in the token body and the getting-started\n"
            "  example omits it, so the callback's own copy is the fallback. Dropping it\n"
            "  leaves a working grant looking scopeless."
        )

    async def test_a_reused_code_or_a_rotated_away_token_is_a_reconnect_and_not_a_retry(
        self, credentials, transport
    ):
        denial = {
            "message": "Bad Request",
            "errors": [{"resource": "RefreshToken", "field": "refresh_token", "code": "invalid"}],
        }
        transport(_response(400, denial, url=TOKEN_URL, method="POST"))

        with pytest.raises(StravaAuthDenied) as caught:
            await strava.refresh(pair())

        assert caught.value.status == 400
        assert "invalid" in caught.value.detail

    async def test_nothing_in_the_credential_path_retries(self, credentials, transport):
        """A timed-out refresh may already have rotated upstream, so a second attempt with
        the old token is the lockout by another route."""
        stub = transport(
            httpx.ReadTimeout("strava took too long"),
            _response(200, token_body(), url=TOKEN_URL, method="POST"),
        )

        with pytest.raises(strava.StravaTimeout):
            await strava.refresh(pair())

        assert len(stub.calls) == 1, (
            "the refresh was retried. A timed-out refresh may already have rotated the token\n"
            "  upstream; retrying with the old one is exactly the lockout this avoids."
        )

    async def test_revoking_hands_back_the_grant_over_basic_auth_on_the_revoke_endpoint(
        self, credentials, transport
    ):
        stub = transport(_response(200, {}, url=REVOKE_URL, method="POST"))

        assert await strava.revoke(pair()) is None

        method, url, payload = stub.calls[0]
        assert (method, url) == ("POST", REVOKE_URL)
        assert payload["data"] == {"token": REFRESH, "token_type_hint": "refresh_token"}, (
            "revoke posts the REFRESH token in the form body — revoking it takes the access\n"
            "  tokens with it, which is why this is one call and not two. The retired\n"
            "  /oauth/deauthorize took the ACCESS token in the query string instead."
        )
        assert payload["headers"]["Authorization"].startswith("Basic "), (
            f"revoke stopped using Basic auth over the client credentials. {FACTS}: that is\n"
            "  the difference between /oauth/revoke and the retired /oauth/deauthorize."
        )
        assert "Bearer" not in payload["headers"]["Authorization"]


class TestATokenNeverTravelsInSomethingPrintable:
    """`model_dump()` still yields both halves, because persistence needs them. Everything
    else — a repr, an f-string, a traceback frame, a refusal detail — must not. A redaction
    that also broke the dump would lock every connected athlete out of their own history,
    so both halves are pinned here together."""

    def test_neither_half_survives_a_repr_an_f_string_or_a_container(self):
        held = pair()
        for rendered in (repr(held), str(held), f"{held}", f"{held!r}", repr([held]), repr({"p": held})):
            for secret in (ACCESS, REFRESH):
                assert secret not in rendered, (
                    f"a token leaked through {rendered[:60]!r}…. `__repr_args__` redacts every\n"
                    "  field whose name ends in `_token`; a rename or a removal puts a live\n"
                    "  credential in the next traceback, log line or exception message."
                )
            assert "…redacted…" in rendered

    def test_model_dump_still_yields_both_halves_because_persistence_needs_them(self):
        dumped = pair().model_dump()
        assert dumped["access_token"] == ACCESS
        assert dumped["refresh_token"] == REFRESH, (
            "the redaction reached model_dump(). A dump that cannot round-trip a credential\n"
            "  locks every connected athlete out at the next restart — this is the half of\n"
            "  the redaction that must NOT work."
        )
        assert TokenPair(**dumped) == pair()

    def test_a_field_level_validation_error_carries_no_token(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as caught:
            TokenPair(access_token=ACCESS, refresh_token=REFRESH, expires_at="not-an-epoch")
        rendered = str(caught.value)
        assert ACCESS not in rendered and REFRESH not in rendered, (
            "a pydantic error message carried a token. pydantic renders the offending input\n"
            "  only, so a valid token beside an invalid field must not appear — this is one\n"
            "  of the accidental paths TokenPair's redaction exists for."
        )

    async def test_an_unreadable_token_response_is_reported_by_type_and_never_by_body(
        self, credentials, transport
    ):
        """pydantic builds a missing-field error out of the whole input dict, so the raw
        `ValidationError` for a truncated token body carries part of the credential. This
        is the containment: the refusal names the exception TYPE, not its message."""
        transport(
            _response(200, {"access_token": ACCESS, "refresh_token": REFRESH}, url=TOKEN_URL, method="POST")
        )

        with pytest.raises(StravaUnavailable) as caught:
            await strava.exchange("code-1")

        detail = caught.value.detail
        assert "ValidationError" in detail
        for secret in (ACCESS, REFRESH, ACCESS[-20:], REFRESH[-20:]):
            assert secret not in detail, (
                f"the token response leaked into a refusal detail: {detail!r}. `_token()`\n"
                "  reports `type(exc).__name__` on purpose — pydantic's own message embeds a\n"
                "  truncated repr of the input dict, which includes the tail of a token."
            )

    async def test_no_trace_event_on_the_credential_path_carries_a_token(
        self, credentials, transport
    ):
        transport(
            _response(200, token_body(scope=SCOPES, athlete={"id": 98_765}), url=TOKEN_URL, method="POST"),
            _response(200, token_body(), url=TOKEN_URL, method="POST"),
            _response(200, {}, url=REVOKE_URL, method="POST"),
        )

        await strava.exchange("code-1")
        await strava.refresh(pair())
        await strava.revoke(pair())

        recorded = json.dumps([event.as_dict() for event in trace.events()])
        for secret in (ACCESS, REFRESH, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "c0ffee" * 6):
            assert secret not in recorded, (
                "a token reached the trace. The ring is read by evidence.py, rendered in the\n"
                "  trace panel and pasted into a model's context — a credential that travels\n"
                "  that far is a credential in a log aggregator."
            )
        assert [e.event for e in trace.events()][:2] == ["strava.token", "strava.token"]

    async def test_a_read_carries_the_access_token_in_a_header_and_not_in_the_query(
        self, credentials, transport
    ):
        stub = transport(_response(200, athlete_body()))

        await strava.fetch_athlete(pair())

        _method, url, payload = stub.calls[0]
        assert payload["headers"]["Authorization"] == f"Bearer {ACCESS}"
        assert ACCESS not in url and ACCESS not in json.dumps(payload["params"]), (
            "the access token reached the URL. Query strings are logged by every proxy in\n"
            "  between; the retired /oauth/deauthorize did this and is the reason not to."
        )

    def test_the_expiry_horizon_is_strava_own_absolute_epoch(self):
        now = 1_784_000_000.0
        held = pair(expires_at=int(now) + 21_600)
        assert held.seconds_left(now) == 21_600
        assert held.is_expired(now) is False
        assert held.needs_refresh(now) is False
        assert REFRESH_SKEW_SECONDS == 3_600
        assert held.needs_refresh(now + 21_600 - 3_600) is True
        assert held.needs_refresh(now + 21_600 - 3_601) is False, (
            "the one-hour refresh skew moved. Strava returns the SAME access token while more\n"
            "  than an hour is left on it, so an hour before expiry is also the first moment\n"
            "  at which refreshing does anything — earlier spends a rotation for nothing and\n"
            "  later hands a read a credential that dies mid-walk."
        )
        assert pair(expires_at=int(now)).is_expired(now) is True

    def test_a_granted_scope_splits_on_either_delimiter_because_both_arrive(self):
        assert pair(scope="read activity:read_all").scopes == ("read", "activity:read_all")
        assert pair(scope="read,activity:read_all").scopes == ("read", "activity:read_all")
        assert pair(scope="").scopes == ()
        assert pair(scope="read,activity:read_all").has_scope("profile:read_all") is False, (
            "the granted scope can be NARROWER than the requested one — the athlete may\n"
            "  uncheck boxes — and it arrives space-delimited in the token body and\n"
            "  comma-delimited in the callback query string."
        )


class TestARateLimitIsNeverAnEmptyHistory:
    """The one thing this module exists to make impossible. Strava sends no `Retry-After`
    and its 15-minute window resets on the wall clock at :00/:15/:30/:45, so the wait is
    computed to that boundary and a 429 latches until it — polling only spends a window we
    have already been told is spent."""

    async def test_a_429_is_reported_not_flattened_to_an_empty_list(self, transport):
        transport(_response(429, {"message": "Rate Limit Exceeded"}))

        result = await strava.fetch_activities(pair())

        assert result.outcome is ToolOutcome.RATE_LIMITED, (
            f"a 429 came back as {result.outcome.name}. {FACTS}: a 429 means the request was\n"
            "  REFUSED — never silently drop it to an empty list. `OK` with an empty window\n"
            "  is a claim about an athlete's training that we would be inventing."
        )
        assert result.data is None
        assert result.outcome.is_transient is True and result.outcome.is_conclusive is False

    async def test_the_wait_is_computed_to_the_next_quarter_hour_and_never_guessed(
        self, transport
    ):
        transport(_response(429, {"message": "Rate Limit Exceeded"}))

        result = await strava.fetch_activities(pair())

        assert result.retry_after_s is not None
        assert 0 < result.retry_after_s <= WINDOW_SECONDS, (
            f"the wait is {result.retry_after_s}. Strava sends NO Retry-After header and the\n"
            f"  window resets on the wall clock, so the wait is bounded by one window\n"
            f"  ({WINDOW_SECONDS}s) and is always positive — a zero would mean 'send now',\n"
            "  into a limiter that just refused."
        )
        assert f"{result.retry_after_s:.0f}" in result.detail, (
            "the refusal stopped carrying its own wait. It is the only place the UI and the\n"
            "  trace panel learn how long the cooldown stays shut."
        )

    @pytest.mark.parametrize(
        "stamp,expected",
        [(1_784_000_000.0, 700.0), (1_783_999_800.0, 900.0), (1_783_999_801.0, 899.0)],
        ids=["mid-window", "exactly-on-a-boundary", "one-second-past"],
    )
    def test_the_window_is_a_wall_clock_quarter_hour_and_not_fifteen_minutes_from_now(
        self, stamp, expected
    ):
        assert strava.seconds_to_window_reset(stamp) == expected, (
            "the reset arithmetic changed. Unix time ignores leap seconds, so epoch modulo\n"
            f"  {WINDOW_SECONDS} is exactly the position inside the current window. A refusal\n"
            "  at :14:59 clears one second later, not fifteen minutes later."
        )
        assert 0 < strava.seconds_to_window_reset(stamp) <= WINDOW_SECONDS

    async def test_the_second_call_after_a_429_never_touches_the_network(self, transport):
        stub = transport(
            _response(429, {"message": "Rate Limit Exceeded"}),
            _response(200, [activity_body()]),
        )

        first = await strava.fetch_activities(pair())
        second = await strava.fetch_activities(pair())

        assert (first.outcome, second.outcome) == (
            ToolOutcome.RATE_LIMITED,
            ToolOutcome.RATE_LIMITED,
        )
        assert len(stub.calls) == 1, (
            f"the second read reached Strava while the latch was closed ({len(stub.calls)}\n"
            f"  requests). {FACTS}: sending inside a window we have been told is spent buys a\n"
            "  second refusal and nothing else. The latch decays on the window's own clock."
        )
        assert strava.is_paced() is True
        assert strava.cooldown_remaining() == pytest.approx(second.retry_after_s, abs=0.5), (
            "`cooldown_remaining()` stopped agreeing with the wait the refusal reports. It is\n"
            "  time LEFT, counted down to the boundary — a constant, or an elapsed age, tells\n"
            "  a reader chasing an outage the opposite of the truth."
        )
        strava.reset_pacing()
        assert strava.cooldown_remaining() == 0.0
        assert "no se consulta dentro del enfriamiento" in second.detail

    async def test_a_read_is_served_again_once_the_window_has_actually_turned(
        self, transport, monkeypatch
    ):
        stub = transport(
            _response(429, {"message": "Rate Limit Exceeded"}),
            _response(200, [activity_body()]),
        )
        await strava.fetch_activities(pair())

        monkeypatch.setattr(strava, "_paced_until", time.time() - 1)
        served = await strava.fetch_activities(pair(), max_pages=1)

        assert served.outcome is ToolOutcome.OK
        assert len(stub.calls) == 2, (
            "the latch stopped decaying. Unlike ucp.py's one-way latch this one has to\n"
            "  expire: Strava's window is published and honest, so there IS a time after\n"
            "  which sending again is correct, and a permanent latch ends the session."
        )

    async def test_an_exhausted_window_latches_before_the_next_call_earns_the_refusal(
        self, transport
    ):
        stub = transport(
            _response(200, [], **_read_headers(usage="100,412")),
            _response(200, [activity_body()]),
        )

        served = await strava.fetch_activities(pair())
        refused = await strava.fetch_activities(pair())

        assert served.outcome is ToolOutcome.OK
        assert strava.is_paced() is True, (
            "a response reporting 100/100 used in the window did not latch. The next call\n"
            "  WOULD be a 429; latching on the headers keeps it off the network instead of\n"
            "  buying the refusal to find out."
        )
        assert refused.outcome is ToolOutcome.RATE_LIMITED
        assert len(stub.calls) == 1

    async def test_a_read_latch_never_strands_a_reconnect(self, credentials, transport):
        """The latch is set off a READ window. Refusing a refresh because a read was
        throttled would strand an athlete over a counter that does not govern the token
        endpoint — which sends these headers not at all."""
        stub = transport(
            _response(429, {"message": "Rate Limit Exceeded"}),
            _response(200, token_body(), url=TOKEN_URL, method="POST"),
        )
        await strava.fetch_activities(pair())
        assert strava.is_paced() is True

        fresh = await strava.refresh(pair())

        assert fresh.access_token == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        assert len(stub.calls) == 2, (
            "the refresh was refused by the read cooldown. Only the DATA path honours it."
        )

    async def test_a_429_on_the_token_endpoint_still_latches(self, credentials, transport):
        transport(_response(429, {"message": "Rate Limit Exceeded"}, url=TOKEN_URL, method="POST"))

        with pytest.raises(StravaRateLimited) as caught:
            await strava.refresh(pair())

        assert 0 < caught.value.retry_after_s <= WINDOW_SECONDS
        assert strava.is_paced() is True, (
            "a 429 anywhere means the app's window is spent, so it latches wherever it\n"
            "  arrives — the token path simply is not GATED by the latch afterwards."
        )

    async def test_a_mid_walk_429_costs_the_window_rather_than_shortening_it_silently(
        self, transport
    ):
        stub = transport(
            _response(200, [activity_body(1)]),
            _response(200, [activity_body(2)]),
            _response(429, {"message": "Rate Limit Exceeded"}),
        )

        result = await strava.fetch_activities(pair(), max_pages=4)

        assert result.outcome is ToolOutcome.RATE_LIMITED, (
            "two pages already in hand were handed back as if the walk had finished. A\n"
            "  partial history presented as complete is the empty-list bug wearing a\n"
            "  plausible number of activities."
        )
        assert result.data is None
        assert len(stub.calls) == 3

    async def test_an_empty_history_is_ok_and_that_is_the_only_thing_a_429_may_not_look_like(
        self, transport
    ):
        transport(_response(200, []))

        result = await strava.fetch_activities(pair())

        assert result.outcome is ToolOutcome.OK
        assert isinstance(result.data, ActivityWindow)
        assert result.data.count == 0
        assert result.data.truncated is False, (
            "an athlete with no activities in the window is an ANSWER about training. It is\n"
            "  the one shape a refusal must never be allowed to take, which is why every\n"
            "  refusal above is a typed outcome carrying detail."
        )


class TestTheRateLimitHeadersAreTwoFamiliesOfTwoNumbers:
    """`X-RateLimit-*` is the overall allowance (200/2 000) and `X-ReadRateLimit-*` the
    binding one here (100/1 000), because Huella only ever reads. Each header is a
    comma-separated `15min,daily` pair, and a malformed one must not raise into a caller:
    losing the counters costs a throttle notice, raising costs the read."""

    def test_both_families_are_read_when_both_arrive(self):
        limits = strava.parse_rate_limits(
            httpx.Headers(
                {
                    "X-RateLimit-Limit": "200,2000",
                    "X-RateLimit-Usage": "17,412",
                    "X-ReadRateLimit-Limit": "100,1000",
                    "X-ReadRateLimit-Usage": "3,41",
                }
            )
        )
        assert limits.overall == RateLimit(
            fifteen_min_limit=200, fifteen_min_usage=17, daily_limit=2000, daily_usage=412
        )
        assert limits.read == RateLimit(
            fifteen_min_limit=100, fifteen_min_usage=3, daily_limit=1000, daily_usage=41
        )
        assert limits.exhausted is False

    def test_one_family_alone_is_read_and_the_other_stays_absent(self):
        overall_only = strava.parse_rate_limits(
            httpx.Headers({"X-RateLimit-Limit": "200,2000", "X-RateLimit-Usage": "17,412"})
        )
        assert overall_only.overall is not None and overall_only.read is None

        read_only = strava.parse_rate_limits(httpx.Headers(_read_headers()))
        assert read_only.read is not None and read_only.overall is None, (
            "the two prefixes must not be confused: `X-ReadRateLimit-Limit` also ends in\n"
            "  `RateLimit-Limit`, so a substring match reads the read family as the overall."
        )

    def test_no_headers_at_all_is_an_absence_and_not_a_failure(self):
        empty = strava.parse_rate_limits(httpx.Headers({}))
        assert (empty.overall, empty.read, empty.exhausted) == (None, None, False), (
            "the token endpoint sends these headers not at all, so an absence has to parse\n"
            "  to 'unknown' rather than to zero — zero limits read as exhausted."
        )

    @pytest.mark.parametrize(
        "limit,usage",
        [
            ("one hundred,1000", "3,41"),
            ("100,1000", "three,41"),
            ("100", "3,41"),
            ("100,1000", "3"),
            ("", "3,41"),
            ("100,1000,17", "3,41"),
            ("-1,1000", "3,41"),
            ("100.0,1000", "3,41"),
        ],
        ids=[
            "limit-not-an-integer",
            "usage-not-an-integer",
            "limit-missing-the-comma",
            "usage-missing-the-comma",
            "limit-empty",
            "three-values",
            "negative",
            "decimal",
        ],
    )
    def test_a_malformed_pair_is_dropped_and_never_raised_into_a_caller(self, limit, usage):
        limits = strava.parse_rate_limits(
            httpx.Headers({"X-ReadRateLimit-Limit": limit, "X-ReadRateLimit-Usage": usage})
        )
        assert limits.read is None, (
            f"({limit!r}, {usage!r}) parsed to {limits.read!r}. A header we cannot read costs\n"
            "  a throttle notice in the UI; a header that RAISES costs the read itself, and\n"
            "  these numbers are documented as 'integer, integer' rather than guaranteed."
        )
        assert limits.exhausted is False

    async def test_a_malformed_header_leaves_a_read_completely_unharmed(self, transport):
        transport(
            _response(
                200,
                [activity_body()],
                **{"X-ReadRateLimit-Limit": "not,numbers", "X-ReadRateLimit-Usage": "3,41"},
            )
        )

        result = await strava.fetch_activities(pair(), max_pages=1)

        assert result.outcome is ToolOutcome.OK
        assert result.data.count == 1

    async def test_the_last_response_counters_are_readable_afterwards(self, transport):
        transport(_response(200, [activity_body()], **_read_headers(usage="7,88")))

        await strava.fetch_activities(pair(), max_pages=1)

        read = strava.rate_limits().read
        assert read is not None
        assert (read.fifteen_min_usage, read.fifteen_min_limit) == (7, 100), (
            "`rate_limits()` is what the UI's throttle notice reads: a ToolResult has nowhere\n"
            "  to carry it and the credential path must not grow a data field."
        )

    @pytest.mark.parametrize(
        "read_usage,expected",
        [("99,999", False), ("100,999", True), ("99,1000", True), ("101,1001", True)],
        ids=["inside-both", "fifteen-minute-spent", "daily-spent", "over-both"],
    )
    def test_either_counter_reaching_its_limit_is_an_exhausted_window(self, read_usage, expected):
        fifteen, daily = read_usage.split(",")
        limits = strava.parse_rate_limits(
            httpx.Headers(_read_headers(usage=f"{fifteen},{daily}"))
        )
        assert limits.exhausted is expected
        assert limits.read.exhausted is expected


class TestSportTypeStaysAString:
    """The live enum is 56 values wide and grows by design — the changelog added six on
    30 Apr 2026. A Literal or an Enum here would hand the model invented categories and
    reject real activities the week Strava adds the next one."""

    async def test_a_sport_nobody_has_heard_of_parses_and_is_preserved_verbatim(
        self, transport
    ):
        transport(_response(200, [activity_body(sport_type=UNHEARD_OF_SPORT)]))

        result = await strava.fetch_activities(pair(), max_pages=1)

        assert result.outcome is ToolOutcome.OK, (
            f"{UNHEARD_OF_SPORT!r} cost the whole window. Strava adds sport types on its own\n"
            f"  schedule and {FACTS} names six added on 30 Apr 2026 alone — an unknown value\n"
            "  has to parse or the next release is a self-inflicted outage."
        )
        (activity,) = result.data.activities
        assert activity.sport_type == UNHEARD_OF_SPORT, (
            f"the sport type was rewritten to {activity.sport_type!r}. It is carried verbatim:\n"
            "  normalising it here is where invented categories come from."
        )

    def test_nothing_constrains_the_field_to_a_known_set(self):
        field = Activity.model_fields["sport_type"]
        assert field.annotation is str, (
            f"sport_type is annotated {field.annotation!r}. It must be a plain `str`."
        )
        schema = Activity.model_json_schema()["properties"]["sport_type"]
        assert "enum" not in schema and "const" not in schema, (
            f"sport_type gained a closed value set ({schema}). {FACTS}: parse it as `str`; an\n"
            "  enum here is a self-inflicted outage on Strava's next release."
        )

    def test_models_py_imports_no_enum_and_no_literal_at_all(self):
        tree = ast.parse(MODELS.read_text())
        names = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            f"{node.module}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        forbidden = {n for n in names if n.split(".")[-1] in ("Enum", "Literal", "StrEnum")}
        assert not forbidden, (
            f"models.py imported {sorted(forbidden)}. Nothing on this wire has a closed value\n"
            f"  set worth pinning — {FACTS} says the sport list grows by design, and 40+ of\n"
            "  its values are already documented. If a genuine enum arrives, say so in\n"
            "  AGENTS.md and update this test in the same commit."
        )

    @pytest.mark.parametrize(
        "sport", ["Run", "TrailRun", "Trail Run", "TrackRun", "AlpineSki", "Padel", "Volleyball"]
    )
    async def test_the_real_values_including_the_2026_additions_all_parse(self, transport, sport):
        transport(_response(200, [activity_body(sport_type=sport)]))
        result = await strava.fetch_activities(pair(), max_pages=1)
        assert result.data.activities[0].sport_type == sport

    def test_a_null_sport_type_becomes_empty_rather_than_costing_the_activity(self):
        assert Activity.model_validate(activity_body(sport_type=None)).sport_type == ""


class TestTheParseIsStrictButForgiving:
    """Forgiving about fields it does not read, strict about the ones it does. Strava's own
    documented `SummaryActivity` example carries three fields absent from the schema it is
    an example of, so `extra="forbid"` here is a self-inflicted outage — but a declared
    field arriving as the wrong type is a shape change, and it costs the window with a
    detail rather than quietly costing one activity."""

    async def test_fields_the_model_does_not_declare_are_ignored_and_not_fatal(
        self, transport
    ):
        # The three `models.py` names as present in Strava's own documented example and
        # absent from its schema, plus the ones it deliberately never declares.
        extras = {
            "has_heartrate": True,
            "average_heartrate": 152.4,
            "suffer_score": 88,
            "average_watts": 214.0,
            "kudos_count": 12,
            "map": {"id": "a14820001", "summary_polyline": "_p~iF~ps|U"},
        }
        transport(_response(200, [activity_body(**extras)]))

        result = await strava.fetch_activities(pair(), max_pages=1)

        assert result.outcome is ToolOutcome.OK
        (activity,) = result.data.activities
        assert activity.activity_id == 14_820_001
        for undeclared in extras:
            assert not hasattr(activity, undeclared), (
                f"{undeclared!r} reached the parsed activity. Heart rate and power are what\n"
                "  turn a training window into a medical record: a field that is never\n"
                "  parsed cannot leak, and that is the whole mechanism."
            )

    async def test_a_declared_field_of_the_wrong_type_costs_the_window_with_a_detail(
        self, transport
    ):
        transport(_response(200, [activity_body(1), activity_body(2, distance="veintiún kilómetros")]))

        result = await strava.fetch_activities(pair(), max_pages=1)

        assert result.outcome is ToolOutcome.UPSTREAM_ERROR, (
            f"a page with one unreadable activity came back {result.outcome.name}. Every field\n"
            "  declared IS validated: dropping the bad one would hand back a history that is\n"
            "  quietly shorter than the athlete's, with nothing saying so."
        )
        assert result.data is None
        assert "distance" in result.detail
        assert "página 1" in result.detail
        unparsable = [e for e in trace.events("error") if e.event == "strava.unparsable"]
        assert len(unparsable) == 1 and unparsable[0].payload["page"] == 1

    async def test_an_explicit_null_in_a_string_field_is_not_a_failure(self, transport):
        """Strava's own documented example sends `"country": null`, and a `str` field
        handed an explicit null is a hard validation error — which would cost the read."""
        transport(_response(200, athlete_body(country=None, city=None, state=None)))

        result = await strava.fetch_athlete(pair())

        assert result.outcome is ToolOutcome.OK
        assert (result.data.country, result.data.city, result.data.state) == ("", "", "")

    async def test_an_athlete_body_that_is_not_an_object_is_refused_not_emptied(
        self, transport
    ):
        transport(_response(200, ["ana"]))

        result = await strava.fetch_athlete(pair())

        assert result.outcome is ToolOutcome.UPSTREAM_ERROR
        assert "expected an object" in result.detail

    async def test_an_activities_body_that_is_not_an_array_is_refused_not_emptied(
        self, transport
    ):
        transport(_response(200, {"activities": []}))

        result = await strava.fetch_activities(pair())

        assert result.outcome is ToolOutcome.UPSTREAM_ERROR
        assert "expected an array" in result.detail

    async def test_a_two_hundred_that_is_not_json_is_refused(self, transport):
        transport(_response(200))

        result = await strava.fetch_athlete(pair())

        assert result.outcome is ToolOutcome.UPSTREAM_ERROR
        assert "not JSON" in result.detail

    def test_the_athlete_name_is_bounded_before_it_can_reach_a_prompt(self):
        parsed = Athlete.model_validate(athlete_body(firstname="Ana" * 500))
        assert len(parsed.firstname) == 1500, "Athlete names are `_Text`, not `_Prose`"
        activity = Activity.model_validate(activity_body(name="x" * 5_000))
        assert len(activity.name) == 200, (
            "the activity name is the one field here a person can write a prompt into.\n"
            "  Bounding it is this file's job; sanitising it is the agent layer's."
        )

    def test_start_date_local_stays_a_string_because_its_z_is_not_earned(self):
        activity = Activity.model_validate(activity_body())
        assert activity.start_date_local == "2026-07-20T06:15:09Z", (
            "start_date_local became a datetime. It is local wall time wearing a `Z` it has\n"
            "  not earned — the swagger's own example shows 12:15:09Z arriving as 05:15:09Z\n"
            "  for a GMT-08:00 athlete. As a datetime it is silently wrong by the offset."
        )
        assert activity.start_date is not None and activity.start_date.tzinfo is not None


class TestTheWireNamesAreNotTheModelNames:
    """A silent alias change is a silently-zeroed distance: every aliased field has a
    default, so the model still parses and every figure derived from it is wrong."""

    @pytest.mark.parametrize(
        "wire,attribute,value",
        [
            ("id", "activity_id", 14_820_001),
            ("distance", "distance_m", 21_097.5),
            ("moving_time", "moving_time_s", 6_240),
            ("elapsed_time", "elapsed_time_s", 6_480),
            ("total_elevation_gain", "total_elevation_gain_m", 312.0),
        ],
    )
    def test_each_aliased_field_maps_from_the_name_strava_actually_sends(
        self, wire, attribute, value
    ):
        parsed = Activity.model_validate(activity_body())
        assert getattr(parsed, attribute) == value
        assert Activity.model_fields[attribute].alias == wire, (
            f"`{attribute}` no longer reads the wire's `{wire}`. Every aliased field here has\n"
            "  a default, so a renamed alias does not fail — it parses to 0 and every pace,\n"
            "  distance and elevation figure downstream is quietly wrong."
        )

    def test_the_athlete_id_comes_from_the_wires_bare_id(self):
        assert Athlete.model_fields["athlete_id"].alias == "id"
        assert Athlete.model_validate(athlete_body()).athlete_id == 98_765

    def test_dropping_an_aliased_field_zeroes_it_rather_than_refusing(self):
        """Which is exactly why the alias is pinned above rather than trusted."""
        bare = {k: v for k, v in activity_body().items() if k != "distance"}
        assert Activity.model_validate(bare).distance_m == 0.0

    def test_the_model_side_name_is_accepted_too_so_a_round_trip_survives(self):
        once = Activity.model_validate(activity_body())
        assert Activity.model_validate(once.model_dump()) == once, (
            "`populate_by_name` is what lets a parsed activity be re-validated from its own\n"
            "  dump — a cached window has to survive a restart."
        )


class TestAWindowSaysWhatItCoveredAndNotWhatThereIs:
    """`truncated` is the load-bearing half: it is what lets the uncertainty layer say
    "this is what we saw" rather than "this is what there is"."""

    async def test_a_capped_page_walk_says_it_was_truncated(self, transport):
        transport(
            _response(200, [activity_body(1)]),
            _response(200, [activity_body(2)]),
        )

        result = await strava.fetch_activities(pair(), max_pages=2)

        assert result.data.pages == 2
        assert result.data.truncated is True, (
            "a walk that used every page it was allowed reported truncated=False. The\n"
            "  uncertainty layer reads this field to decide whether it may say 'this is what\n"
            "  there is'; a False here turns a partial history into a claim about all of it."
        )
        assert result.data.count == 2

    async def test_an_exhausted_page_walk_says_it_was_not(self, transport):
        transport(
            _response(200, [activity_body(1)]),
            _response(200, []),
        )

        result = await strava.fetch_activities(pair(), max_pages=4)

        assert result.data.truncated is False, (
            "an exhausted walk claimed to be truncated. Then every window is uncertain and\n"
            "  the flag stops carrying information."
        )
        assert (result.data.pages, result.data.count) == (2, 1)

    async def test_the_walk_stops_on_an_empty_page_and_not_on_a_short_one(self, transport):
        stub = transport(
            _response(200, [activity_body(1)]),
            _response(200, [activity_body(2), activity_body(3)]),
            _response(200, []),
            _response(200, [activity_body(4)]),
        )

        result = await strava.fetch_activities(pair(), per_page=30, max_pages=4)

        assert [call[2]["params"]["page"] for call in stub.calls] == [1, 2, 3], (
            "the walk stopped on a SHORT page. The swagger states no ceiling on `per_page`,\n"
            "  so 30 is what is asked for — and a server-side cap below what we asked for\n"
            "  would make 'short' mean 'done' and truncate a history silently. Only an EMPTY\n"
            "  page means done."
        )
        assert result.data.count == 3
        assert result.data.truncated is False

    async def test_the_window_carries_the_bounds_it_was_asked_for(self, transport):
        stub = transport(_response(200, []))
        after, before = 1_782_000_000, 1_784_000_000

        result = await strava.fetch_activities(pair(), after=after, before=before, max_pages=1)

        assert (result.data.after, result.data.before) == (after, before)
        params = stub.calls[0][2]["params"]
        assert (params["after"], params["before"]) == (after, before), (
            "`after`/`before` are Strava's own epoch-second parameters. There is no clock in\n"
            "  fetch_activities deciding what 'recent' means, and there must not be."
        )
        assert params["per_page"] == 30

    async def test_bounds_are_omitted_entirely_when_not_given(self, transport):
        stub = transport(_response(200, []))

        await strava.fetch_activities(pair(), max_pages=1)

        assert set(stub.calls[0][2]["params"]) == {"page", "per_page"}

    def test_an_empty_window_is_a_window_and_not_a_None(self):
        window = ActivityWindow()
        assert (window.activities, window.count, window.pages, window.truncated) == ((), 0, 0, False)

    async def test_the_default_walk_is_four_pages_of_thirty(self, transport):
        assert (strava.PER_PAGE, strava.MAX_PAGES) == (30, 4)
        stub = transport(*[_response(200, [activity_body(n)]) for n in range(1, 5)])

        result = await strava.fetch_activities(pair())

        assert [call[2]["params"]["per_page"] for call in stub.calls] == [30, 30, 30, 30]
        assert result.data.pages == 4 and result.data.truncated is True


class TestAManualEntryIsSelfReportWearingActivityClothes:
    """Huella reasons from demonstrated training. The agent layer depends on being able to
    exclude a typed-in activity, which it can only do if the flag survives parsing."""

    async def test_the_manual_flag_survives_the_round_trip(self, transport):
        transport(
            _response(200, [activity_body(1, manual=True), activity_body(2, manual=False)])
        )

        result = await strava.fetch_activities(pair(), max_pages=1)

        assert [a.manual for a in result.data.activities] == [True, False], (
            "the `manual` flag was lost. A manual entry is self-report wearing an activity's\n"
            "  clothes — without the flag it is indistinguishable from demonstrated training\n"
            "  and the agent layer has nothing to exclude on."
        )

    async def test_trainer_and_commute_survive_too(self, transport):
        transport(_response(200, [activity_body(1, trainer=True, commute=True)]))

        (activity,) = (await strava.fetch_activities(pair(), max_pages=1)).data.activities

        assert (activity.trainer, activity.commute) == (True, True)

    def test_all_three_default_to_false_rather_than_to_unknown(self):
        bare = Activity.model_validate({"id": 1})
        assert (bare.manual, bare.trainer, bare.commute) == (False, False, False)


class TestEveryRefusalIsATypedOutcomeAndNeverAnEmptyResult:
    """Six ways this can fail and six different outcomes. Collapsing any two of them is
    the bug `coros_core.outcomes` exists to make impossible."""

    @pytest.mark.parametrize(
        "status,outcome",
        [
            (400, ToolOutcome.NEEDS_HUMAN),
            (401, ToolOutcome.NEEDS_HUMAN),
            (403, ToolOutcome.NOT_ELIGIBLE),
            (404, ToolOutcome.UPSTREAM_ERROR),
            (429, ToolOutcome.RATE_LIMITED),
            (500, ToolOutcome.UPSTREAM_ERROR),
            (503, ToolOutcome.UPSTREAM_ERROR),
        ],
    )
    async def test_each_status_maps_to_its_own_outcome(self, transport, status, outcome):
        transport(_response(status, {"message": "no", "errors": [{"code": "invalid"}]}))

        result = await strava.fetch_activities(pair())

        assert result.outcome is outcome
        assert result.detail, "a non-OK result with no detail cannot be rendered to anyone"
        assert result.data is None

    async def test_a_401_says_both_things_it_cannot_tell_apart(self, transport):
        transport(_response(401, {"message": "Authorization Error"}))

        result = await strava.fetch_athlete(pair())

        assert result.outcome is ToolOutcome.NEEDS_HUMAN
        assert "reconectar" in result.detail, (
            "a 401 cannot tell an expired access token from a revoked grant, so the detail\n"
            "  has to say both: refresh first, and if that fails the athlete reconnects."
        )
        assert result.outcome.needs_escalation is True

    async def test_a_missing_scope_is_not_an_empty_history(self, transport):
        transport(_response(403, {"message": "Forbidden"}))

        result = await strava.fetch_activities(pair(scope="read"))

        assert result.outcome is ToolOutcome.NOT_ELIGIBLE, (
            "a 403 means the token is real and does not carry the scope this read needs.\n"
            "  It is not an empty history and not something a retry improves."
        )
        assert result.outcome.is_ok is False

    async def test_a_timeout_is_not_a_no(self, transport):
        transport(httpx.ReadTimeout("strava took too long"))

        result = await strava.fetch_activities(pair())

        assert result.outcome is ToolOutcome.TIMEOUT
        assert result.outcome.is_transient is True
        assert "ReadTimeout" in result.detail

    async def test_a_dropped_connection_is_an_upstream_error_and_not_a_timeout(
        self, transport
    ):
        transport(httpx.ConnectError("connection refused"))

        result = await strava.fetch_activities(pair())

        assert result.outcome is ToolOutcome.UPSTREAM_ERROR
        assert "ConnectError" in result.detail

    async def test_the_credential_path_raises_where_the_data_path_reports(
        self, credentials, transport
    ):
        """Two paths on purpose: a ToolResult reaches the trace, the evidence bundle and
        from there a model's context, and the credential path must never put a token
        anywhere near that. So it raises typed instead."""
        transport(
            _response(403, {"message": "Forbidden"}, url=TOKEN_URL, method="POST"),
            _response(500, None, url=TOKEN_URL, method="POST"),
        )

        with pytest.raises(StravaScopeDenied):
            await strava.refresh(pair())
        with pytest.raises(StravaUnavailable):
            await strava.refresh(pair())

    async def test_a_refusal_detail_carries_stravas_own_error_codes(self, transport):
        transport(
            _response(
                404,
                {
                    "message": "Record Not Found",
                    "errors": [{"resource": "Athlete", "field": "id", "code": "invalid"}],
                },
            )
        )

        result = await strava.fetch_athlete(pair())

        assert "Record Not Found" in result.detail and "invalid" in result.detail, (
            "Strava's documented envelope is {message, errors:[{code, field, resource}]} and\n"
            "  the message plus the codes are the only part of it a person can act on."
        )

    async def test_an_html_error_page_is_still_a_typed_refusal(self, transport):
        transport(_response(502))

        result = await strava.fetch_activities(pair())

        assert result.outcome is ToolOutcome.UPSTREAM_ERROR
        assert "502" in result.detail


class TestTheTransportIsOneClientAndOneDoor:
    def test_the_same_client_is_handed_out_until_it_is_closed(self):
        first = strava.client()
        assert strava.client() is first, (
            "a second client was constructed. httpx pools per client, and MAX_CONCURRENCY\n"
            "  is enforced by this one's connection limits plus SEM — two clients are two\n"
            "  pools and twice the concurrency against a limiter of 100 per 15 minutes."
        )
        assert first.timeout.read == strava.REQUEST_TIMEOUT

    async def test_closing_it_means_the_next_caller_gets_a_fresh_one(self):
        first = strava.client()
        await strava.aclose()
        assert strava.client() is not first
        await strava.aclose()

    def test_concurrency_is_capped_and_the_semaphore_agrees(self):
        assert strava.MAX_CONCURRENCY == 4
        assert strava.SEM._value <= strava.MAX_CONCURRENCY

    def test_no_other_module_spells_a_strava_url(self):
        """Prose is documentation, not a second caller — so this is an AST walk over string
        constants that are not docstrings, the lesson `test_catalog.py` records."""
        offenders: list[str] = []
        for root in (REPO / "packages", REPO / "apps", REPO / "scripts"):
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.py")):
                if path == CLIENT:
                    continue
                tree = ast.parse(path.read_text())
                prose: set[int] = set()
                holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                for node in ast.walk(tree):
                    if isinstance(node, holders) and node.body:
                        first = node.body[0]
                        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                            prose.add(id(first.value))
                offenders += [
                    f"{path.relative_to(REPO)}:{node.lineno}: {node.value!r}"
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "strava.com" in node.value
                    and id(node) not in prose
                ]
        assert not offenders, (
            "a Strava URL is spelled outside huella.strava.client:\n  "
            + "\n  ".join(offenders)
            + f"\nGo through this module. {FACTS}: the 4 Jan 2027 base-URL migration is one\n"
            "line only while the base is spelled once, and a second caller brings its own\n"
            "idea of the retry policy and its own share of a window of 100."
        )


class TestTheStravaPathLeavesATrace:
    """`evidence.py` treats a `strava.*` failure event as "this turn's retrieval is not a
    verified fact". A refusal that emits nothing is a refusal nothing downstream sees."""

    async def test_a_served_read_records_the_window_it_covered(self, transport):
        transport(_response(200, [activity_body()], **_read_headers(usage="9,120")))

        await strava.fetch_activities(pair(), max_pages=1)

        call = next(e for e in trace.events() if e.event == "strava.call")
        assert call.level == "info"
        assert call.payload["read_usage"] == 9 and call.payload["read_limit"] == 100
        summary = next(e for e in trace.events() if e.event == "strava.activities")
        assert summary.payload["count"] == 1 and summary.payload["truncated"] is True

    async def test_both_halves_of_a_rate_limit_are_recorded_as_errors(self, transport):
        transport(_response(429, {"message": "Rate Limit Exceeded"}))

        await strava.fetch_activities(pair())
        await strava.fetch_activities(pair())

        refusals = [e for e in trace.events("error") if e.event == "strava.rate_limited"]
        assert [e.payload["latched"] for e in refusals] == [False, True], (
            "the 429 and the refusal that never left the process are different facts: one\n"
            "  says Strava refused, the other that our own latch did — and only the second\n"
            "  proves a call was withheld rather than spent."
        )
        assert all(e.payload["retry_after_s"] > 0 for e in refusals)

    async def test_a_served_read_emits_no_error_at_all(self, transport):
        transport(_response(200, athlete_body()))

        await strava.fetch_athlete(pair())

        assert trace.events("error") == []


# ── live ───────────────────────────────────────────────────────────────────────
#
# One module-scoped probe, sequential and spaced. Never `asyncio.gather` here. Everything
# below runs WITHOUT a registered app — a bogus client_id is enough to establish that both
# token endpoints process OAuth — so the only thing gating it is `STRAVA_CLIENT_ID`, which
# is the repo's marker for "someone holds a subscription and has registered an app".


async def _probe() -> dict[str, Any]:
    """Four requests. `AGENTS.md` records the two token endpoints answering identically;
    this is the measurement that keeps saying so, plus the two facts a future reader is
    most likely to 'correct': that `api-v3.strava.com` is not live yet, and that
    `/oauth/revoke` exists where `/oauth/deauthorize` is being retired."""
    out: dict[str, Any] = {}
    form = {
        "client_id": "0",
        "client_secret": "0",
        "code": "0",
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as raw:

        async def post(url: str, **kwargs: Any) -> httpx.Response:
            await asyncio.sleep(SPACING)
            return await raw.post(url, **kwargs)

        oauth = await post(TOKEN_URL, data=form)
        out["oauth_token"] = (oauth.status_code, oauth.text)

        api_v3 = await post(f"{API}/oauth/token", data=form)
        out["api_v3_token"] = (api_v3.status_code, api_v3.text)

        revoke = await post(REVOKE_URL, data={"token": "0"})
        out["revoke_status"] = revoke.status_code

        await asyncio.sleep(SPACING)
        unauthorized = await raw.get(f"{API}/athlete")
        out["athlete_status"] = unauthorized.status_code
        out["athlete_body"] = unauthorized.text
        out["athlete_headers"] = dict(unauthorized.headers)

        try:
            await asyncio.sleep(SPACING)
            await raw.get("https://api-v3.strava.com/athlete")
            out["new_base_live"] = True
        except httpx.TransportError:
            out["new_base_live"] = False

    return out


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    if not strava.client_id():
        pytest.skip(
            "STRAVA_CLIENT_ID is unset, so nothing here runs. Standard Tier API access now "
            "requires an active paid Strava subscription (1 Jun 2026 for new developers, "
            "30 Jun 2026 for existing ones) AND a registered app — a blocker on a person, "
            f"not on code. {FACTS}. The offline half above covers the same facts against a "
            "stubbed transport."
        )
    return asyncio.run(_probe())


@pytest.mark.live
def test_both_token_endpoints_are_live_and_answer_identically(live):
    oauth_status, oauth_body = live["oauth_token"]
    api_status, api_body = live["api_v3_token"]

    assert oauth_status == api_status == 400, (
        f"the two token endpoints diverged: /oauth/token gave {oauth_status}, "
        f"/api/v3/oauth/token gave {api_status}. {FACTS} records both answering a bogus "
        "client_id with 400. Update AGENTS.md, client.TOKEN_URL and this test together."
    )
    assert json.loads(oauth_body) == json.loads(api_body), (
        f"the two token endpoints no longer answer identically.\n  /oauth/token: {oauth_body}\n"
        f"  /api/v3/oauth/token: {api_body}\n  {FACTS} records byte-identical 400 bodies "
        "measured 30 Jul 2026, which is the whole basis for preferring the /oauth one."
    )
    errors = json.loads(oauth_body).get("errors", [])
    assert {"resource": "Application", "field": "client_id", "code": "invalid"} in errors, (
        f"the error envelope changed to {oauth_body}. {FACTS} pins this body, and `_fault()` "
        "reads `message` plus `errors[].code` out of it."
    )


@pytest.mark.live
def test_the_api_base_url_migration_has_not_happened_yet(live):
    assert live["new_base_live"] is False, (
        f"https://api-v3.strava.com answers now. {FACTS} says the 4 Jan 2027 base-URL "
        "migration is not live yet, which is why client.API is still www.strava.com/api/v3. "
        "This is the day to change that constant — one line — and this test with it. The "
        "OAuth host does not move, so TOKEN_URL and REVOKE_URL stay."
    )


@pytest.mark.live
def test_the_revoke_endpoint_exists_where_deauthorize_is_being_retired(live):
    assert live["revoke_status"] != 404, (
        f"POST {REVOKE_URL} is a 404. {FACTS}: revoke replaced /oauth/deauthorize on "
        "1 Jun 2026 and deauthorize is retired 1 Jun 2027. If revoke really went away, "
        "there is nothing left to call — update AGENTS.md and client.py together."
    )


@pytest.mark.live
def test_an_unauthenticated_read_is_a_401_carrying_stravas_own_envelope(live):
    assert live["athlete_status"] == 401, (
        f"GET {API}/athlete without a token answers {live['athlete_status']}, not 401. "
        "`_refuse()` maps 400/401 to StravaAuthDenied and from there to NEEDS_HUMAN — a "
        "different status means a revoked grant now reads as some other outcome."
    )
    body = json.loads(live["athlete_body"])
    assert "message" in body, (
        f"the error envelope changed to {live['athlete_body']!r}. `_fault()` reads `message` "
        "and `errors[].code`; without them a refusal reaches the UI as a bare status."
    )


@pytest.mark.live
def test_a_real_response_still_carries_the_rate_limit_headers_the_parser_reads(live):
    headers = live["athlete_headers"]
    present = {k.lower() for k in headers}
    assert {"x-ratelimit-limit", "x-ratelimit-usage"} <= present, (
        f"the rate-limit headers are gone from a live response (got {sorted(present)}). "
        f"{FACTS} pins `X-RateLimit-*` and `X-ReadRateLimit-*` as comma-separated "
        "`15min,daily` pairs, and `parse_rate_limits()` plus the pre-emptive latch in "
        "`_send()` are built on them. Update AGENTS.md and this test together."
    )
    parsed = strava.parse_rate_limits(httpx.Headers(headers))
    assert parsed.overall is not None, (
        f"`X-RateLimit-Limit: {headers.get('x-ratelimit-limit')!r}` / "
        f"`-Usage: {headers.get('x-ratelimit-usage')!r}` no longer parse as two integers. "
        "The offline suite's fixtures are written to this shape."
    )
