# AGENTS.md — COROS Agent Systems

Canonical instructions for any AI assistant working in this repo.
**Two agentic systems:** Brújula (B1 — product advisor) and Huella (B2 — training advisor).

## What this is

A monorepo hosting two **conversational COROS agents** that reason from fitness needs.

**Brújula** takes a natural-language description of a sport or activity, researches real
conditions, asks targeted questions, derives the gear the activity requires, retrieves real
products from COROS's live Colombian storefront, resolves each to an in-stock strap option,
presents them with prices in COP, and — **only on an explicit human click** — creates a cart
link for manual checkout. Brújula's unique constraint: it must report when a product is not
sold in Colombia, rather than substituting. The flow **ends at a cart link**. We never
automate payment, and the COROS experience dictates our stopping point.

**Huella** connects to a user's real Strava training history, derives a requirement from
demonstrated performance and consistency, applies the same product-retrieval pipeline, and
adds a layer of uncertainty-aware reasoning that flags when advice leans on thin or stale
data. Huella is **privacy-first by code**: Strava tokens and activity data live outside
Reflex state, never pickled to disk; only a typed allowlist reaches the merchant path.

Both flow through the same centralized price boundary (`money.py`), device registry
(`devices.py`), and catalog (`catalog.py`). Both use `gemini-3.6-flash` with a shared
pooled credential. Both ship as Docker containers on the same Traefik proxy at
`brujula.web.vespiridion.org` and `huella.web.vespiridion.org`.

## Load-bearing facts — the "do not fix these" registry

Every line below was verified against the live services on **29 Jul 2026** (COROS Colombia)
and **25 Jul 2026** (Strava). **These look like bugs and are not.** Anything here that gets
"corrected" breaks the build. If live behaviour really changed, update this registry and
`tests/test_contracts.py` **together**, in the same commit.

### COROS UCP and pricing

- **Endpoint:** `https://coros.com.co/api/ucp/mcp`, `Content-Type: application/json`, no
  auth, no key, no OAuth.
- **Agent profile in `params.arguments.meta`:** `params.arguments.meta["ucp-agent"].profile`
  = Shopify's public example
  (`https://shopify.dev/ucp/agent-profiles/examples/2026-04-08/valid-with-capabilities.json`).
  This is a **capability declaration, not a credential** — the server really fetches it, so
  it must be publicly reachable over https. `localhost` fails "Https required".
- **EVERY method needs the profile, and only that one placement works.** `initialize`,
  `tools/list` and `tools/call` all fail `-32001 "UCP discovery failed" / invalid_profile_url`
  without it. `params.meta`, a top-level `meta`, `profile_uri`, and every HTTP header tried
  also fail. So `tools/list` is sent with an `arguments` key it has no arguments for — that
  looks like a bug in `ucp.py` and is the only shape the server accepts.
- **`initialize` SUCCEEDS here** (`serverInfo.name == "universal-commerce"`), and so does
  `tools/list` (13 tools). This corrects the earlier reading of this registry and the plan,
  both of which said `initialize` returns `-32001`: it does so only when the profile is
  missing, which is also true of every other method. Two real divergences from DecaBot,
  where both fail unconditionally. Nothing depends on the handshake either way, so
  `ucp.py` still never calls it — but do not "fix" a passing `initialize`.
- **JSON-RPC errors arrive with HTTP 422 (bad profile) or 403 (bad tool name).** The body
  must be read **before** `raise_for_status()`, or COROS's own diagnostics collapse into a
  bare `httpx.HTTPStatusError`. This is the ordering DecaBot did not need.
- **`-32000 AuthenticationRequired` means the tool NAME is wrong**, not that a credential is
  missing. Every real tool — including `create_cart` — answers unauthenticated; a typo'd
  name is what returns 403 "A valid JWT is required to call `<name>`".
- **`result.structuredContent` is byte-identical to `json.loads(result.content[0].text)`.**
  The documented path is `content[0].text`; the duplicate is not a second source of truth
  and switching to it is a change, not a cleanup.
- **Prices come in two units, 100× apart:** Storefront feed `products.json` returns
  `price: "1099000.00"` (**major**, decimal string, COP whole units); UCP `get_product`
  returns `{"amount": 109900000, "currency": "COP"}` (**minor**, integer, centavos).
  `money.py` is the single conversion boundary.
- **Product types are unreliable for device identification.** PACE 4, APEX 4 (42mm), NOMAD,
  VERTIX 2, and VERTIX 2S all report empty `product_type`. Device matching uses the
  hand-authored registry in `devices.py` keyed by product id/handle, **never** by
  `product_type`.
- **Strap sizing: 22mm vs 24mm by watch model, 46mm vs 42mm APEX 4 variants.** This must
  be enforced deterministically in `devices.py`; the model will hallucinate a strap fit.
