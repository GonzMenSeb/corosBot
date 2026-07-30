"""Huella's serving contract: one config, one page, and the callback that is not a page.

Nothing here needs a browser or a Strava credential. What it pins is the handful of
`rxconfig.py` values that each cost somebody a running demo, and the shape of the OAuth
callback — a Starlette route on the `api_transformer`, whose whole correctness is that it is
registered before Reflex mounts its own router into ours.

`tests/test_contracts.py` proves the Reflex-side halves of that mechanism against Reflex's
own source. These prove Huella actually used it, and that the route behaves: a redirect with
no body, a single-use `state`, and no way for a forged callback to reach a session that did
not start the flow.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import inspect
import os
import pathlib
import subprocess
import sys
import types
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import reflex as rx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from huella import oauth, privacy

ROOT = pathlib.Path(__file__).resolve().parents[1]
FACTS = 'AGENTS.md "The OAuth callback route (Huella only)"'


def rxconfig_path(app: str) -> pathlib.Path:
    return ROOT / "apps" / app / "rxconfig.py"


def load_rxconfig(app: str) -> types.ModuleType:
    """Load `apps/<app>/rxconfig.py` under a module name that says which app it is.

    Reflex fixes both the filename and the module name it imports it as, and the suite puts
    both app directories on `sys.path` — so a plain `import rxconfig` resolves by path order
    and caches the winner in `sys.modules`, handing Brújula's config back for Huella with no
    error anywhere. Loading by file path is the only way to name which one you meant.
    """
    path = rxconfig_path(app)
    spec = importlib.util.spec_from_file_location(f"_rxconfig_{app}", path)
    assert spec is not None and spec.loader is not None, f"{path} is not loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(app: str = "huella") -> rx.Config:
    return load_rxconfig(app).config


def app_module() -> types.ModuleType:
    return importlib.import_module("huella.app")


def descendants(component: Any) -> Iterator[Any]:
    yield component
    for child in getattr(component, "children", ()) or ():
        yield from descendants(child)


def outermost_cond(component: Any) -> Any:
    queue = [component]
    while queue:
        node, queue = queue[0], queue[1:]
        if type(node).__name__ == "Cond":
            return node
        queue.extend(getattr(node, "children", ()) or ())
    raise AssertionError(
        "the root page's tree holds no Cond at all.\n"
        "The gate is a branch inside the only route: `rx.cond(State.unlocked, …)`."
    )


def asgi(app: Any) -> httpx.AsyncClient:
    """`ASGITransport` has no sync entry — a `httpx.Client` around it raises
    `AttributeError: '__enter__'` rather than anything about transports."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


@pytest.fixture(autouse=True)
def _no_state_left_behind() -> Iterator[None]:
    oauth._PENDING.clear()
    privacy.forget_all()
    yield
    oauth._PENDING.clear()
    privacy.forget_all()


