# Deploying Brújula and Huella

Two apps, one repo, two images, one VPS. Everything on the host side lives in
`/home/sebastian/versioned-code/vps-infrastructure` (branch `host-brujula-and-huella` as of
30 Jul 2026 — **not yet merged, not yet pushed**).

| | Brújula | Huella |
|---|---|---|
| URL | `https://brujula.web.vespiridion.org` | `https://huella.web.vespiridion.org` |
| Image | `zot.web.vespiridion.org/coros/brujula:latest` | `zot.web.vespiridion.org/coros/huella:latest` |
| Ansible role | `roles/brujula/` | `roles/huella/` |
| On-host dir | `/opt/brujula/` | `/opt/huella/` |
| Jenkins job | `brujula-deploy` | `huella-deploy` |
| Container port | 8000 | 8000 |
| Dev ports | 3000 / 8000 | 3001 / 8001 |
| Image built & booted? | **yes** — 269 MB | **yes** — 270 MB |

Both containers serve the page and the `/_event` websocket on **one** port. Huella also serves
`/oauth/strava/callback` there, as a Starlette route on Reflex's `api_transformer`. That needs
no extra Traefik label and no second router.

---

## 1. What only you can do

Four things are blocked on a human, but **only (a) is on the critical path to getting the apps
up.** (b) and (d) are needed for *automated* redeploys, not for the first deploy — the by-hand
push in §4 needs only `vault_zot_registry_password`, which already exists, and
`playbook.yml --tags brujula,huella` never touches the jenkins role. (c) is Strava, and nothing
else depends on it.

**a. Add the vault secrets.** `ansible-vault edit vault.yml` in `vps-infrastructure`, then add:

```yaml
vault_brujula_password: "<chosen>"   # the app's gate. EMPTY MEANS NO GATE — never on a public host
vault_huella_password: "<chosen>"    # same
vault_strava_client_id: ""           # EMPTY STRING — see below. Not absent, not CHANGE_ME
vault_strava_client_secret: ""       # EMPTY STRING — same
```

**The empty strings are not placeholders. They are the correct pre-Strava state, and
`""` is not the same as leaving the key out.**

- **Omitting them fails the play.** `roles/huella/templates/env.j2` references both
  unconditionally and `error_on_undefined_vars` is on, so an absent key dies at Jinja render
  with an error that names *templating* — it will not mention Strava, and you will look in the
  wrong place. Rendered both ways against the real template with `StrictUndefined`,
  30 Jul 2026: empty strings give `STRAVA_CLIENT_ID=` and a clean file; omitting the key gives
  `UndefinedError: 'vault_strava_client_id' is undefined`, and that string is the whole of the
  error you get.
- **`""` is the right value, not a stopgap.** `strava/client.is_configured()` is
  `bool(client_id() and client_secret() and redirect_uri())`, so `""` → `False` → `connect.py`
  renders the "no credentials configured" sentence instead of a Connect button that cannot
  work. No dead button is ever offered. Replace the two strings when §1c lands; nothing else
  changes.

`vault_jenkins_coros_ssh_key` is the fifth key and is **deliberately deferred** to after the
first merge — it pairs with the deploy key in (b) and neither is needed to get the apps live:

```yaml
vault_jenkins_coros_ssh_key: |     # GitHub deploy key for corosBot; ONE key serves both jobs
  -----BEGIN OPENSSH PRIVATE KEY-----
  …
  -----END OPENSSH PRIVATE KEY-----
```

`vault.yml.example` already lists all five as `CHANGE_ME`, so diff against it if you want the
shapes — but note that `CHANGE_ME` is the wrong *value* for the two Strava keys, for the reason
above. **Do not add a Gemini key** — both apps deliberately reuse
`vault_decabot_gemini_api_key`, which already exists. One key, one quota to watch.

**Share the two gate passwords when you set them.** QA cannot reach a single state behind the
login box without them, so a deploy that is otherwise entirely green still stalls.

**b. Add the GitHub deploy key.** The public half of `vault_jenkins_coros_ssh_key` goes on the
`corosBot` repo (Settings → Deploy keys, read access is enough). Without it both Jenkins jobs
fail at checkout.

**c. Register the Strava app** — <https://www.strava.com/settings/api>.
- Set **Authorization Callback Domain** to `huella.web.vespiridion.org`. Strava validates the
  redirect against the *domain*, not a full URI, and whitelists `localhost` and `127.0.0.1` —
  so this one entry covers local development too.
- **This now requires an active paid Strava subscription** (~$11.99/month; 1 Jun 2026 for new
  developers, 30 Jun 2026 for existing ones). Developers who were already active were offered
  three months free by emailed code — worth checking your inbox before paying.
- Until this exists, Huella's OAuth cannot be exercised and `tests/test_strava.py`'s five
  `live` tests skip rather than fail. Everything else about Huella is built and tested without
  it.

**d. Reload Jenkins after the JCasC change.** New jobs are written to disk *after* Jenkins has
built its item map, so they exist on disk and 404 in the UI and API until it re-reads them:

