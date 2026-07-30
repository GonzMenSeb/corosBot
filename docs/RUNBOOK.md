# Runbook — Brújula and Huella

For when something is wrong in front of an audience. `docs/DEPLOY.md` is for putting it there in
the first place.

Every row in §1 is a failure that has actually happened, here or in DecaBot, and most of them
share one property: **they look like something else.** That is what this table is for.

---

## 1. Symptom → cause

### The page loads and does nothing

The single most common failure in this stack, and it has four distinct causes.

| What you see | Cause | Fix |
|---|---|---|
| Page renders, no reply ever arrives, console shows `Cannot connect to server` naming a `ws://` URL with **the wrong port** | `api_url` is compiled **into** the bundle. The client rewrites a same-domain host to `window.location.hostname` but only **clears the port on https**. Over plain http on any port but 8000, the baked port survives | Serve over TLS, or serve on the port the bundle was built with. Not a code bug |
| Page renders, no reply, and `/_event/` returns **403** | `cors_allowed_origins` gates the socket.io handshake too, and engineio compares `Origin` as an **exact string**. `localhost` and `127.0.0.1` are different origins | Put the origin the browser actually uses in `<APP>_ALLOWED_ORIGINS`. Both spellings are in the dev default for this reason |
| Page renders, no reply, `/_event/` returns **400 Invalid websocket upgrade** when you probe with curl | You probed over HTTP/2. `Connection`/`Upgrade` is an HTTP/1.1 mechanism and Traefik negotiates h2 with curl | Add `--http1.1`. The app is fine |
| Everything 403s including `/`, on a tunnel or a new domain | `vite_allowed_hosts=False` | It is `True` in both `rxconfig.py`; if you changed it, change it back |

### The agent answers, but says it could not look

| What you see | Cause | Fix |
|---|---|---|
| Every turn ends "No pude consultar el catálogo", trace shows `catalog.unavailable` `status: 429` `rate_limited: true` | **Almost certainly not a rate limit.** COROS sits behind Cloudflare, which classifies by TLS fingerprint and answers Python's `ssl` ClientHello with `429 local_rate_limited` regardless of how long you wait. Measured: 30+ minutes of silence, then `requests` first request → 429, while `curl` was served seconds later | The transport is `curl_cffi` with `impersonate="chrome"`. If this reappears, Cloudflare has moved the goalposts: check whether plain `curl` is still served, then bump the impersonation target. **Do not add retries** |
| `catalog.unavailable` with `status: 403` and a ~7 KB body | Fingerprint **rejected**, not throttled — impersonation is off or stale. A different failure from the 429 and deliberately not flagged `rate_limited` | Check `impersonate` is being passed. See `DECISIONS.md`, 30 Jul, "It is the TLS fingerprint" |
| A turn says nothing is available | Read the trace before believing it. `catalog.unavailable` present ⇒ we could not look, which is **not** an inventory claim. `guardrail.buy_nothing` with `reason: inconclusive` is the app correctly refusing to turn one into the other | Nothing. This is the design working |

### Deploy and CI

| What you see | Cause | Fix |
|---|---|---|
| `docker push` uploads every layer then fails **415 manifest invalid** | Zot rejects Docker v2 manifests. It fails *after* the upload, so it looks like a network problem | `docker buildx ... --provenance=false --sbom=false --output type=image,oci-mediatypes=true,push=true`. The attestations matter too: they turn the result into an index Zot also rejects |
| A brand-new Jenkins job 404s in the UI **and** the API | JCasC writes `jobs/<name>/config.xml` after the item map is built. It exists on disk and is invisible | `curl -u admin:… -X POST https://jenkins.web.vespiridion.org/reload`. Existing jobs update in place and never need this |
| `brujula-deploy` fails at checkout | The GitHub deploy key for `corosBot` is missing, or `vault_jenkins_coros_ssh_key` is not in the vault | `DEPLOY.md` §1a-1b |
| The deploy job builds nothing on a merge that changed app code | The touched-path gate compares `GIT_PREVIOUS_SUCCESSFUL_COMMIT` to the build SHA. If the job has never succeeded, that variable is unset | Check the Scope stage's log; it prints what it matched |
| Health check fails and the job rolls back | Working as designed. It reads the outgoing `org.opencontainers.image.revision` off the live `:latest` before replacing it, then pulls `:$PREV_SHA`, retags, and `compose up -d` | Read the Health stage log for which probe failed — `/ping` or the websocket. If it says there is **no labelled previous revision**, the live image predates the pipeline and rollback is manual |
| `ansible-playbook --tags huella` prints "image not yet available" and starts nothing | Deliberate. The pull is tolerant so a first run before the image exists is safe | Populate Zot, then re-run |
| Container starts, then the app cannot write session state | uid mismatch. The image runs as **1001** and the role bind-mounts `states/` owned `1001:1001`. A mismatch is silent | `docker exec <c> id -u`, and `roles/<app>/defaults/main.yml` |
| Traefik stops serving everything on 80/443 | Host `caddy`/`nginx` stole the ports. `roles/traefik` stops, disables **and masks** them — a 19-day outage on 2026-07-09. Do not remove those tasks | Re-run the traefik role |

### Tests and local development

