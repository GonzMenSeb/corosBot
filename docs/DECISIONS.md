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

### 2026-07-30 · The device registry is two curated tables, and a width is only ever quoted

`devices.py` could have been three helpers over the feed: split the handle on `mm`, read
`product_type` for the family, match a strap to a watch when the widths agree. Every one of
those is wrong against the live catalogue, and each was checked before it was rejected.

`product_type` is empty on the PACE 4 and says `Relojes GPS` on the `coros-dura`, which is a
bike computer — COROS's own homepage labels that card `alt="Ciclocomputador COROS DURA"`. The
handle `correa-de-nylon-de-24-mm-morada-para-apex-4-46-copia` belongs to a 22 mm silicone
strap for the APEX 4 42, and two more handles lie the same way. And COROS sells a 24 mm strap
whose description reads "solo compatible con el APEX 4 46mm" alongside three 24 mm NOMAD
straps: equal width, and the vendor states they do not swap. So the file is a hand-authored
table of 14 devices and 26 strap products, joined to the feed **by product id** with the
handle as a second key, each row carrying the sentence it was curated from.

Two rules follow from that and are worth more than the table. A **width is recorded only
where COROS states it in a title or a description** — the PACE 2 and APEX 2 widths exist
nowhere but in a handle, so those rows carry none and `strap_width()` answers `None` rather
than 20 mm. And **ambiguity raises**: the APEX 4 is the one device with two cases, they take
22 mm and 24 mm, and `straps_for("apex-4")` with no case raises `CaseUnspecified` instead of
returning `()`, which would read as "COROS sells no APEX 4 straps".

The registry does not certify itself. `audit(products)` re-derives every join against the
live feed and returns the drift as sentences: a product id that vanished, a handle that was
edited, a case size the feed offers and the table does not, and any unregistered product
whose title names a device — which is how the day COROS Colombia starts selling the PACE Pro
becomes a failing test instead of a refusal that is quietly untrue. Zero drift against all 45
live products on 30 Jul 2026; the `live` half of `tests/test_devices.py` is what keeps it so.

The plan for this module counted six devices as not sold locally. It is ten: the feed also
carries straps for the PACE 2 and the APEX Pro with no watch SKU, and names the gen-1 APEX
and VERTIX in the chargers' copy. Corrected in `AGENTS.md` in the same commit as the table.

### 2026-07-30 · Guardrails verify against the feed, not against the candidate

The obvious shape for `check_stock` is to read `available` off the item the model proposed.
That check passes whenever the model is wrong in the one way that matters. So every check in
`packages/coros_core/guardrails.py` takes the catalog snapshot as a second argument and
re-derives the answer from it: `check_provenance` rebuilds the whole `AdviceItem` from the
feed and keeps only the model's `rationale` and `satisfies`, recording what it claimed as a
`FieldMismatch` rather than arguing with it; `check_stock` looks the variant up and ignores
any availability the candidate asserted. Verification through an independent code path is
the point — a check that reads the thing it is checking is a prompt with extra steps.

Two shapes fall out of that. Ambiguity is a question, never a pick: a product with two
variants and none named is dropped rather than resolved to the first, matching
`devices.straps_for`, and matching the one live size mistake DecaBot made with a stage that
chose. And a price in prose is compared as a number against the set the code computed, so
`"$1.099.000"` and `"$1099000"` are one price and `"$980.000"` is a claim.

### 2026-07-30 · An absent watch has nowhere to put a substitute

Ten of the fourteen devices in the registry are sold in Colombia as straps and not as
watches, so a search for a VERTIX 2 returns six real products and none of them is a watch.
The failure mode is not refusing — it is answering with a PACE 4 and letting the swap read
as an answer. `UnavailableDevice` therefore has no `alternative`, `instead`, `closest` or
`replacement` field, its two sentences are templates over that one device's own name, and
the cleared path is a separate model whose `is_available: Literal[True]` cannot be
constructed for a device the registry marks absent. The guarantee is the absence of a field,
which survives a careless edit in a way a rule in a prompt does not.

Finding the devices needed a scan `devices.resolve` does not offer: it answers for a whole
string and returns one device, so "compara el VERTIX 2 con el PACE Pro" loses one.
`devices_named()` walks token windows and asks repeatedly, and accepts a window only when
extending it by one token still resolves to the same device. Without that guard "APEX 5"
shrinks to the window "apex" and comes back as the gen-1 APEX — the exact substitution
`devices._names_this_device` was written to refuse. Measured against all 45 live products:
zero invented devices, and every locally-sold watch found in its own title.

