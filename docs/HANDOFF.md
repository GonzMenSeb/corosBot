# Handoff — 30 Jul 2026

Written at the end of a long session for whoever picks this up. Read this, then `AGENTS.md`.

**State: 40 of 55 checklist tasks done, 4 partial, 11 untouched. 1259 tests passing.
17 commits on `corosBot` branch `spike-the-oauth-callback-route`, 4 on `vps-infrastructure`
branch `host-brujula-and-huella`. Nothing is pushed. No PR is open. Nothing is deployed.**

---

## 1. Read these first, in this order

1. **`AGENTS.md`** — canonical. The load-bearing-facts registry is 78 entries across 9 sections
   and every one was measured, not assumed. The sections most likely to bite you:
   `### The storefront classifies by TLS fingerprint`, `### The OAuth callback route`,
   `### Strava integration`. Also read the **maintenance contract** table before editing
   anything it names.
2. **`docs/DECISIONS.md`** — append-only. The two storefront entries on 30 Jul should be read
   as a pair: the first records measurements and *refuses* to conclude, the second concludes.
   That order is why the conclusion could be corrected twice.
3. **`docs/RUNBOOK.md`** — organised by symptom, because this stack's failures present as each
   other. "The page loads and does nothing" has four distinct causes.
4. **`docs/DEPLOY.md`** — §1 is the four things only Sebastian can do.
5. **`docs/QA-BRUJULA.md`** — what was audited and, more importantly, what was not.

---

## 2. Blocked on Sebastian — nothing below moves without these

| | What | Blocks |
|---|---|---|
| a | `ansible-vault edit vault.yml` → `vault_brujula_password`, `vault_huella_password`, `vault_strava_client_id`, `vault_strava_client_secret`, `vault_jenkins_coros_ssh_key`. **Do not add a Gemini key** — both apps reuse `vault_decabot_gemini_api_key` by decision | any deploy |
| b | GitHub deploy key on `corosBot` matching `vault_jenkins_coros_ssh_key` | both Jenkins jobs |
| c | Strava app at <https://www.strava.com/settings/api>, Authorization Callback Domain `huella.web.vespiridion.org`. **Now requires a paid Strava subscription** (~$11.99/mo). Already-active developers were offered 3 months free by emailed code | Huella's OAuth round-trip, and one whole success criterion |
| d | `curl -u admin:… -X POST https://jenkins.web.vespiridion.org/reload` after the Jenkins play | new JCasC jobs being visible |

`vault.yml.example` already lists all five as `CHANGE_ME`.

---

## 3. What is verified, and how

Do not re-verify these. Each cost real time and the evidence is recorded.

- **Brújula works on live COROS data.** `scripts/verify_brujula.py` → **10/10**. COROS PACE 4 at
  `$1.099.000`, total = sum of cards, no unbacked spec claims, buy-nothing reachable with zero
  cards. Separately driven: asking for a PACE Pro yields `advice_kind='not_sold_locally'` with
  **0 cards** and a reply that explicitly declines to substitute. **That is success criterion 3,
  met.**
- **The storefront is fingerprint-gated, not rate-limited.** `curl_cffi` with
  `impersonate="chrome"` reads 43 products (45 less two `gwp-hidden`). Prices $40.000–$2.099.000.
- **The privacy gate holds under attack.** 8 smuggling attempts, each built to violate exactly
  one rule and assert the specific reason; 12/12 mutations caught.
- **The OAuth serving shape.** `make spike-oauth` → 14/14 under granian with a real prod export.
- **Brújula's container.** 238 MB, `/ping` 200, compiled `/`, `healthy`, uid 1001 writes
  `.states`, one worker, no build tooling leaked, `/_event` 101 / 101 / **403** across
  no-Origin / allowed / foreign.
- **Brújula's gate passes AA at 1440 and 414**, measured in-browser from `getComputedStyle`.
- Both Ansible `--syntax-check` runs clean.

---

## 4. What to do next, in order

**Do this first — it is cheap and it unblocks a partial task.** Build and boot Huella's image.
`reflex export` now succeeds for Huella (that was the blocker), so `apps/huella/Dockerfile` can
finally be exercised. Brújula's is the template that passed:

