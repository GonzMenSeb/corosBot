"""Prove Reflex can serve Huella's OAuth callback before any Strava code is written.

The question is narrow and it is the whole of PR 6's serving risk: under the *production*
shape — `granian --factory` against `rx.App.__call__`, with `__REFLEX_SKIP_COMPILE` and
`__REFLEX_MOUNT_FRONTEND_COMPILED_APP` on — does an explicit `/oauth/strava/callback` route
still get the request, or does the compiled frontend's catch-all mount swallow it and hand
Strava a 404?

Reading `reflex/app.py:747-761` says it works: a Starlette `api_transformer` has Reflex's
own ASGI app mounted *into* it at `""`, and Starlette matches in list order, so anything
registered before that mount wins. This runs it, and then keeps going: line 757 also calls
`App._add_cors(api_transformer)`, so Reflex puts its own CORS policy on our route, and
`cors_allowed_origins` defaults to `("*",)` with `allow_credentials=True`. Phase two proves
the one-line narrowing works and costs nothing.

Nothing here needs Strava credentials — the handler echoes the query string instead of
exchanging it — so this is unblocked while the app registration is pending.

Usage:
    make spike-oauth
    ./.venv/bin/python scripts/spike_api_transformer.py --keep --port 8123
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV = REPO / ".venv" / "bin"
BOOT_TIMEOUT = 90.0
EXPORT_TIMEOUT = 900.0
HOST = "https://huella.web.vespiridion.org"
FOREIGN = "https://evil.example"

RXCONFIG = '''from __future__ import annotations

import reflex as rx
from reflex.plugins import SitemapPlugin

config = rx.Config(
    app_name="spike",
    app_module_import="spike.app",
    api_url="http://localhost:{port}",
    frontend_port={port},
    backend_port={port},{cors}
    disable_plugins=[SitemapPlugin],
)
'''

APP = '''from __future__ import annotations

import contextlib
import os
from pathlib import Path

import reflex as rx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

MARKER = Path(os.environ["SPIKE_LIFESPAN_MARKER"])


async def callback(request: Request) -> JSONResponse:
    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)
    return JSONResponse(
        {
            "route": "callback",
            "code": code,
            "state": request.query_params.get("state"),
            "scope": request.query_params.get("scope"),
        }
    )


async def late(request: Request) -> JSONResponse:
    return JSONResponse({"route": "late"})


@contextlib.asynccontextmanager
async def transformer_lifespan(_app: Starlette):
    MARKER.write_text("the transformer's own lifespan ran")
    yield


api = Starlette(
    routes=[Route("/oauth/strava/callback", callback, methods=["GET"])],
    lifespan=transformer_lifespan,
)

app = rx.App(api_transformer=api)
app.add_page(lambda: rx.text("spike"), route="/")

# Registered after rx.App(...) and before granian calls the factory. The mount happens
# inside App.__call__, so this must still win.
api.routes.append(Route("/oauth/strava/late", late, methods=["GET"]))
'''


@dataclass(frozen=True)
class Probe:
    name: str
    detail: str
    ok: bool
    got: str


@dataclass(frozen=True)
class Reply:
    status: int
    body: str
    # Lower-cased keys: granian answers HTTP/1.1 with lower-case header names, so a
    # dict built off the wire misses "Content-Type" and reports a passing probe as failed.
    headers: dict[str, str]

    @property
    def json(self) -> dict[str, object]:
        try:
            return json.loads(self.body)
        except ValueError:
            return {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _config(root: Path, port: int, *, allowed: str | None) -> None:
    cors = f'\n    cors_allowed_origins=["{allowed}"],' if allowed else ""
    (root / "rxconfig.py").write_text(RXCONFIG.format(port=port, cors=cors))


def _write_app(root: Path) -> None:
    (root / "spike").mkdir(parents=True, exist_ok=True)
    (root / "spike" / "__init__.py").write_text("")
    (root / "spike" / "app.py").write_text(APP)


def _request(url: str, *, method: str = "GET", origin: str | None = None) -> Reply:
    req = urllib.request.Request(url, method=method)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Reply(resp.status, resp.read().decode("utf-8", "replace"), _lower(resp.headers.items()))
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read().decode("utf-8", "replace"), _lower(exc.headers.items()))


def _lower(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {key.lower(): value for key, value in items}


def _allow_origin(reply: Reply) -> str | None:
    return reply.headers.get("access-control-allow-origin")


def _upgrade(port: int) -> str:
    """Raw HTTP/1.1 handshake. Reflex's event channel is socket.io over websocket."""
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        "GET /_event/?EIO=4&transport=websocket HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(request.encode())
        return sock.recv(256).decode("utf-8", "replace").split("\r\n")[0]


