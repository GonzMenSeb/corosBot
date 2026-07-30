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
| Image built & booted? | **yes, verified** | **no — see §5** |

Both containers serve the page and the `/_event` websocket on **one** port. Huella also serves
`/oauth/strava/callback` there, as a Starlette route on Reflex's `api_transformer`. That needs
no extra Traefik label and no second router.

---

## 1. What only you can do

Four things are blocked on a human. Nothing else in the deploy is.

**a. Add the vault secrets.** `ansible-vault edit vault.yml` in `vps-infrastructure`, then add:

```yaml
vault_brujula_password: "…"        # the app's gate. EMPTY MEANS NO GATE — never on a public host
vault_huella_password: "…"         # same
vault_strava_client_id: "…"        # from the Strava app in (c)
vault_strava_client_secret: "…"    # from the Strava app in (c)
vault_jenkins_coros_ssh_key: |     # GitHub deploy key for corosBot; ONE key serves both jobs
  -----BEGIN OPENSSH PRIVATE KEY-----
  …
  -----END OPENSSH PRIVATE KEY-----
```

`vault.yml.example` already lists all five as `CHANGE_ME`, so diff against it if you want the
shapes. **Do not add a Gemini key** — both apps deliberately reuse
`vault_decabot_gemini_api_key`, which already exists. One key, one quota to watch.

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

# 2. trigger brujula-deploy (and huella-deploy once §5 is done) to populate Zot

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
docker buildx build \
  -f apps/brujula/Dockerfile \
  --provenance=false --sbom=false \
  --output type=image,name=zot.web.vespiridion.org/coros/brujula:latest,oci-mediatypes=true,push=true \
  .
```

**The build context is the repo root**, not the app directory — both images need
`packages/coros_core`, which is flattened to `/app/coros_core` so `import coros_core` reads the
same in the container as in the test suite.

**`oci-mediatypes=true` is not optional.** Zot rejects Docker v2 manifests with a `415` — and it
does so *after* every layer has finished uploading, so a plain `docker push` looks like it is
working right up until it isn't.

---

## 5. Huella's image does not exist yet

`apps/huella/Dockerfile` is written and **has never been built**. Its first line says so. The
app currently ships `huella/__init__.py`, `huella/strava/`, `huella/privacy.py`,
`huella/ui/theme.py` and `huella/agent/` — but **no `rxconfig.py` and no `app.py`**, and
`reflex export` compiles the tree by importing the module named in `rxconfig.py`, so there is
nothing for it to import.

What has to land first: `apps/huella/rxconfig.py` (`app_name="huella"`,
`app_module_import="huella.app"`, the localhost `api_url` default, `HUELLA_ALLOWED_ORIGINS`,
dev ports 3001/8001), `huella/app.py` with its module-level `app` and the OAuth callback route
on the `api_transformer`, `assets/huella.css`, and a re-exported `reflex.lock/` once the
component set is final. Then the same build and the same runtime probes Brújula passed.

Until then: run `ansible-playbook playbook.yml --tags huella` freely — it is safe, it just
prints the warning — but `huella-deploy` will fail at the build stage.

---

## 6. Environment variables

The authoritative list is `.env.example`. Two are deliberately absent from the rendered `.env`
and it is worth knowing why:

- **`BRUJULA_API_URL` / `HUELLA_API_URL`** — read by `rxconfig.py` at *compile* time and baked
  into the frontend bundle. A value in the `.env` reaches a bundle that was already built and
  does nothing. The baked `http://localhost:8000` is what lets one image serve every domain:
  the compiled client rewrites a same-domain host to `window.location.hostname` and upgrades
  `ws:`→`wss:`. Setting the real hostname needs one image per domain and buys nothing. The
  override exists for a dev tunnel only.
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
```

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
| Brújula image builds, boots, serves, healthy, uid 1001, one worker | **verified 30 Jul 2026** |
| Huella image | **never built** — §5 |
| Both Ansible roles, `--syntax-check` | clean |
| `dig` for either subdomain | returns nothing yet; the records are created by the roles |
| Brújula on live COROS data | **unverified** — the storefront is rate-limiting us; see `DECISIONS.md`, 30 Jul, "What refuses us at the storefront is not settled" |
| Huella OAuth round-trip | blocked on §1c |
| Anything pushed to GitHub or the VPS | **nothing** |
