# Architectural Decisions

Append-only log of architectural and design decisions. **Never edit past entries.**

### 2026-07-29 · Two-app monorepo layout with shared core

Two agentic systems (Brújula + Huella) share a centralized core (`packages/coros_core/`) for
price conversion, device registry, catalog retrieval, and Gemini client. Each app is a standalone
Reflex 0.9.7 service with its own `rxconfig.py`, `app.py`, `State`, and UI token set. Both build
from `apps/<name>/Dockerfile` with build context = repo root, copy the shared core at build time.
Mirror DecaBot's monorepo absence of `docker-compose.yml` in the app repo; Compose is rendered
by Ansible on the host. `.web/` is cwd-relative to each app; use `PYTHONPATH=.:packages:apps/<name>`
for tests.

### 2026-07-29 · 100× price boundary in money.py

COROS storefront feed price = `"major"` (decimal string, COP units). UCP `get_product` price =
`amount` integer (minor, centavos). Conversion happens only in `money.py`; everything internal
stays minor units. `money.py:minor_to_display()` is the **only** path to human-readable COP.
This single boundary enforces the invariant: the model never sees a 100× inflation.
Enforced by source-scan test in `tests/test_money.py`.

### 2026-07-29 · Device registry determinism over product_type heuristics

PACE 4, APEX 4, NOMAD, VERTIX 2, and VERTIX 2S report empty `product_type`. Device matching
uses a hand-authored registry in `devices.py` keyed by product id/handle. Zero hallucination
surface. The model cannot invent a device; it can only ask which watch you have, then look it
up.

### 2026-07-29 · Strava tokens and activity data live outside rx.State

Huella privacy boundary: tokens and raw Strava payloads live in a module-level `_SESSIONS` dict
only, **never** in `rx.State`. The Reflex DISK state manager pickles all state vars to `.states/`;
putting a Strava token in state would write it to disk. One-way gate: activity data in → derived
`Requirement` out. Only typed `Requirement` fields reach the catalog path. `disconnect()` revokes
upstream and drops the session. No Strava field or token survives a restart.
Tested with an adversarial suite.

### 2026-07-29 · Reuse of DecaBot's Gemini key for both apps

One per-project Gemini quota, shared between Brújula and Huella. No public pool. Both apps read
`vault_decabot_gemini_api_key` (same credential as DecaBot). Rate limiting is inherent to Gemini
quota; the apps queue internally at the app level (no Traefik middleware, no per-IP queue).