class TestRxconfigCarriesTheFactsThatCostADemo:
    def test_the_app_module_import_is_explicit(self) -> None:
        assert config().app_module_import == "huella.app", (
            "app_module_import is not 'huella.app'.\n"
            "Reflex otherwise resolves the app module as app_name + '.' + app_name —\n"
            "`huella.huella` — and dies with ModuleNotFoundError before serving anything."
        )

    def test_the_app_name_is_the_package_directory(self) -> None:
        cfg = config()
        assert cfg.app_name == "huella", "app_name must match apps/huella/huella/."
        assert (ROOT / "apps" / cfg.app_name / cfg.app_name).is_dir(), (
            f"apps/{cfg.app_name}/{cfg.app_name}/ does not exist.\n"
            "Reflex reports the mismatch as a folder-structure error at boot, not as a\n"
            "config error, so it reads like the package is missing."
        )

    @pytest.mark.parametrize(("port", "value"), (("frontend_port", 3001), ("backend_port", 8001)))
    def test_the_ports_are_pinned_one_above_brujulas(self, port: str, value: int) -> None:
        assert getattr(config(), port) == value, (
            f"{port} is not pinned to {value}.\n"
            "Both apps run at once in dev and Reflex falls back to 'the next available\n"
            "port' when one is taken — so an unpinned pair silently lands Huella on\n"
            "Brújula's ports or moves it out from under whatever is proxying it.\n"
            "STRAVA_REDIRECT_URI in .env.example names :8001; it is the backend port,\n"
            "because the callback is a route on the api_transformer and not a page."
        )

    def test_the_two_apps_do_not_share_a_port(self) -> None:
        for port in ("frontend_port", "backend_port"):
            assert getattr(config("huella"), port) != getattr(config("brujula"), port), (
                f"both apps pin the same {port}. The second one to start takes 'the next\n"
                "available port' and stops being where anything expects it."
            )

    def test_vite_allowed_hosts_is_on(self) -> None:
        assert config().vite_allowed_hosts is True, (
            "vite_allowed_hosts is off.\n"
            "It defaults to False, which allows localhost only, and any other host — a\n"
            "quick tunnel, a staging domain — then gets `403 Blocked request. This host is\n"
            "not allowed.` on a completely healthy app."
        )

    def test_the_baked_api_url_is_domain_agnostic(self) -> None:
        assert config().api_url == "http://localhost:8001", (
            "api_url is not http://localhost:8001.\n"
            "It is compiled INTO the frontend bundle, and the compiled client rewrites any\n"
            "SAME_DOMAIN_HOSTNAMES host to window.location.hostname, upgrading ws:→wss: and\n"
            "clearing the port on https (.web/utils/state.js:99-121). Baking the real\n"
            "hostname in needs one image per domain and buys nothing."
        )

    def test_the_api_url_is_overridable_for_a_tunnel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HUELLA_API_URL", "https://tunnel.example/api")
        assert config().api_url == "https://tunnel.example/api", (
            "HUELLA_API_URL no longer overrides api_url.\n"
            "A dev tunnel hands out a fresh backend URL on every restart, and the browser\n"
            "reaches the backend over a WebSocket it opens itself. With no override the\n"
            "page renders perfectly and does nothing at all."
        )

    def test_the_default_credentialed_cors_wildcard_is_not_shipped(self) -> None:
        """Reflex defaults `cors_allowed_origins` to `("*",)` and `App._add_cors` pairs it with
        `allow_credentials=True`. The spike measured that default mirroring
        `Origin: https://evil.example` back ON THE OAUTH CALLBACK, credentials allowed."""
        allowed = list(config().cors_allowed_origins)
        assert "*" not in allowed and allowed, (
            f"cors_allowed_origins is {allowed!r}.\n"
            f"A wildcard here is Reflex's default, not a decision. See {FACTS}."
        )

    def test_both_spellings_of_the_dev_origin_are_allowed_by_default(self) -> None:
        """The list also gates the socket.io handshake (`reflex/app.py:540`), and engineio
        compares the Origin header as an exact string — `origin not in allowed_origins`,
        `async_server.py:229`. A browser sends whichever host was typed, so `localhost` and
        `127.0.0.1` are two different origins and shipping only one means opening the other
        renders the page and never connects."""
        assert config().cors_allowed_origins == [
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ], (
            "the default allowed origins are no longer both spellings of Huella's dev\n"
            "frontend on :3001. Dropping either spelling does not fail loudly."
        )

    def test_the_hosted_origin_replaces_the_dev_one_rather_than_joining_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A production instance that still trusted localhost would be trusting any process
        on the box, which on this VPS means every other container."""
        monkeypatch.setenv("HUELLA_ALLOWED_ORIGINS", "https://huella.web.vespiridion.org")
        assert config().cors_allowed_origins == ["https://huella.web.vespiridion.org"], (
            "HUELLA_ALLOWED_ORIGINS no longer replaces the default. If it appends instead,\n"
            "the hosted app trusts http://localhost:3001 as well."
        )

    def test_a_comma_separated_list_is_accepted_and_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HUELLA_ALLOWED_ORIGINS", "https://a.example , https://b.example ,")
        assert config().cors_allowed_origins == ["https://a.example", "https://b.example"], (
            "the origin list no longer splits on commas and trims. An untrimmed\n"
            "' https://b.example' silently matches no origin at all."
        )

    def test_no_third_party_badge_is_injected_into_the_page(self) -> None:
        assert config().show_built_with_reflex is False, (
            "show_built_with_reflex is not explicitly False, so Reflex will inject its own\n"
            "sticky badge into a client-facing page. None is not off — the compiler turns\n"
            "None into True unless the deploy is on a paid tier."
        )

    def test_the_app_ships_a_favicon(self) -> None:
        """A browser requests /favicon.ico unprompted on every page load, and Reflex serves
        `assets/` at the web root, so an absent file is a 404 in the console of a demo."""
        icon = rxconfig_path("huella").parent / "assets" / "favicon.ico"
        assert icon.is_file(), f"{icon} is missing; every page load logs a 404 for it"
        assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00", (
            "assets/favicon.ico is not an ICO. Browsers sniff the bytes, and the extension "
            "alone will not make a PNG or an SVG work at this path."
        )

    def test_the_two_apps_do_not_ship_the_same_mark(self) -> None:
        """Two demos on one VPS that share an icon read as one demo with two front doors."""
        assert (
            (ROOT / "apps" / "huella" / "assets" / "favicon.ico").read_bytes()
            != (ROOT / "apps" / "brujula" / "assets" / "favicon.ico").read_bytes()
        ), "Huella is shipping Brújula's compass. Its mark is the trace, not a bearing."

    def test_rxconfig_needs_nothing_on_sys_path_but_its_own_directory(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run(
            [
                sys.executable,
                "-c",
                "from reflex.config import get_config;print(get_config().app_module_import)",
            ],
            cwd=ROOT / "apps" / "huella",
            env=env,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0 and done.stdout.strip() == "huella.app", (
            "`reflex.config.get_config()` could not read apps/huella/rxconfig.py from that\n"
            f"directory with a bare environment.\nstdout: {done.stdout}\nstderr: {done.stderr}\n"
            "get_config() reduces sys.path to the current directory to import the module and\n"
            "only retries with the ambient path if that raises. So an `import coros_core` —\n"
            "or an `import huella.ui.theme` — resolves when the caller happens to export the\n"
            "right PYTHONPATH and raises on a bare `reflex run` here. Keep it to reflex and\n"
            "the stdlib."
        )

    def test_the_theme_is_configured_on_the_app_not_in_the_config(self) -> None:
        cfg = config()
        assert not any(
            type(plugin).__name__ == "RadixThemesPlugin" for plugin in (cfg.plugins or [])
        ), (
            "the Radix theme moved into rxconfig.py as a RadixThemesPlugin.\n"
            "`rx.App(theme=...)` warns that it is deprecated in favour of exactly that, and\n"
            "on reflex 0.9.7 it still works. Taking the warning's advice puts the theme\n"
            "somewhere that cannot read huella/ui/theme.py's tokens: rxconfig.py is imported\n"
            "with sys.path reduced to its own directory."
        )


class TestHuellaServesOnePageAndPutsTheGateInsideIt:
    @staticmethod
    def pages() -> dict[str, Any]:
        return dict(app_module().app._unevaluated_pages)

    def test_the_module_reflex_is_pointed_at_exposes_an_app(self) -> None:
        assert isinstance(app_module().app, rx.App), (
            "huella/app.py has no module-level `app` that is an rx.App.\n"
            "Reflex resolves app_module_import and then requires that attribute."
        )

    def test_exactly_one_page_is_registered(self) -> None:
        pages = self.pages()
        assert list(pages) == ["index"], (
            f"routes registered: {sorted(pages)}\n"
            "Huella serves one page. A route for the gate — or for anything the gate\n"
            "protects — is a URL that renders without the password check ever running.\n"
            "The OAuth callback is NOT a page: it is a Starlette route on the transformer."
        )

    def test_the_only_page_runs_on_page_load(self) -> None:
        page = self.pages()["index"]
        declared = page.on_load if isinstance(page.on_load, (list, tuple)) else [page.on_load]
        names = [
            getattr(getattr(handler, "fn", handler), "__qualname__", str(handler))
            for handler in declared
            if handler is not None
        ]
        assert "State.on_page_load" in names, (
            f"the root page's on_load is {page.on_load!r}.\n"
            "It opens the gate when HUELLA_PASSWORD is unset AND it is the only thing that\n"
            "reads the callback's `?strava=` answer — the browser came back from Strava\n"
            "through a redirect this state machine never saw."
        )

    def test_the_gate_is_a_conditional_render_not_a_second_route(self) -> None:
        cond = outermost_cond(app_module().index())
        assert "unlocked" in str(cond.cond), (
            f"the root page branches on {cond.cond!s}, not on State.unlocked."
        )

    def test_both_branches_of_the_gate_are_built(self) -> None:
        branches = outermost_cond(app_module().index()).children
        assert len(branches) == 2, f"the gate has {len(branches)} branches, expected 2."
        for label, branch in zip(("unlocked", "locked"), branches, strict=True):
            assert list(descendants(branch))[1:], (
                f"the {label} branch of the gate renders nothing.\n"
                "A one-armed rx.cond renders an empty fragment, so the locked page comes up\n"
                "blank instead of asking for the password."
            )


class TestTheCallbackIsARouteOnTheTransformer:
    def test_the_transformer_carries_the_callback_route(self) -> None:
        paths = [getattr(route, "path", "") for route in app_module().api.routes]
        assert oauth.ROUTE in paths, (
            f"the api_transformer's routes are {paths}.\n"
            "Reflex mounts its own router — including, in prod, the compiled frontend's\n"
            "catch-all — into this Starlette. A callback that is not on it gets a 404 from\n"
            f"that mount, and Strava is handed the 404. See {FACTS}."
        )

    def test_the_app_was_handed_that_transformer(self) -> None:
        module = app_module()
        assert module.app.api_transformer is module.api, (
            "rx.App was constructed without api_transformer=api, so the route above is on a\n"
            "Starlette nothing serves. The symptom is a 404 on a route that plainly exists."
        )

    def test_the_routes_are_registered_before_rx_app_is_constructed(self) -> None:
        """Both orders work — the mount happens inside `App.__call__`, which granian invokes
        per worker boot — and the spike proved the late one live. Registering first is the
        order that stays correct if Reflex ever moves the mount into the constructor."""
        source = ast.parse(pathlib.Path(app_module().__file__).read_text())
        transformer = [
            node.lineno
            for node in ast.walk(source)
            if isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") == "api" for t in node.targets)
        ]
        constructed = [
            node.lineno
            for node in ast.walk(source)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "App"
        ]
        assert transformer and constructed, "huella/app.py builds no api_transformer"
        assert min(transformer) < min(constructed), (
            "the api_transformer is built after rx.App(...). That still works today, but a\n"
            "route appended after a Mount('') on the SAME router is unreachable and the\n"
            f"failure is silent — see {FACTS}."
        )

    def test_the_transformer_declares_no_lifespan(self) -> None:
        """`App.__call__` builds `Starlette(lifespan=self._run_lifespan_tasks)` and mounts
        everything else inside it, and a mounted ASGI app is never sent a lifespan scope —
        the spike's marker file was never written. A `lifespan=` here is dead code that
        fails silently."""
        module = app_module()
        source = ast.parse(pathlib.Path(module.__file__).read_text())
        offenders = [
            node.lineno
            for node in ast.walk(source)
            if isinstance(node, ast.Call)
            and any(kw.arg == "lifespan" for kw in node.keywords)
        ]
        assert not offenders, (
            f"a lifespan= is declared at line(s) {offenders}. It never runs. Startup work\n"
            f"goes through app.register_lifespan_task, which does. See {FACTS}."
        )

    def test_the_sweeper_goes_through_the_hook_that_does_fire(self) -> None:
        module = app_module()
        registered = [
            getattr(task, "__name__", "") for task in module.app.get_lifespan_tasks()
        ]
        assert "sweeper" in registered, (
            f"registered lifespan tasks are {registered}.\n"
            "privacy.sweep() is driven by traffic, so a process nobody is using holds an\n"
            "idle athlete's refresh token past SESSION_TTL_SECONDS with nothing to drop it."
        )

    async def test_the_callback_outranks_a_catch_all_mounted_after_it(self) -> None:
        """The production shape, without granian: Reflex's whole router — the compiled
        frontend's catch-all included — becomes one `Mount("")` appended after ours."""
        module = app_module()
        outer = Starlette(routes=list(module.api.routes))
        outer.routes.append(
            Mount("", Starlette(routes=[Route("/{path:path}", lambda _r: PlainTextResponse("frontend"))]))
        )

        async with asgi(outer) as client:
            answer = await client.get(f"{oauth.ROUTE}?code=x&state=y", follow_redirects=False)

        assert answer.status_code == 303, (
            f"the frontend mount answered instead ({answer.status_code} "
            f"{answer.text[:60]!r}). Starlette matches in list order and ours is earlier."
        )

    async def test_a_post_is_405_because_a_swallowed_path_would_be_404(self) -> None:
        async with asgi(app_module().api) as client:
            answer = await client.post(oauth.ROUTE)
        assert answer.status_code == 405, (
            f"POST {oauth.ROUTE} answered {answer.status_code}. 405 is what proves the path\n"
            "is ours: a swallowed one would 404."
        )

    async def test_the_prefix_is_not_a_wildcard(self) -> None:
        async with asgi(app_module().api) as client:
            answer = await client.get("/oauth/strava/nope")
        assert answer.status_code == 404