- **Cart lines are `cart.line_items[].item.id`** — not `merchandise_id`, not a bare id.
- **Responses are double-encoded.** The real body is a JSON *string* inside
  `result.content[0].text` — `json.loads()` it a second time.
- **Schema errors arrive as `result.isError: true` with HTTP 200**, not as JSON-RPC
  errors. Naive error handling treats a rejected call as success. The `isError` text is
  sometimes a bare sentence (`"Missing required arguments: catalog"`) and sometimes a full
  JSON envelope, so **check the flag before decoding** — decoding first raises on half of
  them and discards the message on the other half.
- **Every decoded body carries a `ucp` capability echo of ~4 KB.** `data.pop("ucp", None)`
  in `ucp.py` strips it; left in, it is the largest thing in the model's context.

### COROS storefront catalog and retrieval

- **Single page, 45 products.** `GET /products.json?limit=250` returns the complete COROS
  Colombia catalog in one request, no pagination. Product count verified live 29 Jul 2026.
- **Retrieval must use `requests` MODULE, not a Session.** Reused connections are refused
  with 429, which cascades to the UCP rate limiter. See DecaBot `AGENTS.md:111-128` for the
  measured evidence; the pattern is identical here. `catalog.py` uses `requests.get()`, not
  a pooled session.
- **Sizes arrive phrased the way the customer said them** — `"US 10.5"`, `"men's L"`,
  `"size 8"` — while feed labels are bare (`"10.5"`, `"L"`). `_clean_request()` strips that
  noise before matching. Without it, exact in-stock matches fail and the agent substitutes.

### Strava integration (Huella only)

- **OAuth 2.0:** authorize endpoint `https://www.strava.com/oauth/authorize`, token
  endpoint `https://www.strava.com/oauth/token`. Scopes: `read,activity:read_all,profile:read_all`.
- **Access tokens expire in 6 hours; a refresh invalidates the previous refresh token
  immediately.** Persist the new pair atomically or the user is locked out. `_SESSIONS`
  dict update is not atomic across a retry; use a lock.
- **Rate limits:** 200 requests per 15 minutes + 2000 per day (overall); 100 per 15 min +
  1000 per day (read endpoints). The API returns `X-RateLimit-*` and `X-ReadRateLimit-*`
  headers. A 429 response means the request was refused, never silently drop it to an empty
  list.
- **Activities have no `type` enum.** They carry a `sport_type` string which may be any of
  40+ values (e.g. `"AlpineSki"`, `"NordicSki"`, `"BackcountrySki"`, `"IceSkate"`,
  `"InlineSkate"`, `"RollerSki"`, `"Skateboarding"`, `"Snowboarding"`, `"Snowshoeing"`,
  `"Trail Run"`, `"TrailRun"`, `"TrackRun"`, `"Run"`, `"Trail Run"`, …). The model must
  not invent categories.

### Rate limits and pacing

- **No measurable rate limit on COROS UCP yet.** Decathlon's documented 20 sequential / 40
  concurrent is a safe working assumption; DecaBot `AGENTS.md:66-81` covers pacing policy.
  COROS has not triggered the same lockout. If it does, apply the same latch-pacing rule
  (Semaphore, never un-latch on success).
- **Storefront has its own limiter**, separate from UCP. Observed 29 Jul 2026: a burst of
  6 concurrent reads to `products.json` returned 429 from the storefront even before UCP
  was touched. Use the unpooled pattern and back off on 429 with a budget.

### Reflex / frontend and serving

- Copy from DecaBot `AGENTS.md:218-252`, sections "Reflex & serving" and "Container
  deployment". The app architecture is identical: one `rx.State`, two ports in dev (frontend
  3000, backend 8000), one port in prod (both on 8000), compile into `.web/build/client`,
  domain-agnostic `api_url=http://localhost:8000`, skip-compile in prod, `granian` not
  `uvicorn`, session state to `./.states` on disk (DISK state manager, not NOOP).
- **Do NOT re-read those sections here. Link to DecaBot's AGENTS.md.**

## Module boundaries — enforced socially, and worth it

- **Nothing posts to the UCP endpoint except `ucp.py`.** New MCP tools go through
  `call_ucp()`; anything that is not a `tools/call` goes through `rpc()`. A second caller
  brings its own idea of the profile, its own retry policy, and its own share of the rate
  limit. Caught by an AST scan in `tests/test_ucp.py` — a docstring may *mention* the
  endpoint, a code path may not name it.
- **Nothing constructs a price except `money.py`.** All conversions between major and
  minor units happen in that one file. A price `*100` or `/100` anywhere else is a bug
  caught by the test scanner in `tests/test_money.py`.