@contextlib.contextmanager
def _serving(root: Path, port: int, env: dict[str, str], log: Path) -> Iterator[None]:
    proc = subprocess.Popen(
        [
            str(VENV / "granian"), "--interface", "asgi", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--workers", "1",
            "--log-level", "info", "spike.app:app",
        ],
        cwd=root,
        env={**env, "__REFLEX_SKIP_COMPILE": "1", "__REFLEX_MOUNT_FRONTEND_COMPILED_APP": "true"},
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT
        while True:
            if proc.poll() is not None:
                raise SystemExit(f"granian exited with {proc.returncode}\n{log.read_text()}")
            if time.monotonic() > deadline:
                raise SystemExit(f"no /ping after {BOOT_TIMEOUT}s\n{log.read_text()}")
            try:
                if _request(f"http://127.0.0.1:{port}/ping").status == 200:
                    break
            except OSError:
                time.sleep(0.4)
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def _routing_probes(port: int, marker: Path) -> list[Probe]:
    base = f"http://127.0.0.1:{port}"

    hit = _request(f"{base}/oauth/strava/callback?code=abc123&state=xyz&scope=read")
    blank = _request(f"{base}/oauth/strava/callback")
    posted = _request(f"{base}/oauth/strava/callback", method="POST")
    late = _request(f"{base}/oauth/strava/late")
    missing = _request(f"{base}/oauth/strava/nope")
    ping = _request(f"{base}/ping")
    root = _request(f"{base}/")
    upgrade = _upgrade(port)
    wildcard = _allow_origin(_request(f"{base}/oauth/strava/callback?code=abc", origin=FOREIGN))

    return [
        Probe(
            "the callback wins over the frontend mount",
            "GET /oauth/strava/callback?code=… -> 200 from our handler, query string intact",
            hit.status == 200
            and hit.json.get("route") == "callback"
            and hit.json.get("code") == "abc123"
            and hit.json.get("state") == "xyz"
            and hit.json.get("scope") == "read",
            f"{hit.status} {hit.body[:110]}",
        ),
        Probe(
            "we own the error branch too",
            "no code -> our 400, not a static 404",
            blank.status == 400 and blank.json.get("error") == "missing code",
            f"{blank.status} {blank.body[:60]}",
        ),
        Probe(
            "method routing is ours",
            "POST the callback -> 405; a swallowed path would 404",
            posted.status == 405,
            str(posted.status),
        ),
        Probe(
            "a route added after rx.App() still wins",
            "the mount is appended inside App.__call__, not at construction",
            late.status == 200 and late.json.get("route") == "late",
            f"{late.status} {late.body[:50]}",
        ),
        Probe(
            "the prefix is not a wildcard",
            "GET /oauth/strava/nope -> 404",
            missing.status == 404,
            str(missing.status),
        ),
        Probe(
            "Reflex's own routes survive",
            "GET /ping -> 200; the container healthcheck depends on it",
            ping.status == 200 and ping.body.strip('"') == "pong",
            f"{ping.status} {ping.body[:30]}",
        ),
        Probe(
            "the compiled frontend still serves",
            "GET / -> 200 text/html out of .web/build/client",
            root.status == 200
            and "text/html" in root.headers.get("content-type", "")
            and "<!DOCTYPE html>" in root.body,
            f"{root.status} {root.headers.get('content-type', '')}",
        ),
        Probe(
            "the event channel still upgrades",
            "GET /_event/ with Upgrade: websocket -> 101",
            "101" in upgrade,
            upgrade,
        ),
        Probe(
            "the transformer's lifespan NEVER runs",
            "Reflex mounts it under its own Starlette, and a mounted app gets no lifespan",
            not marker.exists(),
            "marker written" if marker.exists() else "marker absent",
        ),
        Probe(
            "the default CORS policy mirrors ANY origin",
            "_add_cors(api_transformer) + cors_allowed_origins=('*',) + allow_credentials",
            wildcard == FOREIGN,
            f"allow-origin: {wildcard}",
        ),
    ]


def _cors_probes(port: int) -> list[Probe]:
    base = f"http://127.0.0.1:{port}"

    foreign = _request(f"{base}/oauth/strava/callback?code=abc", origin=FOREIGN)
    ours = _request(f"{base}/oauth/strava/callback?code=abc", origin=HOST)
    bare = _request(f"{base}/oauth/strava/callback?code=abc")
    ping = _request(f"{base}/ping")
    root = _request(f"{base}/")
    upgrade = _upgrade(port)

    return [
        Probe(
            "narrowing drops the foreign mirror",
            "cors_allowed_origins=[the host] -> no allow-origin for anyone else",
            _allow_origin(foreign) is None,
            f"allow-origin: {_allow_origin(foreign)}",
        ),
        Probe(
            "our own origin is still allowed",
            "the configured host is mirrored back",
            _allow_origin(ours) == HOST,
            f"allow-origin: {_allow_origin(ours)}",
        ),
        Probe(
            "Strava's redirect is unaffected",
            "a top-level navigation carries no Origin and CORS never applies",
            bare.status == 200 and bare.json.get("code") == "abc",
            f"{bare.status} {bare.body[:60]}",
        ),
        Probe(
            "narrowing costs nothing",
            "/ping, / and the event channel all still serve",
            ping.status == 200 and root.status == 200 and "101" in upgrade,
            f"ping {ping.status} · root {root.status} · ws {upgrade.split(' ', 1)[-1]}",
        ),
    ]


def _report(title: str, probes: list[Probe]) -> int:
    width = max(len(p.name) for p in probes)
    print(f"\n{title}")
    for p in probes:
        print(f"  {'PASS' if p.ok else 'FAIL'}  {p.name.ljust(width)}  {p.got}")
        print(f"        {' ' * width}  {p.detail}")
    return sum(1 for p in probes if not p.ok)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    port = args.port or _free_port()
    root = Path(tempfile.mkdtemp(prefix="oauth-spike-"))
    marker = root / "lifespan.marker"
    log = root / "granian.log"
    print(f"spike dir {root}\nport      {port}")

    _write_app(root)
    _config(root, port, allowed=None)
    env = {
        **os.environ,
        "PYTHONPATH": f"{REPO / 'packages'}:{root}",
        "SPIKE_LIFESPAN_MARKER": str(marker),
    }

    print("\nexporting the frontend (prod)…")
    export = subprocess.run(
        [str(VENV / "reflex"), "export", "--frontend-only", "--no-zip", "--env", "prod", "--loglevel", "info"],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=EXPORT_TIMEOUT,
    )
    if export.returncode != 0:
        print(export.stdout.decode()[-4000:], export.stderr.decode()[-4000:], sep="\n")
        return 2
    static = root / ".web" / "build" / "client" / "index.html"
    print(f"  .web/build/client/index.html {'ok' if static.exists() else 'MISSING'}")

    with _serving(root, port, env, log):
        routing = _routing_probes(port, marker)

    # cors_allowed_origins is read at boot and is not compiled into the bundle, so the
    # second phase re-boots against the same export.
    _config(root, port, allowed=HOST)
    with _serving(root, port, env, root / "granian-narrowed.log"):
        cors = _cors_probes(port)

    failed = _report("phase 1 — routing, default config", routing)
    failed += _report(f"phase 2 — cors_allowed_origins=['{HOST}']", cors)

    total = len(routing) + len(cors)
    print(f"\n{total - failed}/{total} passed" + ("" if not failed else "  — SPIKE FAILED"))
    if args.keep:
        print(f"kept {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
