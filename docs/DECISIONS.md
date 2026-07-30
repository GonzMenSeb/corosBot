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

### 2026-07-30 · The storefront gets a circuit breaker, not a retry ladder

The `products.json` limiter was measured rather than assumed, and the measurement inverts
DecaBot's policy. It tolerates roughly **4 requests over 20 s**, then answers **429 /
`Retry-After: 60` / `local_rate_limited`**. The hint is not honest and **retrying makes it
worse**: after one 429, thirty requests spaced 10 s apart were all refused across five
unbroken minutes; it cleared only after ~100 s of complete quiet, and a later lockout
outlasted 120 s. Mid-lockout the connection is sometimes dropped outright
(`ConnectTimeout`). A browser `User-Agent` was tested and ruled out as the discriminator.

So `catalog.py` diverges from DecaBot's storefront transport in two ways:

- **A 429 is never retried.** It latches, and every request inside a 90 s cooldown fails
  immediately as `CatalogUnavailable(rate_limited=True)` **without a network call**. Sending
  during a lockout is what sustains it, so the breaker protects the storefront from us and
  the turn from a 60 s stall. `retry_after` is reported for the trace panel, never slept on.
- **`SEM = Semaphore(1)`.** DecaBot allows 6 because it fetches ~24 collection feeds per
  turn. The whole COROS catalogue is one document, so the only source of a burst is several
  browser sessions at once — and a burst is the one thing measured to trip the limiter.
  Serialising costs a second session about a second; a 429 costs it the turn.

The retry ladder survives for `ConnectTimeout`, 5xx and a truncated body, which are the
failures a retry can actually fix, inside a 10 s total budget because a turn is waiting.

**Open, and deliberately not decided here:** `AGENTS.md` says catalog.py never caches and
retrieval is live every turn. Under this limiter, two turns inside a minute can legitimately
fail to reach the feed. The honest options are (a) surface it — "the storefront is
rate-limiting us", no fabricated inventory — or (b) a short-TTL snapshot with the fetch time
shown in the UI. This commit implements (a), which is the fail-closed direction and needs no
permission. Revisit when the agent loop lands and the real per-turn request count is known.

### 2026-07-30 · Untrusted catalog text is sanitized by element, not by tag

DecaBot's `strip_untrusted` replaces every tag with a newline and keeps what is between
them. Run against COROS that returns **pure CSS** for the two richest products:
`coros-pod-2` is 36 160 characters of BeeFree email template and `coros-apex-4` opens with a
CSS reset, so 400 characters of stylesheet reach the model and the real copy is truncated
away. `catalog.py` therefore removes the **content** of `style`, `script`, `head`,
`noscript`, `svg` and `template`, and an unclosed one swallows the remainder of the document
— the safe direction.

The alternative considered was a CSS-shaped segment filter. It was measured on the full
corpus — all 45 descriptions and all 30 articles, 1 931 segments — and rejected: precise
element removal leaves **zero** CSS behind, while the fuzzy filter dropped **22 segments of
real copy** (store hours, `@coroscolombia`, `www.coros.com.co`, a NIT). Precision comes from
knowing which elements are opaque, not from guessing which sentences look like code.

Second divergence, in the same function: **tags are stripped before unescaping.** One live
article contains `href="&gt;https://support.coros.com/…"`, and unescaping first turns that
`&gt;` into a real `>` that ends the tag early and spills the URL and a `style="color: rgb(…)"`
attribute into the prose. Roles and tags are re-checked afterwards, because `&lt;system&gt;`
is a role marker too. Injection patterns cover Spanish as well as English — the storefront is
Spanish — with zero false positives over the same 1 931 segments.

### 2026-07-30 · Gift-with-purchase products are excluded, and flagged rather than dropped

Two of the 45 products are dress shirts tagged `gwp-hidden`, in stock at $120.000:
`camisa-blanca-hombre` and `camisa-blanca-mujer`. Nothing in the feed marks them as
non-merchandise except that tag, and recommending one for a trail ultra is the cheapest
available embarrassment. `get_products()` excludes them by default — hence 43, not 45 — and
`include_hidden=True` returns them with `CatalogProduct.hidden` set, so the count discrepancy
is explainable without reading the source. A filter that empties the result raises
`CatalogUnavailable` rather than returning nothing, on the same principle as an empty feed:
"nothing is available" is an inventory claim, and we do not invent those.