```bash
docker build -f apps/huella/Dockerfile -t huella:probe .
docker run -d --name huella-probe -p 8001:8000 \
  -e HUELLA_PASSWORD=qa -e HUELLA_ALLOWED_ORIGINS=http://localhost:8001 \
  -e GEMINI_API_KEY=unset huella:probe
```
Then the five probes from `docs/QA-BRUJULA.md` §5. **Map host port to the port the bundle was
built with, or the client looks for a backend that is not there** — see the RUNBOOK's first row.

**Then, and this is the bulk of the remaining work: Huella's UI (Tasks 35, 36, 37).** Comparable
in size to Brújula's, which took five tasks. `huella/ui/theme.py` is done and its 353 lines of
measured tokens are the whole brief — read its docstring first. Hard constraints:
- **Strava attribution is mandatory and unmodifiable**: "Powered by Strava", the official
  "Connect with Strava" button, "View on Strava" links. Never recoloured, animated, or used as
  the app icon. `theme.STRAVA = "#FC5200"` — **corrected in session 28 from `#FC4C02`, which was
  a memory, not a measurement** — is declared on `SHEET` and on **nothing else**: it measures
  3.31:1 there and 2.9977:1 on `SHEET_2`, under the graphic floor, and there is deliberately no
  darkened variant because darkening the asset is what the brand terms forbid. The official
  marks are vendored byte-identical under `apps/huella/assets/strava/`.
- `STRAVA` and `FLAG` are 25.6° of hue apart at 1.28:1 — two dots that close are one dot, and
  **it is the contrast that says so, not the hue**. The guarantee is that **they never share a
  surface**.
- Red (`FLAG`) is the uncertainty flag and nothing else.
- Mirror `tests/test_brujula_ui.py`: walk `Component.render()`, not the component tree —
  `rx.match` renders its cases at construction and a tree walk loses every branch.

**Then** Task 32 (cold-start fallback — a connected account with two activities is not a signal),
Task 33 (`tests/test_huella_agent.py` + `scripts/verify_huella.py`), then the QA sweeps
(49, 50, 51), then docs (52, 54, 55).

**Task 52 needs re-scoping, not doing as written.** The criteria say "all 18 verified facts"; the
registry holds **78**. Cramming them into `test_contracts.py` would be worse than what exists —
most are already pinned by `test_catalog.py`, `test_ucp.py`, `test_gemini.py`, `test_devices.py`
and the Strava suites. The real gap: the registry does not *name* the test that pins each fact,
so a reader cannot tell which are protected. Add the citations; do not add facts.

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
- **Mutation-probe every suite that claims to pin something.** This caught two real holes in my
  own work: a privacy suite that passed with five of seven gate checks deleted (the attacks were
  over-determined — each broke three rules, so deleting one changed nothing), and a test that
  passed with its own check removed because its fixture lacked the field the mutant needed.

---

## 6. Traps that cost time today

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

---

## 7. Housekeeping left behind

- Scratch probes are in `/home/sebastian/.claude/jobs/b2697d8e/tmp/` and are **not** in the repo:
  `probe_oauth_contracts.py`, `probe_verifier.py`, `probe_privacy_suite.py`, `probe_ws_origin.py`,
  `attack_privacy.py`, `no_net.py`, `no_curl.py`. Several are worth re-creating if you touch what
  they cover; the privacy and verifier ones are described well enough above to rebuild.
- `curl-cffi==0.15.0` is pinned **exactly** on purpose: `"chrome"` is an alias for curl_cffi's
  newest bundled profile, so an upgrade silently moves the fingerprint target.
- **Nothing is running.** Verified at handoff: no containers, no images, no `granian` / `reflex` /
  `react-router` processes, and `:3000` / `:3001` / `:8000` / `:8001` all free.
- Three orphaned processes were cleaned up on the way out, and two of them are worth knowing about:
  - A Reflex **dev server hung on a futex** for 2h45m — accepting TCP on `:8000` and answering
    nothing on any path, while its frontend on `:3000` served pages normally. If `:8000` is busy
    and the app is unresponsive, suspect this shape again.
  - Two `granian` workers from `scripts/spike_api_transformer.py`, still serving from temp
    directories that had already been deleted. The script does terminate them in a `finally` —
    these survived because **I killed their parent shell**, with a `pkill -f "reflex.*brujula"`
    that matched my own command line. If you run the spike in a background shell, check for
    `spike.app:app` afterwards; and never `pkill -f` on a pattern your own invocation contains.
- `.claude-task-master/state.json` still says `status: "failed"` from the session that died on
  Task 26. It was not updated; `progress.md` is the accurate record.
