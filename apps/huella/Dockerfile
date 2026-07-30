# syntax=docker/dockerfile:1
#
# UNVERIFIED — THIS IMAGE HAS NEVER BEEN BUILT. Huella is unfinished: as of 30 Jul 2026 the
# app ships `huella/__init__.py`, `huella/ui/theme.py` and `huella/strava/`, and has no
# `rxconfig.py`, no `huella/app.py` and no `assets/`. `reflex export` compiles the component
# tree by importing the app module named by `rxconfig.py`'s `app_module_import`, so it cannot
# run until both files exist, and the two COPY lines for `rxconfig.py` and `assets/` fail
# before that on a context that does not carry them. Every line below is Brújula's, whose
# build and runtime behaviour ARE verified (`docker build -f apps/brujula/Dockerfile .`, a
# 200 on /ping, a 101 on /_event, uid 1001 writing .states). Nothing here is a claim about
# Huella. What will verify it: `apps/huella/rxconfig.py` (app_name "huella",
# app_module_import "huella.app", api_url localhost default, HUELLA_ALLOWED_ORIGINS,
# frontend_port 3001 / backend_port 8001 for dev), `huella/app.py` with its module-level
# `app` and the OAuth callback route on the api_transformer, `assets/huella.css`, and a
# re-export of `reflex.lock/` once the component set is final — then this same build and the
# same five runtime probes.
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