| What you see | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError` collecting the suite | Import roots live in **`pytest.ini`'s `pythonpath`** and nowhere else. A caller that exports a partial `PYTHONPATH=.` runs but cannot import the core | Do not pass `PYTHONPATH`; let `pytest.ini` own it. CI runs `test_layout.py` first so this fails in seconds |
| One app's config handed back for the other | The module name `rxconfig` is global to the suite and both app dirs are on `sys.path`. `import rxconfig` resolves by path order and caches the winner | Load it by file path — `tests/test_brujula_app.py::load_rxconfig` |
| A `reflex` process listening but answering nothing | Seen 30 Jul 2026: a dev server blocked on a futex for 2h45m, accepting TCP on 8000 and timing out on `/ping`, `/` and `/_event/` alike, while its frontend on 3000 served pages happily | Kill it. Symptom is a connection toast on a page that otherwise looks perfect |
| `make check` green, `reflex export` fails | `export` compiles the tree by importing the module in `app_module_import`. The suite never runs `export` | Run `reflex export --frontend-only --no-zip --env prod` from the app dir |
| CI fails on a committed `apps/<app>/requirements.txt` | `reflex init` seeds one holding only the reflex pin, and it shadows the root pins | It is gitignored; `tests/test_layout.py` enforces it. Delete it |

### Strava (Huella)

| What you see | Cause | Fix |
|---|---|---|
| Cannot register an API app at all | Standard Tier now needs an **active paid Strava subscription** (1 Jun 2026 new / 30 Jun 2026 existing) | A blocker on a person. Already-active developers were offered 3 months free by emailed code |
| `redirect_uri` rejected at authorize | Strava validates the **Authorization Callback Domain**, not a full URI | Set the domain to `huella.web.vespiridion.org`. `localhost` and `127.0.0.1` are whitelisted, so dev needs nothing extra |
| An athlete is locked out and must reconnect | A refresh invalidates the previous refresh token **immediately**. If a rotation was stored partially, the pair we hold is dead | `privacy.ensure_fresh` rotates under a per-session lock and re-checks inside it, so two concurrent callers spend one rotation; a pair whose session was evicted mid-flight is discarded rather than stored. If this still happens, that lock is the place to look |
| A disconnect leaves a live grant on Strava's side | `disconnect()` drops under the lock **before** it awaits the revoke, so our copy is always gone; a failed revoke is reported as unrevoked | Tell the athlete to remove the permission in their Strava account. The message already says so |
| The token endpoint looks wrong against the docs | `https://www.strava.com/oauth/token` is deliberate. Both it and `/api/v3/oauth/token` are live — measured, identical `400`s — and this one is on the host the 4 Jan 2027 base-URL migration does **not** move | Leave it. Do not "fix" it to match the docs |

---

## 2. Reading the trace instead of guessing

Both apps keep an append-only trace and both surface it. It answers most of §1 faster than any
log:

- `catalog.unavailable` — we could not look. Carries `status` and `rate_limited`. **Its absence
  is what makes "nothing available" an actual claim about stock.**
- `guardrail.buy_nothing` with `reason: inconclusive` — the app refusing to turn a failed read
  into a negative answer.
- `evidence.bundle` — `accepted`, plus every check as `pass` / `fail` / `not_run`. An
  `insufficient_evidence` answer with `blocking: []` means the bundle **accepted** with checks
  unrun, which is a different thing from a blocked answer and is the distinction that cost a
  debugging cycle on 30 Jul 2026.
- `privacy.rotated` / `privacy.disconnected` — Huella's credential transitions, carrying counts
  and never a token.

The rail in each app renders this live. `scripts/verify_brujula.py` reads the same events, and it
grants its one excuse **only** on `catalog.unavailable` with `rate_limited` — never on the shape
of the answer. If it ever excuses a run without that event, that is a regression in the verifier
and `tests/test_verify_brujula.py` should have caught it.

---

## 3. Things that look broken and are not

- **`product_type` is empty on 24 of 45 products, PACE 4 included.** Never key on it; `devices.py`
  exists because of this.
- **A handle is a URL slug and three of them lie outright.** `correa-de-nylon-de-24-mm-morada-para-apex-4-46-copia`
  is a 22 mm white silicone strap for the APEX 4 **42**.
- **UCP prices are 100× the feed's.** COP has no cents in practice; `money.py` is the only
  converter and the only path to human text.
- **`initialize` succeeds on COROS's UCP and every method needs the agent profile.** This diverges
  from DecaBot's Decathlon behaviour.
- **The `api_transformer`'s own `lifespan=` never runs.** Startup work goes through
  `app.register_lifespan_task`. A hook that silently does not run looks like a bug in whatever
  depended on it.
- **A Reflex `rx.State` *can* hold a dataclass.** The reason the conversation lives outside state
  is cost — every mutation is broadcast and pickled — not capability.

---

## 4. Fast checks

```bash
# is it up
curl -sI https://brujula.web.vespiridion.org/ping

# is the event channel up (no Origin, http/1.1 — both deliberate; see §1)
curl -s -D - -o /dev/null --http1.1 -N -m 5 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  'https://brujula.web.vespiridion.org/_event/?EIO=4&transport=websocket' | head -1

# which commit is live
ssh ubuntu@<vps> "docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \
  zot.web.vespiridion.org/coros/brujula:latest"

# both services, end to end, from the Ansible repo
ansible-playbook tests/health-check.yml

# is my checkout sane (one request per endpoint, and --no-coros if a measurement is running)
./.venv/bin/python scripts/doctor.py --no-coros
```

**Do not loop any COROS request.** Retrying a refusal is what keeps the limiter shut, and the
transport latches deliberately: inside the cooldown every call fails immediately without touching
the network. That latch is a feature.
