#!/usr/bin/env bash
# The local container gate, both apps, as one command.
#
# These probes existed as a numbered list in docs/QA-BRUJULA.md §5 and were retyped by hand
# every session. Two of them are easy to get wrong in a way that reports a pass:
#
#   * `/ping` answers the JSON string `"pong"`, with the quotes. Comparing against a bare
#     `pong` fails a healthy app.
#   * The runtime image has NO `ps` — that is the point of the two-stage build, and it is
#     asserted below. So `docker exec … ps | grep -c granian` returns 0 on a perfectly
#     healthy container, which reads as "no workers" rather than as "the check could not
#     run". Worker counting therefore goes through `docker top`, which uses the HOST's ps
#     against the container's PIDs and does not care what is inside the image.
#
# Ports are not a preference. `api_url` is baked into each frontend bundle and the compiled
# client only clears the port on https — so over plain http the browser asks for whatever
# was baked. Brujula baked :8000, Huella baked :8001. Map anything else and you get a page
# that renders perfectly and never connects, with nothing logging why.
#
#   ./scripts/smoke_containers.sh            # builds both images, then probes
#   ./scripts/smoke_containers.sh --no-build # probe images that already exist
set -u

BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s — %s\n' "$1" "$2"; }
chk() { if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1" "expected $3, got '$2'"; fi; }

cleanup() { docker rm -f brujula-probe huella-probe >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

if [ "$BUILD" = "1" ]; then
  echo "building both images (context = repo root) ..."
  docker build -q -f apps/brujula/Dockerfile -t brujula:probe . >/dev/null || exit 1
  docker build -q -f apps/huella/Dockerfile  -t huella:probe  . >/dev/null || exit 1
fi

docker run -d --name brujula-probe -p 8000:8000 \
  -e BRUJULA_PASSWORD=qa -e BRUJULA_ALLOWED_ORIGINS=http://localhost:8000 \
  -e GEMINI_API_KEY=unset brujula:probe >/dev/null
docker run -d --name huella-probe -p 8001:8000 \
  -e HUELLA_PASSWORD=qa -e HUELLA_ALLOWED_ORIGINS=http://localhost:8001 \
  -e GEMINI_API_KEY=unset huella:probe >/dev/null

for _ in $(seq 1 60); do
  a=$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://localhost:8000/ping 2>/dev/null)
  b=$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://localhost:8001/ping 2>/dev/null)
  [ "$a" = "200" ] && [ "$b" = "200" ] && break
  sleep 2
done

probe_app() {
  NAME=$1; PORT=$2; CTR=$3; TITLE=$4
  echo ""; echo "=== $NAME (host :$PORT -> container :8000) ==="

  chk "/ping 200"   "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://localhost:$PORT/ping)" "200"
  chk "/ping body"  "$(curl -s -m 5 http://localhost:$PORT/ping)" '"pong"'

  root=$(curl -s -m 15 http://localhost:$PORT/)
  chk "compiled / 200" "$(curl -s -o /dev/null -w '%{http_code}' -m 15 http://localhost:$PORT/)" "200"
  case "$root" in *"<title>$TITLE"*) ok "correct <title>";; *) bad "<title>" "missing '$TITLE'";; esac
  case "$root" in *"uilt with Reflex"*) bad "Reflex badge" "present";; *) ok "no Reflex badge";; esac
  printf '  ---- / is %s bytes\n' "$(printf '%s' "$root" | wc -c)"
  chk "/favicon.ico 200" "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://localhost:$PORT/favicon.ico)" "200"

  for _ in $(seq 1 45); do
    h=$(docker inspect --format '{{.State.Health.Status}}' "$CTR" 2>/dev/null)
    [ "$h" = "healthy" ] && break; sleep 2
  done
  chk "HEALTHCHECK reports healthy" "$h" "healthy"

  chk "runs as uid 1001" "$(docker exec "$CTR" id -u 2>/dev/null)" "1001"
  if docker exec "$CTR" sh -c 'touch /app/.states/.probe && rm /app/.states/.probe' 2>/dev/null
  then ok "uid 1001 writes /app/.states"; else bad "uid 1001 writes /app/.states" "touch failed"; fi

  # Host-side ps. See the header for why `docker exec … ps` cannot be used here. No `-o`:
  # docker top requires a PID field in the ps format and rejects `-o args` outright with
  # "Couldn't find PID field in ps output", which -- swallowed by 2>/dev/null -- counts 0
  # and reads as "no workers". The default format's header carries no "granian", so a plain
  # grep -c is exact.
  chk "supervisor + exactly one worker" \
      "$(docker top "$CTR" 2>/dev/null | grep -c granian)" "2"

  leaked=""
  for t in bun node npm npx unzip curl gcc make ps; do
    docker exec "$CTR" sh -c "command -v $t" >/dev/null 2>&1 && leaked="$leaked $t"
  done
  [ -z "$leaked" ] && ok "no build tooling leaked" || bad "build tooling leaked" "found:$leaked"
  docker exec "$CTR" sh -c '[ -d /app/node_modules ]' 2>/dev/null \
    && bad "node_modules" "present" || ok "no node_modules"
  docker exec "$CTR" sh -c '[ -f /app/requirements.txt ]' 2>/dev/null \
    && bad "shadowing requirements.txt" "present at /app" || ok "no shadowing requirements.txt"

  # --http1.1 is load-bearing: over h2 the Upgrade handshake is invalid and granian answers
  # 400 on a healthy app. No Origin must pass — engineio does not check what is not sent,
  # which is why the Ansible health check and Strava's own redirect are unaffected.
  ws() {
    curl -s -D - -o /dev/null --http1.1 -N -m 4 \
      -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
      ${1:+-H "Origin: $1"} \
      "http://localhost:$PORT/_event/?EIO=4&transport=websocket" 2>/dev/null | head -1 |
      grep -oE '[0-9]{3}'
  }
  chk "/_event  no Origin"      "$(ws '')"                       "101"
  chk "/_event  allowed Origin" "$(ws "http://localhost:$PORT")" "101"
  chk "/_event  foreign Origin" "$(ws 'https://evil.example')"   "403"
}

probe_app BRUJULA 8000 brujula-probe "Br"
probe_app HUELLA  8001 huella-probe  "Huella"

# The one shape `make spike-oauth` cannot reproduce: a real image behind a real mount. A 404
# here means the compiled frontend's catch-all swallowed the path. No Strava credentials are
# involved — an unknown state simply fails consume_state.
echo ""; echo "=== HUELLA: the OAuth route outranks the frontend mount ==="
O=http://localhost:8001/oauth/strava
chk "?code&state    -> 303" "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$O/callback?code=x&state=y")" "303"
chk "?state=garbage -> 303" "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$O/callback?state=garbage")"  "303"
chk "POST           -> 405" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -m 5 "$O/callback")"        "405"
chk "unknown path   -> 404" "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$O/nope")"                    "404"
chk "unknown state redirects to /?strava=state" \
  "$(curl -s -D - -o /dev/null -m 5 "$O/callback?state=garbage" | grep -i '^location:' | tr -d '\r' | awk '{print $2}')" \
  "/?strava=state"

echo ""; echo "==================== $PASS passed, $FAIL failed ===================="
[ "$FAIL" -eq 0 ]