### 2026-07-30 · Colombian price text is parsed by locale and fails closed

`minor_to_display` prints `$1.099.000`, so that is the form the model sees and the form
`find_unbacked_claims` reads: `.` groups thousands, `,` is the decimal mark. A US-formatted
`$1,099,000` is unreadable under that rule and comes back `None`, which flags it as an
unbacked claim and scrubs it. Guessing would be worse than scrubbing: the two readings of
that string differ by a factor of a thousand. All parsing goes through
`money.major_string_to_minor`, so `guardrails.py` contains no rescaling of its own and the
source scan in `tests/test_money.py` stays true.

Verified before shipping: the claim patterns produce **zero** false positives when each of
the 24 described products in `fixtures/products.json` is checked against its own
description, and tampering with a real spec figure — `42mm` to `49mm` — is caught.

### 2026-07-30 · `trace.py` lands as the ring only

`guardrails.py` cannot satisfy "every verdict emits at `level="guardrail"`" without an
`emit`, so the ring buffer, `TraceEvent` and `reset()` ship with it. Per-session sink
binding across `asyncio.create_task()`, the storefront and UCP instrumentation, and the
evidence bundle are deliberately not here; they arrive with `evidence.py` in this same PR.

### 2026-07-30 · The evidence bundle reads the trace, not the agent

A stage cannot certify itself, so `evidence.build()` takes the advice and the turn's trace
and nothing else. A check that emitted no event did not run, a recommendation missing a
required check is `accepted=False`, and the only way to make a check appear is to call the
function in `guardrails.py` that emits it. That is also how the bundle catches the failure
that is invisible from inside a prompt: an item the stock check rejected, or a device the
local-availability check named as not sold in Colombia, appearing in the advice anyway. The
answer is blocked with the product id in the reason, not softened into a caveat.

Two readings are deliberately not the obvious ones. **An outcome is about the advice
agreeing with the verdict, not about the verdict being good news** — an over-budget
selection reported as over budget is a `pass` with the overage carried as a risk, because
killing the honest bad answer is the failure mode and not the goal. And **silence is not a
pass**: `scrub_prose` emits only when it excises something, so no `guardrail.prose` event
means either clean prose or prose nobody scrubbed, and the bundle reports that as an
untested region rather than resolving it in whichever direction flatters the run. Each
check declares what it verifies, what it cannot verify, and what confidence it provides
(KB `Code As Agent Harness.pdf` §5.2.2), and the assumptions carry `held=None` for the ones
nothing re-derived — "unchecked" is not the same claim as "true" (§5.2.4).

### 2026-07-30 · A trace payload is stored as JSON text

The bundle's verdicts are only evidence if nobody can rewrite them afterwards, and a frozen
dataclass holding a `dict` is not frozen — the same leak `models.py` documents for a frozen
model holding a `list`. So a payload is serialised at `emit()` and rebuilt on every read:
callers cannot reach the record, readers cannot edit it, and a value JSON cannot represent
is written as its `str()` rather than raising, because an observability layer that can take
down the run it observes is worse than none. The cost is that an int-keyed payload comes
back string-keyed; nothing emits one. `dropped()` counts what the bounded ring evicted, and
the bundle reports the loss — a bounded log is fine, a bounded log that reads as complete is
not.

### 2026-07-30 · Instrumentation carries counts and names, never the text

`catalog.unavailable` is emitted inside `CatalogUnavailable.__init__` rather than at the six
raise sites, so a new failure path cannot be added without a trace event. `strip_untrusted`
emits only when it actually removed an injected segment, and carries how many segments and
how many characters — not the segment. `call_ucp` records argument NAMES and never values.
Both for the same reason: an evidence bundle is an artifact a human pastes into a model, so
a trace that quotes the injection verbatim launders it back into a prompt, and one that
quotes an argument leaks whatever the argument was.

### 2026-07-30 · One door to the model, and it corrects the shapes it is handed