```bash
curl -u admin:<password> -X POST https://jenkins.web.vespiridion.org/reload
```

Existing jobs update in place and are unaffected. This is recorded in `casc.yml.j2` around
L80-85 and was observed on `decabot-deploy` on 29 Jul 2026.

---

## 2. Order of operations

Ansible first, Jenkins second, images third, and the app roles last. The first run of the app
roles before the images exist is **safe by design** — the pull is deliberately tolerant — and
will create the directory, the `.env`, the compose file and the DNS record, then print a
warning instead of starting anything.

```bash
cd /home/sebastian/versioned-code/vps-infrastructure

# 0. all of §1 first — the templates will not render without the vault keys
ansible-playbook playbook.yml --syntax-check          # must be clean
ansible-playbook jenkins-playbook.yml --syntax-check  # must be clean

# 1. Jenkins gets the two jobs and the credential
ansible-playbook jenkins-playbook.yml
curl -u admin:<password> -X POST https://jenkins.web.vespiridion.org/reload   # §1d

# 2. trigger brujula-deploy and huella-deploy to populate Zot — or push by hand, §4

# 3. the app roles: dir, .env, compose, DNS, pull, up
ansible-playbook playbook.yml --tags brujula,huella

# 4. prove it
dig +short brujula.web.vespiridion.org     # expect 148.113.172.15
dig +short huella.web.vespiridion.org      # expect 148.113.172.15
ansible-playbook tests/health-check.yml    # green for both
```

Run `ansible` from the repo root — `ansible.cfg` points at `.vault_pass` **relatively**.

---

## 3. What the roles put on the host

Per app, under `/opt/<svc>/`:

| Path | Owner / mode | What it is |
|---|---|---|
| `.env` | `root:ubuntu` `0640` | Rendered from `templates/env.j2`. **Templated, never copied** |
| `docker-compose.yml` | `root:ubuntu` `0644` | One service, the `proxy` network, six Traefik labels |
| `states/` | `1001:1001` `0755` | Bind-mounted to `/app/.states`; Reflex's on-disk session state |

`states/` being owned by `1001:1001` is a **two-way contract with the Dockerfile**, whose user
is uid 1001. Brújula's image has been checked against it and matches; a mismatch shows up as a
silently unwritable state directory rather than an error.

The compose file declares **no healthcheck**. The image already declares one against `/ping`,
and a second copy is only something to drift.

There is **no middleware** on either router. Access control is the app's own password gate,
enforced in Python inside every handler that spends a Gemini call. For Huella a middleware
would additionally break Strava's redirect, which arrives as an unauthenticated top-level
navigation.

---

## 4. Building and pushing by hand

Jenkins does this on every merge to `main`. To do it yourself:

```bash
cd /home/sebastian/versioned-code/corosBot
APP=brujula                              # or huella
SHA=$(git rev-parse --short HEAD)
docker buildx build \
  -f apps/$APP/Dockerfile \
  --provenance=false --sbom=false \
  --label org.opencontainers.image.revision=$SHA \
  --output type=image,"name=zot.web.vespiridion.org/coros/$APP:$SHA,zot.web.vespiridion.org/coros/$APP:latest",oci-mediatypes=true,push=true \
  .
```

**The build context is the repo root**, not the app directory — both images need
`packages/coros_core`, which is flattened to `/app/coros_core` so `import coros_core` reads the
same in the container as in the test suite.

**`oci-mediatypes=true` is not optional.** Zot rejects Docker v2 manifests with a `415` — and it
does so *after* every layer has finished uploading, so a plain `docker push` looks like it is
working right up until it isn't. `--provenance=false --sbom=false` belong to the same problem:
buildx otherwise pushes an attestation manifest list Zot will not take either.

**The `:$SHA` tag and the revision label are what make rollback exist**, and neither is
cosmetic. Jenkins' Deploy stage reads `org.opencontainers.image.revision` off the *running*
image to compute `PREV_SHA`, then pulls `:$PREV_SHA` to roll back to. A hand-push of `:latest`
with no label leaves that read empty, so the first automated deploy has nothing to fall back
to — and you find out during the incident, which is the worst time to find out. Push both tags
from one build so the digest is identical; two builds can differ.

---

## 5. Rolling back

```bash
ssh ubuntu@<vps>
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
  zot.web.vespiridion.org/coros/<app>:latest        # the SHA currently serving
docker pull zot.web.vespiridion.org/coros/<app>:$PREV_SHA
docker tag  zot.web.vespiridion.org/coros/<app>:$PREV_SHA \
            zot.web.vespiridion.org/coros/<app>:latest
cd /opt/<app> && docker compose up -d
```

This works **only because §4 pushes `:$SHA` alongside `:latest`**. A registry holding nothing
but a moving `:latest` has no previous revision to name, which is the entire reason the label
and the second tag are mandatory rather than tidy.

**Before the first successful deploy there is no rollback target at all.** Not a degraded one
— none. The remedy in that window is `docker compose down`, after which Traefik returns 404 for
a subdomain nobody has been given yet. That is the honest answer; do not build blue/green to
avoid saying it. The window closes the moment §4 has run once.

