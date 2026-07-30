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

### 2026-07-30 · Import roots declared in pytest.ini, not per caller

Supersedes the "use `PYTHONPATH=.:packages:apps/<name>` for tests" note in the monorepo-layout
entry above. `pythonpath = . packages apps/brujula apps/huella` in `pytest.ini` is now the only
declaration; no runner exports `PYTHONPATH` for a pytest command. The workflow copied from
DecaBot carried `PYTHONPATH=.` — correct for a single-app repo, one root short of ours — so CI
on PR #1 died at collection with `ModuleNotFoundError: No module named 'coros_core'` while
`make check` was green. Two copies of a path list, one of them partial. `make check`/`make
verify` now invoke pytest exactly as CI does, so local green means CI green. `PYPATH` survives
in the Makefile for non-pytest entry points (`doctor`, `fixtures`); the Dockerfiles are
untouched. Enforced by `TestPytestIniIsTheOnlyPlaceTheImportRootsAreDeclared` in
`tests/test_layout.py`.

### 2026-07-30 · One generic `rpc()` under `call_ucp()`, and no handshake

`packages/coros_core/ucp.py` exposes two functions: `call_ucp(tool, args)` for `tools/call`
and `rpc(method, **params)` for everything else. DecaBot needed only the former, because
Decathlon's `initialize` and `tools/list` fail unconditionally. Probing COROS live showed the
opposite: **both succeed**, and the single reason is the agent profile — every method on this
endpoint returns `-32001 invalid_profile_url` without `params.arguments.meta["ucp-agent"].profile`,
and no other placement (`params.meta`, top-level `meta`, `profile_uri`, four header spellings)
works. So `tools/list` is sent with an `arguments` key it has no arguments for. The plan for
this module and the first draft of the facts registry both recorded `initialize` as failing;
that reading came from testing it without the profile. Registry corrected in the same commit,
pinned by `tests/test_ucp.py::test_initialize_and_tools_list_both_succeed_once_the_profile_is_passed`.

Consequences worth naming:

- **The body is parsed before `raise_for_status()`.** COROS returns JSON-RPC errors on HTTP
  **422** (rejected profile) and **403** (unknown tool name), where DecaBot's endpoint did not,
  so DecaBot's status-then-body order would convert every typed `UcpToolError` into an
  untyped `httpx.HTTPStatusError` and lose the diagnostic.
- **A typo'd tool name reads as `-32000 AuthenticationRequired`.** Verified that the real
  tools — `create_cart` included — answer with no credential, so a 403 is a naming bug.
- **No `initialize()` or `list_tools()` wrapper.** Neither has a production caller; a wrapper
  with no caller is dead code. `scripts/dump_fixtures.py` and `scripts/doctor.py` will reach
  `tools/list` through `rpc()`, which keeps them inside the rate limiter and the error
  taxonomy. The "only `ucp.py` posts to the endpoint" boundary is enforced by an AST scan.
- **Pacing is DecaBot's policy, not a measured COROS limit.** `Semaphore(8)`, and the first
  429 latches the process into serialised 1.5 s spacing for good. The latch never releases
  on a success, because a single call is served throughout a lockout. Simulated offline —
  inducing a real lockout to test it would cost the demo, which is the whole point.
