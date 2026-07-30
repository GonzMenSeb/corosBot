# Handoff — 30 Jul 2026

Written for whoever picks this up. Read this, then `AGENTS.md`. This replaces the earlier
handoff of the same day, which was written before the deploy and described a repo that no
longer exists — if you are reading a copy that opens "Nothing is pushed. No PR is open.
Nothing is deployed", you have the stale one.

**State: both apps are DEPLOYED and answering over TLS.**

- <https://brujula.web.vespiridion.org>
- <https://huella.web.vespiridion.org>

**48 of 55 checklist tasks done, 2 partial (45, 48), 5 open (49, 50, 51, 52, 55).
1605 offline tests passing at this commit, 26 deselected — the deselected ones are the `live`
marks `pytest.ini` excludes by default, and the total is climbing while the QA lanes land, so
re-run it rather than quoting this. 25 commits on `corosBot` branch
`spike-the-oauth-callback-route`; 4 commits plus an uncommitted working tree on
`vps-infrastructure` branch `host-brujula-and-huella`. Neither branch is pushed and no PR is
open: the deploy went out by hand down `docs/DEPLOY.md` §4, so the merge is still ahead of
you rather than behind you.**

`.claude-task-master/progress.md` still shows 42/5/8 and has not been updated for Tasks 32,
33, 35, 37, 39 or 54, all of which landed. Trust the line above and the git log over that
file. Its `state.json` also still says `status: "failed"` from the session that died on Task
26; that has been wrong all day.

---

## 1. Read these first, in this order

1. **`AGENTS.md`** — canonical. The load-bearing-facts registry is **85 entries across 10
   sections** and every one was measured, not assumed; the count moves as facts are added, so
   recount rather than quoting this number back. The sections most likely to bite you:
   `### The storefront classifies by TLS fingerprint, and calls it a rate limit` — which grew
   today and now describes a defect the old text could not have predicted — `### The OAuth
   callback route`, and `### Strava integration`. Also read the **maintenance contract** table
   before editing anything it names.
