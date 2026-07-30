# Demo readiness

State as of 30 Jul 2026. Both apps are **live over TLS with valid certificates**.

| | Brújula | Huella |
|---|---|---|
| URL | <https://brujula.web.vespiridion.org> | <https://huella.web.vespiridion.org> |
| `/ping` | 200, cert valid | 200, cert valid |
| `/_event` | `wss://…/_event`, **no port**, 101 | same |
| Live data | **43 products read** from COROS | n/a — reads Strava, not the storefront |
| Gate | one shared password | same |
| Contrast, 1440 + 414 | 0 failures | 0 failures |
| Horizontal overflow at 414 | 0 px | 0 px |

---

## P0 — would break the demo

**None open.** One was found and fixed during this pass:

- ~~**Brújula served zero products in production.**~~ COROS's Cloudflare refused the
  `impersonate="chrome"` TLS fingerprint from the VPS's datacentre IP with `429
  local_rate_limited`, while serving `impersonate="safari"` the full 297 399 B from that same
  IP in the same minute. Every offline test and every local container probe passed, because a
  residential IP is served either way. Fixed by `IMPERSONATE_CHAIN`; verified live at 43
  products, 34 purchasable.

## P1 — visible, worth fixing before showing

- ~~**Brújula scrolled sideways at 414.**~~ `width: 100%` plus `margin: 0 1rem 1rem` on the
  audit rail; a margin is outside the width. Fixed, verified 0 px.

- **The agent's floor is refusal, not recovery.** On a request where retrieval surfaced two
  real purchasable products, `_decide` has no path from surviving candidates back to an
  answer and the turn ends in "no compres nada". Found by `scripts/eval_baseline.py`; the
  detail is in `docs/EVAL.md` §6. **Not fixed** — it is a design gap, not a bug, and changing
  `_decide` late is riskier than the honest refusal it currently gives.

- **An ambiguity becomes a buy-nothing.** `lookup_device_compat` raises `CaseUnspecified` and
  the guardrail fires, but nothing downstream preserves the question, so "which case?" — the
  correct answer — is lost. Also `EVAL.md` §6. Same reasoning for not fixing it now.

## P2 — record, do not fix

- **Reflex's own connection-error toast fails AA at 4.35:1.** Recorded rather than patched;
  overriding a framework component to fix a state the app should not be in is the worse
  trade. It also flashes briefly on every load before the socket settles.

- **`hu-shake` is defined in `huella.css` and applied by nothing.** One unused class.

- **Image size drifts with the base image.** Brújula went 238 → 269 MB with no app change,
  because `python:3.12-slim` moved. Not a pinned fact; rebuild before quoting a figure.

---

## What a demo cannot show

**The Strava round trip.** Registering the app needs an active paid Strava subscription
(~$11.99/mo), which is blocked on a human with a payment method. Everything up to the
redirect works and is verified in production: the callback route outranks the compiled
frontend's catch-all, an unknown `state` answers `303 → /?strava=state`, `POST` answers 405,
and an unknown path under `/oauth/strava/` answers 404.

**This is not a degraded mode.** With no credentials Huella renders *"Esta instancia no tiene
credenciales de Strava configuradas"* and falls back to asking the athlete directly — which is
the app's stated premise, that it says out loud why it is asking instead of deducing. The two
empty strings in the vault are what make it render that sentence instead of a Connect button
that cannot work.

---

## Still deferred, by decision

- `vault_jenkins_coros_ssh_key` and the matching GitHub deploy key — needed for *automated*
  redeploys, not to be live. Hand-push (`docs/DEPLOY.md` §4) is what put the current images
  in Zot.
- The Jenkins `POST /reload` after the JCasC change.