class TestTheCallbackAnswersWithARedirectAndNothingElse:
    async def test_every_outcome_is_a_redirect_with_an_empty_body(self) -> None:
        """CORS is a browser policy and `rxconfig.py`'s narrowing is defence in depth, not
        the guarantee. The guarantee is that there is nothing to read."""
        module = app_module()
        async with asgi(module.api) as client:
            for query in ("", "?code=x", "?state=nope", "?code=x&state=nope", "?error=access_denied"):
                answer = await client.get(f"{oauth.ROUTE}{query}", follow_redirects=False)
                assert answer.status_code == 303, f"{query!r} answered {answer.status_code}"
                assert answer.content == b"", (
                    f"{query!r} came back with a body: {answer.content[:120]!r}.\n"
                    "The code is exchanged server-side and the answer carries nothing worth\n"
                    f"stealing — see {FACTS}."
                )
                assert answer.headers["location"].startswith(f"{oauth.LANDING}?strava=")

    async def test_the_answer_never_carries_the_code_or_the_state_back(self) -> None:
        module = app_module()
        async with asgi(module.api) as client:
            answer = await client.get(
                f"{oauth.ROUTE}?code=super-secret-code&state=super-secret-state",
                follow_redirects=False,
            )
        blob = answer.headers["location"] + answer.text
        assert "super-secret-code" not in blob and "super-secret-state" not in blob, (
            "the callback reflected its own query string into the response. That URL ends\n"
            "up in a browser's history and its Referer."
        )

    def test_every_word_the_route_can_answer_with_has_spanish_on_the_page(self) -> None:
        from huella import state as st

        assert set(st._LANDING_ES) == oauth.OUTCOMES, (
            "a landing outcome the route can redirect with has no sentence in state.py, so\n"
            "the browser comes back to silence about what just happened."
        )