2. **`docs/DECISIONS.md`** — append-only, 37 entries. The two storefront entries on 30 Jul
   ("What refuses us at the storefront is not settled, and the record says so", then "It is the
   TLS fingerprint, and `curl_cffi` is the fix") should be read as a pair: the first records
   measurements and *refuses* to conclude, the second concludes. That order is why the
   conclusion could be corrected at all. **It was then corrected a third time, from production,
   and that correction is not in this file** — it lives in commit `8fe1da5` and in `AGENTS.md`'s
   fingerprint section, and it is the first thing Task 55 owes the log. Read the red/amber entry
   too: **the rule changed today.**
3. **`docs/DEPLOY.md`** — §1 is what only Sebastian can do; §7 is the first-run checks the
   deploy was closed with. Parts of §8 and its header table predate the deploy: if it still
   reads "Anything pushed to GitHub or the VPS — **nothing**" or "`dig` for either subdomain
   returns nothing yet", that row is older than the thing it describes.
4. **`docs/RUNBOOK.md`** — organised by symptom, because this stack's failures present as each
   other. "The page loads and does nothing" has four distinct causes.
5. **`docs/QA-BRUJULA.md`** — what was audited and, more importantly, what was not.
6. **`docs/VISUAL-BRIEF-HUELLA.md`** — the whole design instruction set, and the only complete
   statement of the amended red rule. §8 is a list of things not to do; it is there because
   each one was done once.

**The red rule was amended on 30 Jul 2026 and the old wording must not survive anywhere.**
Red in Huella means **"do not lean on what is on screen"** and covers three states:
`CONFIDENCE_COLOR["none"]`, `OUTCOME_COLOR["fail"]` and `LEVEL_COLOR["error"]`. Amber is
**"usable with reservations"**. A refusal arrived at correctly — `buy_nothing`,
`not_sold_locally` — is **uncoloured**, because a right answer is not a degraded one. The
superseded rule read "red is the uncertainty flag and nothing else"; it disagreed with the
app's own registries, `docs/VISUAL-BRIEF-HUELLA.md` §8 forbids restating it, and it survived
in this file's own hard-constraints brief for most of a day after the amendment shipped.
No token and no mapping changed — this was prose catching up to code.

---

## 2. Blocked on Sebastian

One of the original four is done, two are deferred on purpose, one is still a hard block. What
is left blocks *automation* and *Strava*, not serving — both apps are up without any of it.

| | What | Blocks | State |
|---|---|---|---|
| a | `ansible-vault edit vault.yml` → the app secrets | any deploy | **done.** All four COROS keys are in the encrypted vault. `vault_strava_client_id` and `vault_strava_client_secret` are the **empty string** and that is correct, not a stopgap — see the trap below and `DECISIONS.md`, "The pre-Strava vault value is `\"\"`". No Gemini key was added: both apps reuse `vault_decabot_gemini_api_key` by decision |
| b | GitHub deploy key on `corosBot` matching `vault_jenkins_coros_ssh_key` | both Jenkins jobs | **deferred on purpose** until after the first merge. Nothing before the merge needs it |
| c | Strava app at <https://www.strava.com/settings/api>, Authorization Callback Domain `huella.web.vespiridion.org`. **Requires a paid Strava subscription** (~$11.99/mo). Already-active developers were offered 3 months free by emailed code | Huella's OAuth round-trip, and one whole success criterion | **still blocked, and still on a human.** Everything else about Huella is built, tested and deployed without it |
| d | `curl -u admin:… -X POST https://jenkins.web.vespiridion.org/reload` after the Jenkins play | new JCasC jobs being visible | **deferred**, same reason as (b) |

---

## 3. What is verified, and how

Do not re-verify these. Each cost real time and the evidence is recorded.

**In production, over TLS, 30 Jul 2026** — the commands are `docs/DEPLOY.md` §7:

- **Both apps answer.** `/ping` **200** with a valid certificate on both hosts, and the
  `/_event` websocket upgrade returns **101** on both. Send `--http1.1`: Traefik negotiates h2
  with curl and the `Connection`/`Upgrade` handshake is HTTP/1.1-only, so over h2 a perfectly
  healthy granian answers `400 Invalid websocket upgrade`. Send **no** `Origin` either, as the
  Ansible health-check does: an origin the app does not allow correctly gets `403`, which
  **looks** like a broken deploy and isn't. In a container the same triple reads 101 / 101 /
  **403** across no-Origin / allowed / foreign.
- **Huella's OAuth callback outranks the compiled frontend's catch-all in production**:
  `?state=garbage` → **303** to `/?strava=state`, `POST` → **405**, `/oauth/strava/nope` →
  **404**. That triple is the one production check `make spike-oauth` cannot substitute for: a
  **404** on the first probe is the real failure mode, and it only exists in a built image
  behind a proxy. It needs no Strava credentials at any point.
- **Images are in Zot as `:latest` *and* `:<sha>`**, each carrying
  `org.opencontainers.image.revision`. Both halves are load-bearing: Jenkins' Deploy stage
  reads that label off the **running** image to compute `PREV_SHA`, and then pulls `:$PREV_SHA`
  — so a build that pushed only a moving `:latest` would leave the first automated rollback
  with nothing to fall back to. Push both tags from **one** build so the digest is identical.
  `oci-mediatypes=true` and `--provenance=false --sbom=false` are not optional: Zot rejects
  Docker v2 manifests with a `415` — *after* every layer has finished uploading, so a plain
  `docker push` looks like it is working right up until it isn't — and it will not take
  buildx's attestation manifest list either.

**Offline and in containers:**

- **`./scripts/smoke_containers.sh` — 37/37 on both apps**, OAuth triple included. This is the
  probe list from `QA-BRUJULA.md` §5, which used to be retyped by hand every session. Read its
  header before trusting a hand-rolled equivalent: two of its checks are easy to write so that
  they report a pass. `/ping` answers the JSON string `"pong"` **with the quotes**, and the
  runtime image has **no `ps`** — that being the point of the two-stage build — so
  `docker exec … ps | grep -c granian` returns 0 on a *healthy* container and reads as "no
  workers". Worker counting goes through `docker top`, which uses the host's `ps`.
- **Brújula works on live COROS data.** `scripts/verify_brujula.py` → **10/10**. COROS PACE 4
  at `$1.099.000`, total = sum of cards, no unbacked spec claims, buy-nothing reachable with
  zero cards. Separately driven: asking for a PACE Pro yields `advice_kind='not_sold_locally'`
  with **0 cards** and a reply that explicitly declines to substitute. **That is success
  criterion 3, met.**
- **The privacy gate holds under attack.** 8 smuggling attempts, each built to violate exactly
  one rule and assert the specific reason; 12/12 mutations caught.
- **The OAuth serving shape.** `make spike-oauth` → 14/14 under granian with a real prod export.
- **Brújula's gate passes AA at 1440 and 414**, measured in-browser from `getComputedStyle`.
  `scripts/contrast_walk.js` is that audit as one payload, with a WCAG implementation
  deliberately **independent of `theme.py`'s helper** — the unit suites recompute with the same
  helper the theme uses, so a helper bug moves the measurement and the expectation together and
  stays invisible. It composites `rgba` layers in paint order rather than hunting for the first
  opaque ancestor, because this palette puts real translucent tokens between text and surface.
- **The Jenkinsfile parses** under `groovy:4-jdk17`, and the two contracts a parse cannot see —
  that `ON_MAIN` gates Build/Deploy/Health but **not** Test, and that rollback's three moving
  parts are all present — are pinned in `tests/test_layout.py`.
- Both Ansible `--syntax-check` runs clean.

---

## 4. What to do next, in order

**1. Finish the QA sweeps (Tasks 49, 50), then fold what they find back into the docs.** This
is the active work and it was in flight when this was written — look for `docs/QA-HUELLA.md`
and `docs/screenshots/huella-{1440,414}.png` before assuming Huella's sweep has not run.
`docs/VISUAL-BRIEF-HUELLA.md` §9 is the protocol and §10 is the acceptance list. Brújula's
sweep (Task 48) audited the gate at both widths and found two defects, but **everything behind
the gate was unaudited** because there was no catalogue at the time: no cards, no advice
panels. There is one now, so the second pass is the one that counts.

**2. Then Task 51** — `tests/test_integration.py`, the cross-seam regression suite built from
every bug the sweeps find. It has nothing to pin until they run, which is why it is second.

**3. Then Task 52, re-scoped — not as written.** The criteria say "all 18 verified facts"; the
registry holds 85. Cramming them into `test_contracts.py` would be worse than what exists —
most are already pinned by `test_catalog.py`, `test_ucp.py`, `test_gemini.py`,
`test_devices.py`, `test_layout.py` and the Strava suites. The real gap: the registry does not
systematically *name* the test that pins each fact, so a reader cannot tell which are
protected. Add the citations; do not add facts.

**4. Then Task 55** (`DECISIONS.md` completeness) and the doc sweep. `DEPLOY.md`'s §8 table and
its header both predate the deploy and assert things that are no longer true.

**5. Then one PR, and only then Jenkins.** Push, open the PR, merge — and *after* the merge do
blockers (b) and (d): the deploy key, the Jenkins play, the `/reload`. They are deferred rather
than forgotten. `vps-infrastructure` has an uncommitted working tree (`vault.yml`,
`vault.yml.example`, both `env.j2` templates); commit that before you touch anything else there.

**6. Strava, last and self-contained.** Blocked on (c). When the subscription exists, Phase 6 is
replacing two empty strings in the vault and nothing else — `redirect_uri` is already templated
from `huella_host`, and the callback route is already proven in production.

---

## 5. How to work on this

Parallelise with subagents — it worked well and the seams that matter are known:

- **One lane per file set, and freeze the seam before you fan out.** The lanes that went cleanly
  owned one directory each. The one collision risk is `tests/` — give each lane its own test file
  by name.
- **Do not let a lane commit.** Have it leave unstaged changes and review before committing. Two
  lanes produced work that needed correcting, and one died mid-report with its work already
  complete and green — which you only discover by checking rather than by waiting.
- **Verify a lane's claims yourself, at a different boundary than it used.** Concretely: the
  theme's ratios were recomputed with an *independent* WCAG implementation, because its own suite
  uses the same helper the theme does and a helper bug would be invisible. The container was
  rebuilt rather than trusted. The "no network" claim was checked by blocking `curl_cffi` and
  `Curl.perform` — **a socket patch does not stop libcurl** — and the block itself was tested
  first, because a block that does not block proves nothing.
- **Mutation-probe every suite that claims to pin something, and run the probe on the shipped
  tree.** This caught four real holes today: a privacy suite that passed with five of seven gate
  checks deleted (the attacks were over-determined — each broke three rules, so deleting one
  changed nothing); a test that passed with its own check removed because its fixture lacked the
  field the mutant needed; the `PREV_SHA` substring below; and a catalog test whose *premise*
  was wrong rather than its arithmetic.
- **An adversarial verifier is worth more than another builder.** Three of those four came from
  someone breaking the code and watching the suite stay green, not from someone reading it. The
  fourth came from production, which is the expensive way to find out.

---

## 6. Traps that cost time today

- **The fingerprint that rescues you from a laptop is the one that damns you in production.**
  Brújula served zero products from the VPS while every offline test, every local container
  probe and the whole suite passed. Measured from inside the running container on
  148.113.172.15, minutes apart, same storefront: plain `urllib` **200**; `curl_cffi` with no
  impersonation **403**; `impersonate="chrome"` **429 `local_rate_limited`**;
  `impersonate="safari"` **200**. The IP is not blocked — two clients read the whole document
  from it in the same minute. What is refused is the *pair* (ClientHello, ASN): a Chrome
  handshake arriving from an OVH datacentre is less plausible than plain libcurl from one, so
  the *better* impersonation scores worse there. **From a residential IP `"chrome"` returns 200
  and the bug is invisible — the profile can only be tested where it fails.** That is why
  `IMPERSONATE_CHAIN` is an ordered chain retried one request per profile, promoted on success,
  overridable by `COROS_IMPERSONATE`, and why the 429 cooldown no longer latches until *every*
  profile has been refused. A fallback emits `catalog.fingerprint_fallback`: a silent one means
  nobody notices the head of the chain is dead until the spare dies too.
- **A test that pins a substring can be satisfied by a nearby echo.** The Jenkinsfile rollback
  test asserted `"env.PREV_SHA" in body`. Renaming only `env.PREV_SHA = sh(` to
  `env.PREVIOUS = sh(` leaves the variable never computed and rollback silently dead — and the
  suite reported **32 passed**, because the substring survives two lines below in
  `echo "Currently live: ${env.PREV_SHA ?: …}"`. It pinned its own documentation. It now matches
  the *assignment*, checks the label is read by **that** `sh()`, and checks it inspects
  `:latest` — inspecting `:$GIT_SHA` would report the revision being deployed, so every rollback
  would target the build that just failed. Same family as the smoke checks that were easy to
  write so they report a pass: **ask what the check does when the thing is broken, not when it
  works.**
- **Documentation is not a measurement.** I rewrote the registry to say Strava's token endpoint
  was wrong, on the strength of the docs plus a forum thread, and told a lane to change it. Two
  POSTs showed both endpoints are live and identical. The repo's rule — read the fixture, run the
  call, or read the source — excludes docs and forums.
- **A transplanted measurement is not a measurement either.** `catalog.py`'s entire transport
  design rested on "the storefront refuses reused connections", which was measured against
  **Decathlon** and never reproduced here. It is false for COROS: pooling is safe, verified by two
  GETs from the same local port.
- **`local_rate_limited` in a 429 body does not mean rate limited.** Cloudflare says that to a
  fingerprint it does not like, forever.
- **Probing a limiter spends the budget the verification needs.** Each experiment cost a window;
  the decisive one needed 30 minutes of touching nothing. If you must measure, measure once.
- **A verifier that excuses failures on the shape of the answer verifies nothing.**
  `verify_brujula.py` ran green through five merged PRs while blaming a lockout it never observed.
  It now requires the `catalog.unavailable` event with `rate_limited`.
- **A placeholder is only safe where the code tests for the placeholder, and this codebase tests
  for truthiness.** `vault_strava_client_id: "CHANGE_ME"` makes `is_configured()` return **True**,
  so Huella renders the official "Connect with Strava" button and sends the person to Strava with
  `client_id=CHANGE_ME` — the failure lands in the vendor's UI after they have already committed
  to the action. `""` makes it False and Huella says plainly that this instance has no Strava
  credentials. And **absent is not empty**: `env.j2` references both vars unconditionally under
  `StrictUndefined`, so deleting a line kills the play at Jinja render with an error that names a
  variable and nothing else, which reads as a templating fault and starts the search in the wrong
  file.

---

## 7. Housekeeping left behind

- **In production**: both containers are up and serving. Nothing was left running *locally* that
  matters — `:3000` / `:3001` / `:8000` / `:8001` are free, no `granian` / `reflex` /
  `react-router` processes, and no `brujula`/`huella` containers. The `brujula:probe` and
  `huella:probe` images (269 MB / 270 MB) are still in the local cache, as is a
  `buildx_buildkit_coros-builder0` container — the buildx builder outlives the push that created
  it, and it is the one thing here that will still be running tomorrow.
- **Image size is not a pinned fact.** `python:3.12-slim` is 119 MB of the total and moved under
  us between builds — Brújula went 238 → 269 MB with no application change. Rebuild before
  quoting a figure.
- `curl-cffi==0.15.0` is pinned **exactly** on purpose: `"chrome"` is an alias for curl_cffi's
  newest bundled profile, so an upgrade silently moves the fingerprint target. That mattered more
  than anyone expected — see the first trap.
- Scratch probes from the earlier session are in `/home/sebastian/.claude/jobs/b2697d8e/tmp/` and
  are **not** in the repo: `probe_oauth_contracts.py`, `probe_verifier.py`, `probe_privacy_suite.py`,
  `probe_ws_origin.py`, `attack_privacy.py`, `no_net.py`, `no_curl.py`. The two kinds of probe
  that were worth keeping now exist as repo scripts instead — `scripts/smoke_containers.sh` for
  the container checks and `scripts/contrast_walk.js` for the in-browser audit — which is the
  pattern to follow: a probe worth re-running twice belongs in `scripts/`, not in a tmp dir. The
  privacy and verifier ones are described well enough above to rebuild.
- **Never `pkill -f` on a pattern your own invocation contains.** Two `granian` workers from
  `scripts/spike_api_transformer.py` outlived their `finally` because a
  `pkill -f "reflex.*brujula"` matched the shell that owned them. A Reflex dev server was also
  found **hung on a futex** for 2h45m — accepting TCP on `:8000`, answering nothing on any path,
  while its frontend on `:3000` served pages normally. If `:8000` is busy and the app is
  unresponsive, suspect that shape again.
- `.claude-task-master/state.json` still says `status: "failed"` from the session that died on
  Task 26. It was never updated, and `progress.md` is now stale too — see the note at the top.
