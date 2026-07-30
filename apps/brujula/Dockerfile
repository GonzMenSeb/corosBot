# syntax=docker/dockerfile:1
#
# Build context is the REPO ROOT, not this directory:
#   docker build -f apps/brujula/Dockerfile -t brujula .
# `packages/coros_core` is flattened to `/app/coros_core`, which keeps `import coros_core`
# identical to what the Makefile's PYPATH and pytest.ini give the test suite. /app is also
# the app directory, so `reflex export` finds .web/ and reflex.lock/ where it expects them.
#
# One container, one port. Reflex needs two in dev — frontend 3000, backend 8000 — but in
# prod `App.__call__` mounts the compiled frontend onto the backend's own ASGI app, so a
# single 8000 serves the page AND the /_event WebSocket. That is what makes this deployable
# behind one Traefik router instead of two.
#
# api_url is left at rxconfig.py's localhost default: the compiled bundle rewrites a
# SAME_DOMAIN_HOSTNAMES host to whatever origin actually served the page, upgrading
# http->https and ws->wss. So the image carries no domain and needs no rebuild to move hosts.

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

# The ROOT requirements.txt. `apps/brujula/requirements.txt` is `reflex init` output holding
# only the reflex pin, and .dockerignore keeps it out of the context so it cannot shadow this.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY packages/coros_core/ ./coros_core/
COPY apps/brujula/rxconfig.py ./
COPY apps/brujula/reflex.lock/ ./reflex.lock/
COPY apps/brujula/assets/ ./assets/
COPY apps/brujula/brujula/ ./brujula/

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
COPY --from=builder /app/brujula ./brujula
COPY --from=builder /app/.web/build/client ./.web/build/client
# Not optional. compile_app() takes a no-write fast path only when this marker already
# exists; without it, a skip-compile boot still tries to CREATE .web/backend and dies with
# EACCES on a non-root /app.
COPY --from=builder /app/.web/backend ./.web/backend

# state_manager_mode is DISK with no Redis configured, so Reflex pickles session state under
# ./.states. uid 1001 is a two-way contract with `roles/brujula` in vps-infrastructure,
# which bind-mounts /opt/brujula/states here owned 1001:1001 — a mismatch is a silently
# unwritable state directory, not an error.
RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin brujula \
    && mkdir -p /app/.states \
    && chown -R brujula:brujula /app/.states

USER brujula
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ping').status==200 else 1)"

# Granian, not uvicorn: reflex ships granian and no uvicorn, so should_use_granian() is what
# its own prod path takes. `--workers 1` is load-bearing rather than tuning — state._SESSIONS
# is a module-level dict, so a second worker serves half the users an empty conversation.
CMD ["granian", "--interface", "asgi", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--log-level", "info", "brujula.app:app"]