Jenkins does this automatically when its Health stage fails. If its log says there is **no
labelled previous revision**, the live image predates the pipeline and the rollback above is
manual.

---

## 6. Environment variables

The authoritative list is `.env.example`. Two are deliberately absent from the rendered `.env`
and it is worth knowing why:

- **`BRUJULA_API_URL` / `HUELLA_API_URL`** — read by `rxconfig.py` at *compile* time and baked
  into the frontend bundle. A value in the `.env` reaches a bundle that was already built and
  does nothing. The baked value — `http://localhost:8000` for Brújula, `http://localhost:8001`
  for Huella — is what lets one image serve every domain: the compiled client rewrites a
  same-domain host to `window.location.hostname` and upgrades `ws:`→`wss:`. **On https it also
  drops the port**, which is why Huella's bundle, built against `:8001`, reaches a container
  listening on `:8000` without either number appearing anywhere. Over plain http the port
  survives, and that is the whole of the local-smoke port rule in §7. Setting the real hostname
  needs one image per domain and buys nothing. The override exists for a dev tunnel only.
- **A second Gemini key** — see §1a.

Two that *are* in the `.env` and are load-bearing:

- **`<APP>_PASSWORD`** — `state.py` computes `GATE_ON = bool(this)`. **Unset means no gate.**
- **`<APP>_ALLOWED_ORIGINS`** — narrows `cors_allowed_origins`. Reflex defaults it to `("*",)`
  and pairs it with `allow_credentials=True`, which mirrors *any* origin back; it also feeds
  the socket.io server, so it gates the `/_event` handshake too. Measured under granian: with
  it narrowed, no `Origin` gives `101`, the allowed origin gives `101`, and a foreign origin
  gets `403`. It **replaces** the default rather than adding to it — a hosted instance that
  still trusted `localhost` would trust every other container on the box.

---

## 7. First-run checks

```bash
curl -sI https://brujula.web.vespiridion.org/ping        # 200, valid TLS
curl -s -D - -o /dev/null --http1.1 -N -m 3 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  'https://brujula.web.vespiridion.org/_event/?EIO=4&transport=websocket' | head -1
# expect: HTTP/1.1 101 Switching Protocols

# …and the same two against huella.web.vespiridion.org.

# Huella only: the OAuth route, which needs NO Strava credentials
curl -sI "https://huella.web.vespiridion.org/oauth/strava/callback?state=garbage"  # 303
curl -sI -X POST https://huella.web.vespiridion.org/oauth/strava/callback          # 405
curl -sI https://huella.web.vespiridion.org/oauth/strava/nope                      # 404
```

**The unknown-state probe is the one production check `make spike-oauth` cannot substitute
for.** An unrecognised `state` fails `consume_state` and answers `303 → /?strava=state`, with
no credentials involved at any point. A **404** there is the real failure: it means the
compiled frontend's catch-all mount swallowed the path and the callback route never ran. That
shape only exists in a built image behind a proxy, which is exactly what the offline spike
cannot reproduce. A `405` on POST proves the route matched and rejected the method — a 404
there would mean the same swallowing.

**`--http1.1` is load-bearing.** Traefik negotiates h2 with curl, and the
`Connection`/`Upgrade` handshake is HTTP/1.1-only — over h2 granian answers
`400 Invalid websocket upgrade` on a perfectly healthy app. Send **no** `Origin` header, as the
Ansible health-check does: an origin the app does not allow gets `403`, which would look like a
broken deploy and isn't.

`tests/health-check.yml` runs all of this for both apps, and every task is guarded on the image
being present, so it stays green before the images exist.

---

## 8. Known-good and known-not-yet

| | State |
|---|---|
| Both images build, boot, serve, healthy, uid 1001, one worker, no build tooling | **verified 30 Jul 2026 — `./scripts/smoke_containers.sh`, 37/37**, Brújula 269 MB, Huella 270 MB |
| Huella's OAuth route outranks the frontend mount, in a real image | **verified** — `?code&state` 303, `?state=garbage` 303 → `/?strava=state`, POST 405, unknown path 404 |
| `/_event` 101 / 101 / **403** across no-Origin / allowed / foreign, both apps | **verified**, same run |
| Image size drifts with the base, and is not a pinned fact | `python:3.12-slim` is 119 MB of the total and moved under us between builds (238 → 269 MB for Brújula with no app change). Rebuild before quoting a figure |
| Both Ansible roles, `--syntax-check` | clean |
| `dig` for either subdomain | returns nothing yet; the records are created by the roles |
| Brújula on live COROS data | **verified** — `scripts/verify_brujula.py` 10/10. The storefront refuses us by TLS *fingerprint*, not rate limit; `curl_cffi` with `impersonate="chrome"` reads 43 products. See `DECISIONS.md`, 30 Jul |
| Huella OAuth route in production | not yet — the unknown-state probe in §7 needs no Strava credentials and is the check that closes it |
| Huella OAuth **round-trip** | blocked on §1c (paid Strava subscription) |
| Anything pushed to GitHub or the VPS | **nothing** |