- **Nothing matches a device except `devices.py`.** The device registry is deterministic
  and auditable; a `product_type` field is never used for device identification.
- **`create_cart` and `create_checkout` are not exposed as model tools.** Human-in-the-loop
  is enforced by *absence* from the tool list, not by a prompt instruction.
- **Nothing retrieves from the catalog except `catalog.py`, and catalog.py never caches.**
  Live retrieval every turn; `fixtures/` is for offline development only and is never read
  by the running app.

## Guardrail principle

**A guardrail written in the prompt is a suggestion; a guardrail written in Python
is a guarantee.** Every check is deterministic code between the model and the world,
and every one emits a trace event at `level="guardrail"`.

`LocalAvailabilityVerdict.is_available = Literal[True]` means an out-of-stock or
region-blocked item cannot be represented in a recommendation at all — the guardrail is
enforced by the type system. `BuyNothingVerdict` means "buy nothing" is a reachable typed
outcome, not prose that slipped through. `ProvenanceVerdict` proves a price matches the
live feed; an unbacked `"$X COP"` claim is rejected.

**Attribute invention is the likeliest way to be embarrassed live.** The agent will
not invent *products* — retrieval prevents that. It will invent *properties*: `"waterproof
to 100m"`, `"battery 20+ days"`, `"syncs via ANT+"`. The JSON says none of that. Specs are
rendered only from data; prose carries only reasoning.

## Conventions

- **Python 3.12, `from __future__ import annotations`, type-annotated.**
- **Comments: absolutely minimal.** Only where a fact is genuinely counterintuitive.
  No docstring on every function; no comment restating the code.
- **Run tests as `./.venv/bin/python -m pytest` — no `PYTHONPATH`.** `pythonpath` in
  `pytest.ini` is the single declaration of the suite's import roots (`.`, `packages`,
  both `apps/*`); a hand-rolled `PYTHONPATH=.` on a pytest command is how CI broke on
  PR #1. Anything that is not pytest goes through `make`, which exports `PYPATH`.
- **Never guess an API or a payload shape.** Read the fixture, run the call, or read the
  library source. Unfounded assumptions are the one unforgivable sin here.
- **Double quotes, 4-space indent, soft wrap 88–100.**
- Every optional Pydantic field needs an explicit default (`X | None` alone is
  required-but-nullable).
- Frozen models with tuple fields for immutability + hashability; `extra="forbid"` for
  untrusted boundaries.

## Maintenance contract

| If you change… | You must also update… |
|---|---|
| `packages/coros_core/money.py` | `tests/test_money.py` + the price-scaling entry in this facts registry |
| `packages/coros_core/devices.py` | `tests/test_contracts.py` with a live probe of the storefront + the device-matching entry in this facts registry |
| `packages/coros_core/catalog.py` | `tests/test_contracts.py` with the product count and the unpooled-retrieval entry |
| any tool schema | `brujula/agent/tools.py`, `brujula/agent/prompts.py`, `huella/agent/tools.py`, `huella/agent/prompts.py`, and the trace event names |
| `packages/coros_core/ucp.py` (wire shape, error taxonomy, rate-limiting policy) | this facts registry + `tests/test_ucp.py` — the offline half and the `live` probes together |
| a guardrail | the guardrail table + its test + the trace event |
| anything Strava-scoped | `tests/test_strava.py` + `tests/test_privacy_boundary.py` — token atomicity and state isolation are release blockers |
| `rxconfig.py` | recompile the frontend; note the new URL in `docs/DEPLOY.md` and `docs/RUNBOOK.md` |
| a touched-path gate in `infra/jenkins/Jenkinsfile` | `infra/jenkins/Jenkinsfile`; the two apps have separate images |
| the VPS deployment | nothing; merging to `main` rebuilds and redeploys. `docs/DEPLOY.md` covers the by-hand path. |

**PR checklist:** facts registry still accurate · `make check` green · `make verify` green
if commerce paths changed · `DECISIONS.md` appended if architectural.

## Where the documentation lives

| | |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | This file's pointer. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Append-only architectural log. |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Both services: URLs, vault keys, images, on-host paths, rollback, symptom→cause. |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Demo sequences and contingencies for both apps. |
| [`docs/DEMO-READINESS.md`](docs/DEMO-READINESS.md) | P0/P1/P2 bugs found during live QA. |
| [`docs/VISUAL-BRIEF-BRUJULA.md`](docs/VISUAL-BRIEF-BRUJULA.md) | Complete design instruction set for Brújula. |
| [`docs/VISUAL-BRIEF-HUELLA.md`](docs/VISUAL-BRIEF-HUELLA.md) | Complete design instruction set for Huella. |
| [`docs/EVAL.md`](docs/EVAL.md) | Baseline harness and six evaluation metrics. |
