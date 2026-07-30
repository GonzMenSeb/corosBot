# syntax=docker/dockerfile:1
#
# VERIFIED 30 Jul 2026, built and probed. 268 MB. `/ping` 200, the compiled `/` served with
# Huella's own title and no Reflex badge, `/favicon.ico` 200, HEALTHCHECK reports `healthy`,
# uid 1001 writes `/app/.states`, one granian worker, and nothing from the builder leaked —
# bun, node, npm, npx, unzip, curl and gcc are all absent, there is no `node_modules`, and no
# `requirements.txt` survives to shadow the root pins. `/_event` answers 101 with no Origin,
# 101 from the allowed one and **403** from a foreign one. The OAuth callback outranks the
# compiled frontend's catch-all in the real image, not just in a unit test:
# `?code=…&state=…` → 303 with an empty body, POST → 405, `/oauth/strava/nope` → 404.
#
# Probe it the same way. The host port must be **8001**, not any free port: `api_url` is
# baked into the bundle as `http://localhost:8001` and the compiled client only clears the
# port on https, so over plain http the browser asks for whatever was baked. Serve it
# anywhere else and the page renders perfectly and never connects.
#   docker build -f apps/huella/Dockerfile -t huella:probe .
#   docker run -d --name huella-probe -p 8001:8000 \
#     -e HUELLA_PASSWORD=qa -e HUELLA_ALLOWED_ORIGINS=http://localhost:8001 \
#     -e GEMINI_API_KEY=unset huella:probe
# The origin must match too, or the `/_event` handshake is refused with the same symptom.
#
# Build context is the REPO ROOT, not this directory:
#   docker build -f apps/huella/Dockerfile -t huella .
# `packages/coros_core` is flattened to `/app/coros_core`, which keeps `import coros_core`
# identical to what the Makefile's PYPATH and pytest.ini give the test suite. /app is also
# the app directory, so `reflex export` finds .web/ and reflex.lock/ where it expects them.
#
# One container, one port. Huella's dev pair is 3001/8001, but in prod `App.__call__` mounts
# the compiled frontend onto the backend's own ASGI app, so a single 8000 serves the page,
# the /_event WebSocket AND /oauth/strava/callback — Starlette matches our api_transformer
# routes ahead of the frontend's catch-all mount. Traefik points at 8000 for both apps.
#
# api_url is left at rxconfig.py's localhost default: the compiled bundle rewrites a
# SAME_DOMAIN_HOSTNAMES host to whatever origin actually served the page, upgrading
# http->https and ws->wss. Strava's callback domain therefore needs no baked-in hostname.

FROM python:3.12-slim AS builder

# Reflex downloads bun and unpacks it with unzip; the frontend build needs both.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The ROOT requirements.txt. `apps/huella/requirements.txt`, once a first `reflex run` seeds
# one, holds only the reflex pin, and .dockerignore keeps it out of the context so it cannot
# shadow this.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY packages/coros_core/ ./coros_core/
COPY apps/huella/rxconfig.py ./
COPY apps/huella/reflex.lock/ ./reflex.lock/
COPY apps/huella/assets/ ./assets/
COPY apps/huella/huella/ ./huella/

# Produces .web/build/client. Doing it here is the whole point of the split: the runtime
# image gets no bun, no node_modules and no build step on boot.
RUN reflex export --frontend-only --no-zip --env prod --loglevel info


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # These two are declared internal=True in reflex_base/environment.py, which prefixes the
    # real variable name with "__". Without the prefix they are silently ignored.
    # SKIP_COMPILE: the frontend is already built, so don't regenerate JSX on boot.
    # MOUNT_FRONTEND_COMPILED_APP: serve .web/build/client from the ASGI app itself.
    __REFLEX_SKIP_COMPILE=true \
    __REFLEX_MOUNT_FRONTEND_COMPILED_APP=true \
    REFLEX_ENV_MODE=prod \
    REFLEX_TELEMETRY_ENABLED=false

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/rxconfig.py ./
COPY --from=builder /app/coros_core ./coros_core
COPY --from=builder /app/assets ./assets
COPY --from=builder /app/huella ./huella
COPY --from=builder /app/.web/build/client ./.web/build/client
# Not optional. compile_app() takes a no-write fast path only when this marker already
# exists; without it, a skip-compile boot still tries to CREATE .web/backend and dies with
# EACCES on a non-root /app.
COPY --from=builder /app/.web/backend ./.web/backend

# state_manager_mode is DISK with no Redis configured, so Reflex pickles session state under
# ./.states. uid 1001 is a two-way contract with `roles/huella` in vps-infrastructure, which
# bind-mounts /opt/huella/states here owned 1001:1001 — a mismatch is a silently unwritable
# state directory, not an error. Strava tokens never reach it: they live outside Reflex state
# in `_SESSIONS` and are never pickled.
RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin huella \
    && mkdir -p /app/.states \
    && chown -R huella:huella /app/.states

USER huella
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ping').status==200 else 1)"

# Granian, not uvicorn: reflex ships granian and no uvicorn, so should_use_granian() is what
# its own prod path takes. `--workers 1` is load-bearing rather than tuning — the Strava
# session map is a module-level dict, so a second worker serves half the users an
# unauthenticated session and loses the refresh token the other worker rotated.
CMD ["granian", "--interface", "asgi", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--log-level", "info", "huella.app:app"]
