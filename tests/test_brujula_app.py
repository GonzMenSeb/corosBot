"""Brújula's serving contract: one config, one page, one gate that is not a route.

Nothing here needs a browser. What it pins is the handful of `rxconfig.py` values that each
cost somebody a running demo, and the shape of the page tree — a second route for the gate
is an unauthenticated URL that renders the shell, so the gate is a branch inside the only
route there is.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import subprocess
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest
import reflex as rx

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ("brujula", "huella")


def rxconfig_path(app: str) -> pathlib.Path:
    return ROOT / "apps" / app / "rxconfig.py"


def load_rxconfig(app: str) -> types.ModuleType:
    """Load `apps/<app>/rxconfig.py` under a module name that says which app it is.

    Reflex fixes both the filename and the module name it imports it as
    (`constants.Config.FILE == "rxconfig.py"`, `constants.Config.MODULE == "rxconfig"`), and
    the suite puts both app directories on `sys.path`. A plain `import rxconfig` therefore
    resolves by path order and caches the winner in `sys.modules` — one app's config handed
    back for the other, with no error anywhere. Loading by file path is the only way to name
    which one you meant.
    """
    path = rxconfig_path(app)
    spec = importlib.util.spec_from_file_location(f"_rxconfig_{app}", path)
    assert spec is not None and spec.loader is not None, f"{path} is not loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(app: str) -> rx.Config:
    return load_rxconfig(app).config


def descendants(component: Any) -> Iterator[Any]:
    yield component
    for child in getattr(component, "children", ()) or ():
        yield from descendants(child)


def outermost_cond(component: Any) -> Any:
    """The first conditional a browser meets on the way down the page tree.

    Breadth-first, because the gate has to be the one wrapping everything else: a
    conditional above it decides whether the password check renders at all.
    """
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


class TestRxconfigCarriesTheFactsThatCostADemo:
    def test_the_app_module_import_is_explicit(self) -> None:
        assert config("brujula").app_module_import == "brujula.app", (
            "app_module_import is not 'brujula.app'.\n"
            "Reflex otherwise resolves the app module as app_name + '.' + app_name —\n"
            "`brujula.brujula` — and dies with ModuleNotFoundError before serving anything.\n"
            "See AGENTS.md, Reflex & serving."
        )

    def test_the_app_name_is_the_package_directory(self) -> None:
        cfg = config("brujula")
        assert cfg.app_name == "brujula", "app_name must match apps/brujula/brujula/."
        assert (ROOT / "apps" / cfg.app_name / cfg.app_name).is_dir(), (
            f"apps/{cfg.app_name}/{cfg.app_name}/ does not exist.\n"
            "Reflex reports the mismatch as a folder-structure error at boot, not as a\n"
            "config error, so it reads like the package is missing."
        )

    @pytest.mark.parametrize(("port", "value"), (("frontend_port", 3000), ("backend_port", 8000)))
    def test_the_ports_are_pinned(self, port: str, value: int) -> None:
        assert getattr(config("brujula"), port) == value, (
            f"{port} is not pinned to {value}.\n"
            "Reflex falls back to 'the next available port' when one is taken, which\n"
            "silently moves the app out from under whatever is proxying or tunnelling it.\n"
            "Both apps run at once in dev, so each rxconfig.py pins its own pair."
        )

    def test_vite_allowed_hosts_is_on(self) -> None:
        assert config("brujula").vite_allowed_hosts is True, (
            "vite_allowed_hosts is off.\n"
            "It defaults to False, which allows localhost only, and any other host — a\n"
            "quick tunnel, a staging domain — then gets `403 Blocked request. This host is\n"
            "not allowed.` on a completely healthy app. This is the demo-killer."
        )

    def test_the_baked_api_url_is_domain_agnostic(self) -> None:
        assert config("brujula").api_url == "http://localhost:8000", (
            "api_url is not http://localhost:8000.\n"
            "It is compiled INTO the frontend bundle, and the compiled client rewrites any\n"
            "same-domain host — localhost, 0.0.0.0, :: — to window.location.hostname,\n"
            "upgrading ws:→wss: and clearing the port on https. Baking the real hostname in\n"
            "needs one image per domain and buys nothing. Do not 'fix' this to the public\n"
            "URL. See AGENTS.md, Container deployment."
        )

    def test_the_api_url_is_overridable_for_a_tunnel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRUJULA_API_URL", "https://tunnel.example/api")
        assert config("brujula").api_url == "https://tunnel.example/api", (
            "BRUJULA_API_URL no longer overrides api_url.\n"
            "A dev tunnel hands out a fresh backend URL on every restart, and the browser\n"
            "reaches the backend over a WebSocket it opens itself. With no override the\n"
            "page renders perfectly and does nothing at all."
        )

    def test_the_default_credentialed_cors_wildcard_is_not_shipped(self) -> None:
        """Reflex defaults `cors_allowed_origins` to `("*",)` and `App._add_cors` pairs it with
        `allow_credentials=True`, so an unset value means any site can make credentialed calls
        to this backend and read the answers — measured under granian 30 Jul 2026."""
        allowed = list(config("brujula").cors_allowed_origins)
        assert "*" not in allowed and allowed, (
            f"cors_allowed_origins is {allowed!r}.\n"
            "A wildcard here is Reflex's default, not a decision, and _add_cors sends it with\n"
            "allow_credentials=True. See AGENTS.md, 'The OAuth callback route'."
        )

    def test_both_spellings_of_the_dev_origin_are_allowed_by_default(self) -> None:
        """The list also gates the socket.io handshake (`reflex/app.py:540`), and engineio
        compares the Origin header as an exact string — `origin not in allowed_origins`,
        `async_server.py:229`. A browser sends whichever host was typed, so `localhost` and
        `127.0.0.1` are two different origins and shipping only one of them means opening the
        other renders the page and never connects."""
        assert config("brujula").cors_allowed_origins == [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ], (
            "the default allowed origins are no longer both spellings of the dev frontend on\n"
            ":3000. rxconfig pins frontend_port=3000 and the backend answers on :8000, so the\n"
            "dev browser is always cross-origin. Dropping either spelling does not fail loudly."
        )

    def test_the_hosted_origin_replaces_the_dev_one_rather_than_joining_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`roles/brujula/templates/env.j2` templates the public origin into this var. A
        production instance that still trusted localhost would be trusting any process on the
        box, which on this VPS means every other container."""
        monkeypatch.setenv("BRUJULA_ALLOWED_ORIGINS", "https://brujula.web.vespiridion.org")
        assert config("brujula").cors_allowed_origins == ["https://brujula.web.vespiridion.org"], (
            "BRUJULA_ALLOWED_ORIGINS no longer replaces the default. If it appends instead,\n"
            "the hosted app trusts http://localhost:3000 as well."
        )

    def test_a_comma_separated_list_is_accepted_and_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BRUJULA_ALLOWED_ORIGINS", "https://a.example , https://b.example ,")
        assert config("brujula").cors_allowed_origins == [
            "https://a.example",
            "https://b.example",
        ], (
            "the origin list no longer splits on commas and trims. Reflex's own field carries\n"
            "SequenceOptions(delimiter=','), so a reader will expect that shape; an untrimmed\n"
            "' https://b.example' silently matches no origin at all."
        )

    def test_no_third_party_badge_is_injected_into_the_page(self) -> None:
        """`show_built_with_reflex` defaults to None, which the compiler resolves to True for
        anyone not on a paid Reflex tier — so leaving it unset ships a sticky "Built with
        Reflex" badge on every page. Found bottom-right in a Playwright pass, 30 Jul 2026."""
        assert config("brujula").show_built_with_reflex is False, (
            "show_built_with_reflex is not explicitly False, so Reflex will inject its own\n"
            "sticky badge into a client-facing page. None is not off — the compiler turns None\n"
            "into True unless the deploy is on a paid tier."
        )

    def test_the_app_ships_a_favicon(self) -> None:
        """A browser requests /favicon.ico unprompted on every page load, and Reflex serves
        `assets/` at the web root, so an absent file is a 404 in the console of a demo."""
        icon = rxconfig_path("brujula").parent / "assets" / "favicon.ico"
        assert icon.is_file(), f"{icon} is missing; every page load logs a 404 for it"
        assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00", (
            "assets/favicon.ico is not an ICO. Browsers sniff the bytes, and the extension "
            "alone will not make a PNG or an SVG work at this path."
        )

    def test_rxconfig_needs_nothing_on_sys_path_but_its_own_directory(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run(
            [sys.executable, "-c", "from reflex.config import get_config;print(get_config().app_module_import)"],
            cwd=ROOT / "apps" / "brujula",
            env=env,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0 and done.stdout.strip() == "brujula.app", (
            "`reflex.config.get_config()` could not read apps/brujula/rxconfig.py from that\n"
            f"directory with a bare environment.\nstdout: {done.stdout}\nstderr: {done.stderr}\n"
            "get_config() reduces sys.path to the current directory to import the module, and\n"
            "only retries with the ambient path if that raises. So an `import coros_core` in\n"
            "rxconfig.py resolves when the caller happens to export the right PYTHONPATH and\n"
            "raises on a bare `reflex run` in this directory — verified 30 jul 2026. Keep it\n"
            "to reflex and the stdlib."
        )

    def test_the_theme_is_configured_on_the_app_not_in_the_config(self) -> None:
        cfg = config("brujula")
        assert not any(
            type(plugin).__name__ == "RadixThemesPlugin" for plugin in (cfg.plugins or [])
        ), (
            "the Radix theme moved into rxconfig.py as a RadixThemesPlugin.\n"
            "`rx.App(theme=...)` warns that it is deprecated in favour of exactly that, and\n"
            "on reflex 0.9.7 — the pin the whole hosting story was measured on — it still\n"
            "works. Taking the warning's advice puts the theme somewhere that cannot read\n"
            "brujula/ui/theme.py's tokens: rxconfig.py is imported with sys.path reduced to\n"
            "its own directory. Revisit when the pin moves, not before."
        )


class TestTheModuleNameRxconfigIsGlobalToTheSuite:
    @pytest.mark.parametrize("app", APPS)
    def test_each_app_owns_a_file_at_the_name_reflex_demands(self, app: str) -> None:
        path = rxconfig_path(app)
        if app != "brujula" and not path.exists():
            pytest.skip(f"apps/{app}/rxconfig.py does not exist yet")
        assert path.name == pathlib.Path(rx.constants.Config.FILE).name, (
            "Reflex looks for exactly one filename in the working directory.\n"
            f"It is {rx.constants.Config.FILE}; this file cannot be renamed to disambiguate."
        )
        assert path.exists(), f"{path} is missing — `reflex run` in that directory has no config."

    def test_a_bare_import_by_name_reaches_only_one_app(self) -> None:
        present = [p for p in (rxconfig_path(app) for app in APPS) if p.exists()]
        spec = importlib.util.find_spec(rx.constants.Config.MODULE)
        assert spec is not None and spec.origin is not None, (
            "No module named rxconfig is importable from the suite's sys.path, which means\n"
            "apps/brujula is not on it. See pythonpath in pytest.ini."
        )
        assert pathlib.Path(spec.origin).resolve() == present[0].resolve(), (
            f"`import rxconfig` resolves to {spec.origin}, the first of "
            f"{[str(p) for p in present]} on sys.path.\n"
            "The module name is global and the winner is cached in sys.modules, so a test\n"
            "that imports it by name gets whichever app comes first in pytest.ini's\n"
            "pythonpath — silently, for the other app too. Use load_rxconfig(app)."
        )


class TestBrujulaServesOnePageAndPutsTheGateInsideIt:
    @staticmethod
    def app_module() -> types.ModuleType:
        return importlib.import_module("brujula.app")

    @staticmethod
    def pages() -> dict[str, Any]:
        return dict(TestBrujulaServesOnePageAndPutsTheGateInsideIt.app_module().app._unevaluated_pages)

    def test_the_module_reflex_is_pointed_at_exposes_an_app(self) -> None:
        assert isinstance(self.app_module().app, rx.App), (
            "brujula/app.py has no module-level `app` that is an rx.App.\n"
            "Reflex resolves app_module_import and then requires that attribute; it reports\n"
            "the miss as 'The app instance in the specified app_module_import in rxconfig\n"
            "must be an instance of rx.App.'"
        )

    def test_exactly_one_page_is_registered(self) -> None:
        pages = self.pages()
        assert list(pages) == ["index"], (
            f"routes registered: {sorted(pages)}\n"
            "Brújula serves one page. A route for the gate — or for anything the gate\n"
            "protects — is a URL that renders without the password check ever running,\n"
            "because on_load is per page and `unlocked` is what the shell branches on."
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
            "`State.unlocked` defaults to a hard False — its default is compiled into the\n"
            "frontend bundle — so on_page_load is the only thing that opens the gate when\n"
            "BRUJULA_PASSWORD is unset, and the only thing that honours the cookie when it\n"
            "is set. Without it a local run shows the password screen with no password."
        )

    def test_the_gate_is_a_conditional_render_not_a_second_route(self) -> None:
        cond = outermost_cond(self.app_module().index())
        assert "unlocked" in str(cond.cond), (
            f"the root page branches on {cond.cond!s}, not on State.unlocked.\n"
            "The gate is `rx.cond(State.unlocked, shell, gate)`. Branching on anything else\n"
            "— gate_on, a computed var over it — decides visibility from something the\n"
            "browser can hold while `unlocked` stays False."
        )

    def test_both_branches_of_the_gate_are_built(self) -> None:
        branches = outermost_cond(self.app_module().index()).children
        assert len(branches) == 2, f"the gate has {len(branches)} branches, expected 2."
        for label, branch in zip(("unlocked", "locked"), branches, strict=True):
            assert list(descendants(branch))[1:], (
                f"the {label} branch of the gate renders nothing.\n"
                "A one-armed rx.cond renders an empty fragment, so the locked page comes up\n"
                "blank instead of asking for the password."
            )
