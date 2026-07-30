from __future__ import annotations

import os

import reflex as rx
from reflex.plugins import SitemapPlugin

# Compiled INTO the frontend bundle, so changing it means a recompile. It stays localhost
# because the compiled client rewrites a SAME_DOMAIN_HOSTNAMES host to
# window.location.hostname (`.web/utils/state.js:99-121`) — which is what lets one image
# serve every domain. Note the port only survives on http: the same block clears it when
# the page was served over https, which is how the hosted instance reaches :8000 with
# :8001 baked in. A plain-http deploy on any other port needs this override, as does a dev
# tunnel, whose backend URL is new on every restart.
# Symptom of getting this wrong: a page that renders perfectly and does nothing at all.
API_URL = os.environ.get("HUELLA_API_URL", "http://localhost:8001")

# Reflex's default is `("*",)` and `App._add_cors` pairs it with `allow_credentials=True`, so
# out of the box ANY site can make credentialed cross-origin calls to this backend and read
# the answers — measured under granian 30 Jul 2026 by `scripts/spike_api_transformer.py`,
# where `Origin: https://evil.example` came back mirrored ON THE OAUTH CALLBACK. Narrowing it
# is the only thing that closes that. The value feeds BOTH the Starlette middleware and the
# socket.io server (`reflex/app.py:540`), so the origin the browser loads the page from has to
# be on this list or the /_event handshake is refused and the page renders perfectly and does
# nothing. Requests carrying no Origin at all are not checked
# (`engineio/async_server.py:227`), which is why the deploy health-check's curl probe and
# STRAVA'S OWN REDIRECT — a top-level navigation carries no Origin — are unaffected either
# way. Unlike `api_url` this is read at worker boot, not baked into the bundle.
# Both spellings of the dev frontend, because engineio compares the Origin header as an exact
# string (`origin not in allowed_origins`, async_server.py:229) and a browser sends whichever
# host was typed. With only one of these, opening the other renders the page and never connects.
DEV_ORIGINS = "http://localhost:3001,http://127.0.0.1:3001"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("HUELLA_ALLOWED_ORIGINS", DEV_ORIGINS).split(",")
    if origin.strip()
]

config = rx.Config(
    app_name="huella",
    # Reflex otherwise resolves the app module as app_name + "." + app_name — huella.huella
    # — and reports the miss as a folder-structure error.
    app_module_import="huella.app",
    api_url=API_URL,
    # Reflex takes "the next available port" when one is busy, which moves the app out from
    # under whatever is proxying or tunnelling it. Both apps run at once in dev, so Huella
    # pins the pair one above Brújula's 3000/8000. STRAVA_REDIRECT_URI points at the BACKEND
    # port: the callback is a Starlette route on the api_transformer, not a page.
    frontend_port=3001,
    backend_port=8001,
    cors_allowed_origins=ALLOWED_ORIGINS,
    # Reflex injects a sticky "Built with Reflex" badge into every page unless this is set.
    # It defaults to None, which `reflex/compiler/compiler.py:1225-1232` resolves to True for
    # anyone not on a paid Reflex tier, and `_setup_sticky_badge()` then runs. Third-party
    # branding on a client-facing app is not a cosmetic issue when the brief is a distinct
    # brand identity, and the field exists to control it.
    show_built_with_reflex=False,
    # False allows localhost only, and every other host — a tunnel, a staging domain — then
    # gets `403 Blocked request. This host is not allowed.` on a healthy app.
    vite_allowed_hosts=True,
    # One password-gated route has nothing to put in a sitemap. Left on, the default plugin
    # prints a startup warning asking to be told about it either way.
    disable_plugins=[SitemapPlugin],
)