class TestTheStateParameterIsTheOnlyThingTyingACallbackToASession:
    """The callback arrives as a plain HTTP request with no Reflex session, so the
    association is made at the START of the flow, by the one place that has the client
    token. `privacy.issue_state` / `consume_state` are the CSRF check; `oauth._PENDING` is
    the routing table and holds two opaque identifiers and no credential."""

    def test_beginning_a_flow_ties_the_issued_state_to_that_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STRAVA_CLIENT_ID", "424242")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shh")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")

        url = oauth.begin("token-a")
        state = next(iter(oauth._PENDING))

        assert oauth._PENDING[state][0] == "token-a"
        assert f"state={state}" in url and "response_type=code" in url
        assert len(state) >= 32, (
            "the state is short enough to guess. secrets.token_urlsafe(24) is 192 bits and\n"
            "it is the only thing standing between a forged callback and a session."
        )

    def test_the_state_never_carries_the_client_token_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It travels to Strava and back through a URL bar, a history entry and a Referer.
        A Reflex client token in there is a session anybody who reads a log can resume."""
        monkeypatch.setenv("STRAVA_CLIENT_ID", "424242")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shh")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")

        url = oauth.begin("a-real-looking-client-token")
        assert "a-real-looking-client-token" not in url

    async def test_a_forged_state_reaches_no_session_at_all(self) -> None:
        exchanged: list[str] = []

        async def _never(key: str, code: str, **_: Any) -> None:
            exchanged.append(code)

        module = app_module()
        original, privacy.connect = privacy.connect, _never
        try:
            async with asgi(module.api) as client:
                answer = await client.get(
                    f"{oauth.ROUTE}?code=stolen&state=guessed", follow_redirects=False
                )
        finally:
            privacy.connect = original

        assert answer.headers["location"].endswith(oauth.BAD_STATE)
        assert exchanged == [], (
            "a callback carrying a state nobody issued still reached the token exchange.\n"
            "That is the login-CSRF this parameter exists to stop."
        )

    async def test_a_replayed_callback_is_refused_the_second_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STRAVA_CLIENT_ID", "424242")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shh")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")
        oauth.begin("token-a")
        state = next(iter(oauth._PENDING))

        exchanged: list[str] = []

        async def _connect(key: str, code: str, **_: Any) -> None:
            exchanged.append(key)

        module = app_module()
        original, privacy.connect = privacy.connect, _connect
        try:
            async with asgi(module.api) as client:
                first = await client.get(
                    f"{oauth.ROUTE}?code=abc&state={state}", follow_redirects=False
                )
                second = await client.get(
                    f"{oauth.ROUTE}?code=abc&state={state}", follow_redirects=False
                )
        finally:
            privacy.connect = original

        assert first.headers["location"].endswith(oauth.OK)
        assert second.headers["location"].endswith(oauth.BAD_STATE)
        assert exchanged == ["token-a"], (
            "the replay reached the exchange a second time. `_PENDING.pop` and\n"
            "`privacy.consume_state` are each single-use and both have to be."
        )

    async def test_a_denial_still_spends_the_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`error=access_denied` is the athlete pressing Cancel. It arrives with a real
        state, and leaving that redeemable is a handle somebody else can present later."""
        monkeypatch.setenv("STRAVA_CLIENT_ID", "424242")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shh")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")
        oauth.begin("token-a")
        state = next(iter(oauth._PENDING))

        async with asgi(app_module().api) as client:
            denied = await client.get(
                f"{oauth.ROUTE}?error=access_denied&state={state}", follow_redirects=False
            )
            again = await client.get(
                f"{oauth.ROUTE}?code=abc&state={state}", follow_redirects=False
            )

        assert denied.headers["location"].endswith(oauth.DENIED)
        assert again.headers["location"].endswith(oauth.BAD_STATE)

    def test_a_stale_handle_is_dropped_rather_than_kept_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STRAVA_CLIENT_ID", "424242")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shh")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")
        oauth.begin("token-a", now=0.0)

        assert oauth.pending_count() == 1
        oauth.sweep(now=oauth.PENDING_TTL_SECONDS + 1)
        assert oauth.pending_count() == 0, (
            "handles nobody redeemed accumulate for the life of the process. A public URL\n"
            "is served for weeks."
        )

    def test_the_pending_map_holds_no_credential_and_no_payload(self) -> None:
        """It is a routing table. If it ever grows a third field, this is the test that
        says so before a token ends up in it."""
        source = pathlib.Path(inspect.getfile(oauth)).read_text()
        tree = ast.parse(source)
        annotated = [
            ast.unparse(node.annotation)
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == "_PENDING"
        ]
        assert annotated == ["OrderedDict[str, tuple[str, float]]"], (
            f"_PENDING is now {annotated}. It maps an opaque state to an opaque client\n"
            "token and a timestamp; anything else in there is something privacy.py owns."
        )

    async def test_an_unconfigured_instance_refuses_to_start_a_flow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
        monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("STRAVA_REDIRECT_URI", raising=False)

        assert oauth.is_configured() is False
        with pytest.raises(oauth.Unconfigured):
            oauth.begin("token-a")

    async def test_a_refusal_from_strava_never_reads_as_connected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """"We could not finish talking to Strava" and "you said no" are different
        sentences, and the redirect keeps them apart."""
        from huella.strava import client

        monkeypatch.setenv("STRAVA_CLIENT_ID", "424242")
        monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shh")
        monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost:8001/oauth/strava/callback")

        cases = (
            (client.StravaRateLimited("throttled", 60.0), oauth.RATE_LIMITED),
            (client.StravaAuthDenied("code already used", 400), oauth.EXPIRED),
            (client.StravaUnavailable("502", 502), oauth.UNREACHABLE),
            (client.StravaUnconfigured("no id"), oauth.UNCONFIGURED),
        )
        module = app_module()
        original = privacy.connect
        try:
            for error, expected in cases:
                oauth.begin("token-a")
                state = next(iter(oauth._PENDING))

                async def _boom(key: str, code: str, _error=error, **_: Any) -> None:
                    raise _error

                privacy.connect = _boom
                async with asgi(module.api) as client_:
                    answer = await client_.get(
                        f"{oauth.ROUTE}?code=abc&state={state}", follow_redirects=False
                    )
                assert answer.headers["location"].endswith(expected), (
                    f"{type(error).__name__} landed on "
                    f"{answer.headers['location']!r}, expected {expected}"
                )
                assert answer.content == b""
        finally:
            privacy.connect = original


class TestTheSweeperDropsWhatNobodyCameBackFor:
    async def test_it_sweeps_both_maps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = app_module()
        swept: list[str] = []
        monkeypatch.setattr(module.privacy, "sweep", lambda: swept.append("privacy"))
        monkeypatch.setattr(module.oauth, "sweep", lambda: swept.append("oauth"))
        monkeypatch.setattr(module, "SWEEP_INTERVAL_SECONDS", 0.01)

        task = asyncio.create_task(module.sweeper())
        await asyncio.sleep(0.05)
        task.cancel()
        await task

        assert "privacy" in swept and "oauth" in swept
        assert task.cancelled() is False, (
            "the sweeper propagated the cancellation Reflex sends at shutdown. Reflex's\n"
            "done-callback calls task.result(), which then raises into the event loop."
        )