`generate()` in `packages/coros_core/gemini.py` is the only place a Gemini request is issued,
the only place a `genai.Client` is built, and the only place the model name is spelled. Three
AST scans in `tests/test_gemini.py` enforce that, for the reason `ucp.py` has its own scan: a
second call site brings its own retry policy and its own share of a quota that Brújula, Huella
and DecaBot all draw from. `model` defaults to `gemini.MODEL`, so an upgrade is one line rather
than a grep.

Two shapes are corrected rather than trusted, because both fail as an `AttributeError` or a
`RuntimeError` from inside google-genai — errors that read as our bug and point at no fix. A
bare `types.FunctionDeclaration` in `tools=` is wrapped by `as_tools()`; the config is
`model_copy`d rather than edited, since module-level configs are shared between turns and a
normaliser that writes to its caller's object is a second writer to something everything reads.
And a `genai.Client` nobody holds closes its own transport in `__del__` while `client.aio.models`
keeps working — so the client is `lru_cache`d and `generate()` binds the client, not the models
object, for the whole ladder.

Diverging from DecaBot on purpose: no key pool, no `bind_key()`, no `GEMINI_PUBLIC_KEYS`. That
machinery exists there to spread a QR-code audience across keys; here the 29 Jul decision to
reuse one credential makes it dead code, and dead auth machinery is worse than none. What the
shared quota does earn is a `model.call` trace event carrying token counts — counts only, never
the text, the same rule instrumentation follows everywhere in this repo — because burn against
one shared quota is otherwise invisible until it runs out. Giving up after four attempts emits
`model.failed` before re-raising, so a turn that died of quota is distinguishable from a crash.

### 2026-07-30 · A capability the store does not have is a typed dead end

`packages/coros_core/capability.py` answers one question before retrieval runs: is there any
tool here that could serve this request? `capable_tools()` is the pure map; `check_capability()`
turns an empty answer into a typed `DeadEnd` and emits `guardrail.capability`. The failure being
designed out is the one KB `docs/lifeseek/SPEC.md` §4.2 calls the most dangerous silent failure
there is — an empty result that reads as "COROS has nothing for you" when the truth is "nothing
here could have looked". `CapabilityVerdict` makes it structural rather than disciplinary: no
tools and no reason is not a constructible object, so the empty case cannot travel.

The COROS-specific reason this earns a module. COROS Colombia's entire cycling range is a cadence
sensor and a speed sensor; nothing in the 45-product feed measures power, in cycling or in
running. Ask retrieval for a power meter and it returns the speed sensor — a real product, in
stock, at a real price, for a capability it does not have. That is the substitution
`check_local_availability` bans for devices, arriving through a door that check does not watch,
and it is worse than an absent watch because no COROS product will ever satisfy it.

Three distinctions inside it are load-bearing, and each one is a different answer to the person:

- **Capability is not availability.** The cadence sensor is out of stock, so `cycling_cadence`
  stays *capable* and `check_stock` reports the shelf. A dead end there would tell someone COROS
  does not make a product it makes and will restock. `DeadEnd.outcome` is restricted to the two
  escalating outcomes, so `UNAVAILABLE` cannot be dressed as a capability verdict at all.
- **A person is not a missing tool.** `create_cart`/`create_checkout` are withheld by design, so
  `place_order` is `NEEDS_HUMAN`. `NO_CAPABILITY` there would be a lie about the store. `WITHHELD`
  names them as strings precisely so the omission is auditable instead of being a silence someone
  re-adds; a test asserts no `ToolId` shares those values.
- **A need nobody declared is a dead end, not a wildcard.** `need` is a normalised `str`, not the
  `Need` literal: it arrives from a model, and a typo has to fail closed rather than raise out of
  the gate meant to catch it. No alias table translates Spanish synonyms — "potenciómetro" is
  ambiguous between the two power needs COROS answers differently, and picking one is a guess.

The `running_power` dead end is the case that proves the map has to be code rather than a prompt
line or a retrieval hope. COROS answers that exact question itself, in the POD 2's own FAQ — and
4 916 characters into a 36 KB BeeFree template, where `DESCRIPTION_CHARS = 400` truncation means
no model ever sees it. The vendor's denial exists, and retrieval structurally cannot deliver it.

Diverging from DecaBot, which has no equivalent: its catalogue is wide enough that "nothing
matched" is nearly always the truth. COROS Colombia sells 45 products, so the gap between "we
found nothing" and "there is nothing to find" is most of the catalogue.
