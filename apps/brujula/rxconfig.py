from __future__ import annotations

import os

import reflex as rx
from reflex.plugins import SitemapPlugin

# Compiled INTO the frontend bundle, so changing it means a recompile. It stays localhost
# because the compiled client rewrites a same-domain host to window.location.hostname,
# upgrades ws:->wss: and clears the port on https — which is what lets one image serve every
# domain. The override is for a dev tunnel, whose backend URL is new on every restart.
# Symptom of getting this wrong: a page that renders perfectly and does nothing at all.
API_URL = os.environ.get("BRUJULA_API_URL", "http://localhost:8000")

config = rx.Config(
    app_name="brujula",
    # Reflex otherwise resolves the app module as app_name + "." + app_name — brujula.brujula
    # — and reports the miss as a folder-structure error.
    app_module_import="brujula.app",
    api_url=API_URL,
    # Reflex takes "the next available port" when one is busy, which moves the app out from
    # under whatever is proxying or tunnelling it. Huella pins its own pair.
    frontend_port=3000,
    backend_port=8000,
    # False allows localhost only, and every other host — a tunnel, a staging domain — then
    # gets `403 Blocked request. This host is not allowed.` on a healthy app.
    vite_allowed_hosts=True,
    # One password-gated route has nothing to put in a sitemap. Left on, the default plugin
    # prints a startup warning asking to be told about it either way.
    disable_plugins=[SitemapPlugin],
)
