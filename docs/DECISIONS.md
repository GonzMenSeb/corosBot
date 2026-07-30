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

### 2026-07-30 · Brújula retrieves from a per-turn snapshot, not from a navigable surface

DecaBot's retrieval is navigation: `list_collections` → `get_collection_products`, one storefront
request per collection, because Decathlon's catalogue is thousands of SKUs and no turn can hold
it. Brújula's tools are named the same — the ids are frozen in `capability.ToolId` — and do
something structurally different: they all answer from **one** feed read the turn already paid
for. Three reasons, in order of how much they cost to ignore.

**The catalogue is 45 products in one request.** `products.json?limit=250` returns all of them,
no pagination. A second request per group would buy a subset of products we are already holding,
and buy it from the harshest limiter in this system — measured at ~4 requests before an IP-level
lockout that outlasts the conversation. During this task's own exploration the storefront locked
and stayed locked across three spaced probes, so `collections.json` was never verified; building
`get_collection_products` on an unverified shape would have been the guess this repo does not
make.

**A search that matches nothing is therefore CONCLUSIVE.** `ToolOutcome.UNAVAILABLE` from
`search_products` means "we read all 43 and none matched", which is real evidence — exactly what
`check_buy_nothing(retrieval_conclusive=True)` needs and what a paginated surface can never
supply. It also flips the honesty problem around: on DecaBot an empty result is usually a partial
look, here it is usually the truth, and the tool says which.

**No model-facing tool touches UCP.** `search_products` does not call UCP's `search_catalog`,
and an AST scan in `tests/test_brujula_agent.py` keeps it that way. Two reasons compose: a UCP hit
that is not in the snapshot cannot pass `check_provenance(candidates, catalog)` — that signature
only accepts `CatalogProduct` — so a semantic path can only ever re-rank products we already
have; and `create_cart` lives behind the same client, so a model-facing module able to post to
`ucp.py` has re-opened the door `WITHHELD` exists to keep shut. UCP stays reachable from a click
handler, which is the whole human-in-the-loop design: **UCP is the cart surface, and the model
has no access to it at all.**

Measured while deciding this, and worth writing down because the plan for this task said
otherwise: `search_catalog` with `pagination.limit = 10` returned 10 products and
`has_next_page: true`, not the 7-with-`false` the plan recorded. It paginates and it caps, so it
was never going to be a complete view of a catalogue we can read completely in one request.
Nothing depends on the number, which is why it is here and not in the facts registry.

### 2026-07-30 · The three catalogue groups come from the device registry, never from the feed's own fields

`list_collections` answers "what does COROS Colombia actually sell" with three groups —
`relojes` (4), `correas` (26), `accesorios` (13) — and they are a **partition**: every visible
product lands in exactly one, so nothing is double-counted and nothing is unreachable. A test
asserts the partition and the three counts.

The obvious taxonomy was `product_type`, and it cannot be used. It is empty on 24 of 45 products
including the PACE 4, and it says `Relojes GPS` for the DURA, which COROS's own homepage labels
a *ciclocomputador*. Tags are worse rather than better: `APEX Pro` on a charging cable is a
compatibility claim, and compatibility has exactly one authority in this repo. Grouping on tags
would have opened a second, uncurated device-matching path straight through the rule
`tests/test_devices.py` enforces on `devices.py`. So `relojes` and `correas` are the two curated
registry tables and `accesorios` is defined as the complement — everything the registry does not
name — and an AST scan rejects any read of `product_type` or `tags` in `tools.py`.

`list_collections` also takes no `query` parameter, unlike DecaBot's. Three groups do not need
searching, and a parameter the model can get wrong is a parameter that produces a retry.

Two related calls in the same file. `_slim()` does **not** forward the sanitised `description`:
it is the injection surface, and 400 characters of marketing copy per product is an invitation to
read a spec out of prose when the specs that matter — strap widths, compatibility — come from the
registry with the sentence they were read from. And `lookup_device_compat` answers an APEX 4 with
no case size by returning the *question* as `NOT_ELIGIBLE`, because an empty strap list there
would read as "COROS sells no APEX 4 straps", which is false, and picking a case is a guess.

### 2026-07-30 · A turn ends when the evidence bundle accepts, not when the model stops

`loop.py` builds the bundle **before** the presentation call and presents nothing when
`accepted` is False. That ordering is the decision: a recommendation whose required checks left
no trace event behind never gets prose written for it, the person is told which check is missing
instead, and no stage is marked done — so the next turn resumes rather than the case being
closed with an answer nothing verified. The bundle is rebuilt after the scrub so its `prose` row
reflects a check that had not run yet at gate time. KB §3.4.4: stopping after N iterations "is
the most prevalent convergence pattern in the literature and represents the most significant gap
in the field".

Resumption is keyed on **completion, not emptiness**. `session.done` is a set of stage names, so
an interview that asked nothing is not an interview that never ran; DecaBot's `if not
session.slots` would re-ask on every turn. A message that arrives after a *finished*
recommendation reopens `REOPENED` — the person is changing something, and answering from
requirements they have replaced is worse than spending the calls again.

Only a recommendation costs a presentation call. "Buy nothing", "COROS Colombia does not sell
that", "I could not read the catalogue" and the capability dead ends are rendered from typed
verdicts through `prompts.py`. Those are the four sentences a person is most likely to be lied
to about, and generated prose is prose that can drift; the templates cannot.

Two consequences worth stating. The buy-nothing verdict is checked **before** the item list, so
a selection nothing can afford is reported as a buy-nothing rather than presented and then
blocked — the honest answer and the verified one are the same answer. And the tool surface for
retrieval is read off `capability.MAP` at call time instead of being hardcoded to
`tools.DECLARATIONS`: an empty map raises a typed dead end, because a retrieval stage handed no
tools answers from the model's memory.

Two divergences from DecaBot's loop. It imports `catalog` directly rather than matching
exception class names — there is one upstream here, the read happens in the loop, and no
model-facing tool can reach another. And there is no `_repair` ladder for structured stages:
provenance is enforced downstream by `check_provenance`, so a stage this loop cannot read fails
the turn instead of being retried into something plausible.

### 2026-07-30 · Response schemas are plain wire models, because `extra="forbid"` 400s

Verified live against `google-genai` 2.14.0: handing Gemini a `response_schema` built from a
model with `extra="forbid"` renders `additionalProperties` into the schema and the API answers
`400 INVALID_ARGUMENT · Unknown name "additional_properties"`. `coros_core.models` sets
`extra="forbid"` on every policy model on purpose — a model inventing
`max_total_minor_override` must be rejected, not ignored — so none of them can cross the wire.

`loop.SCHEMAS` is therefore four plain models, and each is validated into the frozen core model
in code. That is not a workaround: the validation step is where a requirement key outside the
allowlist, a `Provenance` label the model made up, and a derived value with no sample size are
dropped with a trace event. A schema that had gone over the wire intact would have had to reject
those on the model's side, which is the one place we do not control. A `str | int | bool` union
renders as `anyOf` and is accepted; `budget_minor` came back an int on the same probe.

### 2026-07-30 · The conversation lives outside Reflex state, for the cost and not the limit

The plan said Reflex state cannot hold a dataclass, so `ConversationSession` had to live in a
module-level map. Probed before building on it, and the claim is false: Reflex 0.9.7 accepts a
dataclass state var, wraps it in a `MutableProxy`, tracks mutations through it and serialises it
— sets included — to the browser.

The map stays anyway, and now for a reason a reader can check. As a state var, every mutation the
agent loop makes to the transcript, the extracted requirements and the finished advice is
broadcast to the browser on the next drain, and `StateManagerDisk` pickles the same bytes into
`.states/` per client token. A backend-only (`_`-prefixed) var fixes the wire half and not the
disk half. `_SESSIONS` is process-local memory: the transcript never reaches either surface, and
`state.trace` carries the clamped rows the panel actually renders instead.

Two divergences from DecaBot's version of the same map. It is an `OrderedDict` bounded at
`MAX_SESSIONS=200` with the oldest evicted, because these apps are hosted for weeks rather than
demoed for an evening and an unbounded map is one transcript per browser that is never freed —
and an evicted conversation costs the person an interview they already answered, which is a
recoverable loss, never a fabricated answer. And it is still process-local, so the note in
DecaBot's `AGENTS.md` holds here too: **1 granian worker is load-bearing.** Adding Redis or a
second worker without moving this map out of module scope splits a conversation across processes.

Two facts fell out of the tests and are registered in `AGENTS.md`. The
`MutableProxy`/`json.dumps` trap is the *compact* encoder only — passing `indent=` selects the
pure-Python path, which goes through `isinstance` and survives, so a mutation test that removes
`plain()` passes against an indented dump and fails against a compact one. And
`hmac.compare_digest` raises `TypeError` on non-ASCII `str`, so the gate compares
`_digest(typed)` against `_GATE_DIGEST`: an accented password is a refusal, not a 500.

### 2026-07-30 · One route, and the gate is a branch inside it

Brújula serves a single page. `index()` is `rx.cond(State.unlocked, shell, gate)`, and there is
no `/gate` route, because a second route is a second door: `on_load` is registered per page, so a
URL that renders the shell is a URL where the password check never ran. The same reasoning keeps
the trace panel, the kit and the evidence rail inside that one page rather than behind routes of
their own — everything they show is a state var the gate already covers.

`rxconfig.py` carries five values and every one of them is load-bearing. `app_module_import`
because Reflex otherwise resolves `brujula.brujula`. `frontend_port`/`backend_port` pinned because
Reflex takes the next available port when one is busy and moves the app out from under whatever is
proxying it — and both apps run at once in dev. `vite_allowed_hosts=True` because False allows
localhost only and every other host gets `403 Blocked request. This host is not allowed.` on a
healthy app. And `api_url` stays `http://localhost:8000` so one image serves every domain: the
compiled client rewrites a same-domain host to `window.location.hostname`, upgrades `ws:`→`wss:`
and clears the port on https. `BRUJULA_API_URL` overrides it for a dev tunnel and is documented in
`.env.example` — DecaBot's equivalent was read by code and named nowhere.

Added over DecaBot: `disable_plugins=[SitemapPlugin]`. A password-gated single route has nothing
to put in a sitemap, and left on, the plugin prints a startup warning asking to be told either
way.

**`rx.App(theme=...)` stays on the app, deliberately, against its own deprecation warning.** 0.9.7
says to configure `rx.plugins.RadixThemesPlugin(theme=…)` in `rxconfig.py` instead. Taking that
advice puts the theme somewhere that cannot read `brujula/ui/theme.py`'s tokens, because
`get_config()` imports `rxconfig.py` with `sys.path` reduced to its own directory and only retries
with the ambient path if that raises. Both halves verified 30 Jul 2026: an `import coros_core` in
a probe `rxconfig.py` raises under a bare environment and succeeds with `packages/` on
`PYTHONPATH`. So config files stay on `reflex` and the stdlib, the theme stays on `rx.App`, and
`tests/test_brujula_app.py` fails if anyone follows the warning before the pin moves.

The presentation in `app.py` is plain on purpose and holds no colour literal: every surface reads
the Radix scale the theme selects (`var(--gray-11)`, `var(--red-11)`), so PR 5's measured tokens
replace a theme argument rather than a hunt through the file. The accent is `bronze` on `sand` —
warm paper over the COROS monochrome, red left free for the honest refusal, and nothing DecaBot's
indigo would recognise. Two things were carried over verbatim because DecaBot paid for them: the
composer is `position: sticky` at the bottom of the column, since with a kit on screen the column
runs several thousand pixels and the reply to a clarifying question sits below all of it; and
every icon-only-below-`md` button carries an `aria_label`.

Two artifacts fell out of proving the tree compiles. `reflex export --frontend-only` **shrank**
`reflex.lock/`: the markdown chain — `react-markdown`, `react-syntax-highlighter`, the
rehype/remark plugins — is gone, because nothing in Brújula renders markdown and the presentation
prompt asks for plain prose. That lockfile is derived from the component set, so it is re-exported
and re-committed whenever the tree changes; the maintenance contract now says so. And `reflex
init` — which any first run in a fresh clone triggers — seeded `apps/brujula/` with its own
`.gitignore` and a `requirements.txt` containing only the reflex pin. Both are now gitignored, and
`tests/test_layout.py` fails if either is committed: that requirements.txt shadows the root pins
for anything installing from the app directory, which is an image with reflex and no
`google-genai` in it, failing at the first model call instead of at build time.

### 2026-07-30 · Brújula's palette: COROS's values, and red inverted to the refusal

`apps/brujula/brujula/ui/theme.py` is anchored on the seven values coros.com.co publishes as
CSS custom properties, and on one reading of them. Their storefront is white, black and grey,
and its only warmth is `--color-border: #EEEEE0` — a warm ivory hairline on an otherwise
neutral page. Brújula takes that single value as its premise: the page is paper, the type is
COROS's own `#161d25`, and the accent is brass, a compass needle's metal. It shares no hex with
DecaBot, because two demos on one VPS that share a palette read as one demo with two front
doors.

**Red is the refusal, which inverts COROS's own usage.** `#ea2e41` is what they spend on buy
buttons — `.buy-btn`, `.button-primary`, `.form-launch-btn`, section headings. Here it never
sells anything: it is reserved for "COROS Colombia no vende eso" and "no compres nada", the two
sentences the whole guardrail layer exists to make possible. If red also meant *buy*, the
honest refusal would arrive in the colour of a purchase. Their `--color-warning: #ff706b` is
rejected for the same reason — a rate limit is not a refusal, and it is another red; amber
carries that one. Neither red carries words: white on `#ea2e41` measures 4.23:1, so `DANGER`
is the icon and the rule and `DANGER_INK` says the sentence.

**Their own success green cannot carry words either.** `#3a8735` on white is 4.47:1, three
hundredths under AA. Rather than drop COROS's value or ship type nobody can read, `SUCCESS`
stays the tick and the fill and `SUCCESS_INK` is a darkened sibling for the words.

**Barlow stands in for a licensed face.** COROS self-hosts PF Din Text Pro from their Shopify
CDN; it is a Parachute commercial face and we cannot ship it, so the interface uses Barlow —
the same DIN-derived skeleton, on Google Fonts. Fraunces is Brújula's own editorial serif and
is deliberately *not* COROS's: the wordmark and the headings, nothing else. One verified
request loads all three with latin-ext, because the whole interface is Spanish and a family
without the accents renders "Brujula".

**The ratios are enforced, not written down.** `tests/test_brujula_theme.py` recomputes every
`TOKEN on SURFACE n.nn:1` figure in the token file from the two colours it names, and rejects
a figure that names no colours — an unverifiable ratio is indistinguishable from an invented
one. Each colour must also classify itself in `SURFACES`, `TYPE_ON`, `EDGE_ON` or `RULE_ONLY`;
type clears AA on every surface it is declared on, an edge clears WCAG 1.4.11's non-text
floor, and anything that can clear neither is `RULE_ONLY` **with the reason** — which is how
COROS's `--color-sub-text: #9A9A9A` (2.65:1 on our paper) is kept off words for good. A last
scan rejects a saturated red outside the refusal family, so the decision above cannot be
undone by a later component reaching for a red button. Eleven mutation probes were run against
these tests: lightening the muted type, falsifying a stated ratio, adding an unclassified
colour, turning the accent red, dropping a trace level's colour, lightening the rail until a
paper token transfers, letting DecaBot's indigo in, and five more, each failing on exactly the
test that names it.

**The rail is a second register, inverted rather than shared.** The audit rail is Brújula's
instrument, not COROS's chrome: a warm ink slab, with secondary type tinted from the slab's own
hue because a neutral grey on warm black reads as dust. None of the paper tokens transfer —
`BRASS on RAIL_BG 3.10:1` — and a test asserts they still cannot, so the rail cannot quietly
drift light enough to make `RAIL_*` look redundant.

### 2026-07-30 · The mark is drawn, and its amber means the throttle rather than a replay

`brujula/ui/brand.py` draws the compass — a cream dial, a bezel somebody can see, four inert
ticks and one brass needle off north with an index mark cut into the bezel at the same bearing —
instead of setting `rx.icon("compass")`. Two reasons, and neither is taste. An icon set's glyph
is a stroke weight and a palette we do not control, so it cannot honour `theme.SURFACES`'s own
reservation of BRASS for "the mark's needle", and `EDGE on BRASS_SOFT 3.17:1` is what gives the
mark a silhouette at all — `BRASS_SOFT on CARD 1.16:1` means a dial with no bezel is invisible on
a white header. The needle sits off north because on north it reads as an arrow pointing
somewhere, and Brújula's claim is that it measured first. `EDGE_ON` gained `BRASS_SOFT` in the
same commit, because a bezel is an edge somebody has to see and theme.py declares every surface
a colour is set on.

**The presence dot's amber is the storefront throttle, not a fixture replay.** DecaBot's dot is
green on live data and amber while replaying a fixture, and the plan asked for the same three
states. `fixtures/` is never read by the running app here, so that amber could never light: it
says the one thing that genuinely degrades an answer instead, `State.throttled` — COROS is
rate-limiting us and what follows came off a partial read of the catalogue. `state.py` clears
`throttled` in its `finally`, so the amber only appears mid-turn, beside the halo rather than
instead of it. Idle is green only when `gemini.api_key()` returns something: a green dot in front
of a process with no key promises an answer it cannot produce, and the demo finds that out
mid-question.

**The amber pip is hollow and the other two are solid, which is a measurement and not a slip.**
`BRASS vs WARN_INK 1.17:1` — a solid dark-amber dot and a solid brass dot are the same dot at
7px, in the same hue family. So the throttle pip is theme.py's own throttle notice in miniature,
`WARN_SOFT` with a `WARN_INK` rim, and it spends its ring on that rim rather than on the surface
colour the other two use to separate from the bezel. `tests/test_brujula_brand.py` measures the
three pips against each other and requires 25° of hue or 1.8:1 of contrast between any two —
green and brass pass on hue at 1.32:1, and a solid amber fails on both, which is how it was
caught. Sixteen mutation probes were run against that file, each failing on the test that names
it: the idle dot forced green, the throttle pip made solid, the needle turned back to north, an
inline animation on the mark, a loose hex and the refusal red on the needle, the wordmark split
to tint its accent, the dial announced to a screen reader, a drifted ratio, and six more.

**The wordmark stays one text run.** DecaBot tints the second half of its name because
"Deca|Bot" has a seam; "Brújula" has none, and tinting the ú would cut the word into three inline
boxes with the seam inside a kerning pair. The brass lives on the needle instead, and the only
two-tone in the lockup is the tagline, where COROS's name is set in COROS's own
`--color-primary-darker` and ours in QUIET — the relationship stated in type.

### 2026-07-30 · The interface is checked against the palette, not just built from it

`tests/test_brujula_theme.py` proves the palette measures what it claims. It cannot prove the
interface uses it that way, and that half is where a measured palette actually fails: a token
whose ratio was computed against `CARD` ends up on `BRASS_SOFT` in one component and nobody
notices, because both look like "the light one". So `tests/test_brujula_ui.py` walks every
rendered surface and resolves the pairs the way a browser does.

**The walk runs over `Component.render()`, not over the component objects.** `rx.match` renders
its cases at construction time, so an object walk loses every branch of every match in the tree
— which would have been all four refusal panels and every per-level style on the audit rail. The
rendered form is also what actually ships: a `css:({…})` string holding the declarations a
browser will apply. A node's own `background` replaces what it inherited, a `_hover` background
is a surface too so a colour on that node has to clear both, and an `rgba()` or a gradient
resolves to no token at all — those subtrees are skipped rather than guessed at. A border is
measured against what is BEHIND it, because an edge separates a box from the page; a border
painted in the enclosing surface's own colour is a cutout, which is what the presence dot's ring
needs and the only carve-out in the rule.

**`EDGE_ON` gained `PAPER_DEEP` for `EDGE` and `BRASS`, measured at 3.15:1 and 5.03:1.** The page
is a gradient into `PAPER_DEEP` — theme.py calls it "the page's lower gradient stop" and already
declared `INK`, `GRAPHITE` and `QUIET` as type on it — but the edges every card and panel draws
against it were never declared. Walking `app._shell()` rather than the column alone is what
surfaced that, and the two pairs were computed with the same helper the theme's own suite
recomputes them with. The alternative was dropping the gradient, which would have left half of
`PAPER_DEEP`'s stated purpose dead.

**Each refusal is a literal panel, selected by `rx.match` over components.** The tempting shape
is one panel whose ink, fill and glyph are each an `rx.match` over `advice_kind`; the failure
mode is that those three tables are independent, so an entry added to one and forgotten in
another silently pairs an ink with a fill nobody measured it against. Written as four whole
trees, a panel's ink is measured against that panel's own surface and against nothing else —
`_panel()` takes both halves at once, and `_KINDS` is asserted to cover every `AdviceKind` that
is not `recommend`, so the unnamed fallback stays unreachable.

**Nothing on either surface is markdown.** DecaBot renders its model's half of the transcript
through `rx.markdown` because its prompts answer in headings and bullet lists. Brújula's
`prompts.py` forbids lists, prices and totals — those are cards — so every template and every
generated reply is paragraphs, and `white_space="pre-wrap"` renders them correctly. That also
closes a boundary rather than defending one: Reflex's markdown pulls in `rehype-raw`, so a typed
`<img onerror=…>` would reach the DOM through the person's own bubble.

**The evidence bundle's English stays on the rail.** `EvidenceBundle.blocking` holds sentences
like "stock did not run" and `Check.detail` is the same register — engineering artifacts written
to be read in a PR. `loop.py` already tells the person, in Spanish, which check blocked the
answer. So the checklist renders the translated check name, outcome and a Spanish confidence
phrase, and the raw reasons go to the audit rail, captioned as the verifier's own words. The rail
is where that vocabulary is already at home: mono, English event names, raw payload lines. An
AST scan asserts `State.blocking` is read by `trace_panel.py` and by nothing else — a text scan
reported this decision's own prose, which is the failure a text scan always has here.

Thirteen mutation probes were run against the new suite, each failing on the test that names it:
words moved onto an unmeasured surface, a loose hex, a light token imported into the rail, the
bundle's English put in the checklist, a refusal kind left without a panel, the refusal red spent
on a rate limit, an icon-only control left unnamed, `Check.detail` rendered, a Var `class_name` on
`rx.form`, a rail that ignores the header's height, minor units on a card, prose without its
quote rule, and a waiting row with no live region.

### 2026-07-30 · The OAuth callback is a route on the api_transformer, proven before it was built

Huella needs one thing Brújula does not: a server-side URL Strava can redirect a browser to.
Reflex has no route decorator for that, and the shape the docs point at — `rx.App(api_transformer=
<Starlette>)` — collides with the one thing production does that development does not. With
`__REFLEX_MOUNT_FRONTEND_COMPILED_APP=true`, Reflex appends a `Mount` that serves
`.web/build/client` and answers *any* path. If that mount is ahead of our callback, Strava gets a
404 with a valid `code` in the query string, once, in production, on a URL we cannot re-drive
without a fresh authorization. Reading `reflex/app.py:747-771` says it works — the frontend mount
goes on `asgi_app.routes`, Reflex's own router, and that whole router then becomes a single
`Mount("")` inside our Starlette, which Starlette matches after everything already registered.

Reading was not enough to build on, so `scripts/spike_api_transformer.py` runs it: a throwaway
Reflex app, a real `reflex export --env prod`, `granian --factory` with both `__REFLEX_*` flags
set, and fourteen probes. The callback answers with the query string intact; a missing `code`
gets *our* 400 rather than a static 404; `POST` gets 405, which a swallowed path could not
produce; a sibling path under the same prefix gets 404, so the win is per-route and not a
prefix takeover; and `/`, `/ping` and the `/_event/` websocket upgrade all still serve. A route
appended to the transformer *after* the `rx.App(...)` line also wins, because the mount happens
inside `App.__call__` at worker boot. `make spike-oauth` re-runs the whole thing; it is slow
because of the export, so it is not part of `make check`.

Two findings the reading had not predicted, and both changed the design.

**The transformer's own `lifespan=` is dead code.** `App.__call__` ends by constructing
`Starlette(lifespan=self._run_lifespan_tasks)` and mounting everything else into it, and a mounted
ASGI app is never handed a lifespan scope. The spike's lifespan wrote a marker file; the marker
was never written. This is the failure mode that costs the most to find later, because a startup
hook that silently does not run looks like a bug in whatever depended on it — a token refresher
that never starts, a client never warmed. Huella's startup work goes through
`app.register_lifespan_task`.

**Reflex puts its own CORS policy on our route, and the default mirrors any origin.**
`App._add_cors(api_transformer)` runs on the line above the mount, so this is not something we
opt into. `cors_allowed_origins` defaults to `("*",)` and `_add_cors` passes
`allow_credentials=True`; probed under granian, `Origin: https://evil.example` came back as
`access-control-allow-origin` on the callback itself. So `apps/huella/rxconfig.py` pins
`cors_allowed_origins`, and phase two of the spike proves the narrowing does what it should: the
foreign mirror is gone, ours is kept, `/ping` and `/` and the websocket are untouched, and
Strava's redirect is unaffected because a top-level navigation carries no `Origin` at all and CORS
never applies to it.

That pin is defence in depth and not the guarantee, which is the reason the callback's *response*
is the actual mitigation: it exchanges the code server-side and answers with a redirect and an
empty body. CORS is a policy a browser applies, not one a server enforces, so anything the
callback returns should be assumed readable. Nothing worth reading is returned.

The unit tests in `tests/test_contracts.py` pin the mechanism rather than re-run the spike: two
tests establish Starlette's list-order semantics with no Reflex in the picture — including the
inverse, that a route after a `Mount("")` is unreachable, which is the shape whose symptom is a
404 on a route that plainly exists — and AST scans over `reflex/app.py` assert the transformer is
mounted exactly once at `""` and that the frontend mount still goes on `asgi_app`, not on ours.
Nine mutation probes were run against the new suite: moving the mount to a prefix, deleting it,
moving the frontend mount onto the transformer, and turning `allow_credentials` off are each
caught by the test that names them.

`make spike-oauth` and those tests are a deliberate pair, recorded in the maintenance contract.
The tests are fast and catch a Reflex refactor; only the spike catches the compiled frontend
winning, because that requires an actual export and an actual server.

The remaining blocker on Huella's OAuth is unchanged and is Sebastian's: a registered Strava app,
with `https://huella.web.vespiridion.org/oauth/strava/callback` as the redirect URI. The spike is
deliberately credential-free — its handler echoes the query string instead of exchanging it — so
the serving question is answered while the registration is still pending.

### 2026-07-30 · A verifier may excuse a run only on evidence, never on the shape of the answer

`scripts/verify_brujula.py` is the only thing in the repo that runs a real turn — the test
suite is offline by construction and `reflex export` proves the tree compiles without ever
invoking a handler. So it is the last line, and it had a hole in it.

Both live checks began with `if state.error or state.advice_kind == "insufficient_evidence"`,
printed a WARN naming "the documented storefront 429 lockout (AGENTS.md)", and returned. The
detail they printed was `state.error or "; ".join(state.blocking)`. In the real 429 case both
of those are empty — the evidence bundle *accepts* `insufficient_evidence`, and an accepted
bundle has nothing in `blocking`. So the script's own diagnosis printed with no evidence
beside it, and it was reached by every path that fails to answer: a broken model, a misfired
intent gate, a retrieval that legitimately found nothing, a turn that emitted no trace at
all. Five PRs merged with this script reporting green. It would have reported green on a
Brújula that never contacted COROS.

This is the failure the knowledge base names directly — `Code As Agent Harness.pdf` §5.2.2,
"if the verifier is weak, the agent will learn to optimize against the wrong signal", and its
companion warning that an agent "may pass visible tests while exploiting weak or incomplete
test suites". Here there was no agent optimizing against it, which is worse: nobody was
adversarial and it still passed everything.

The excuse now comes from `throttle_evidence()`, which walks the turn's own trace for a
`catalog.unavailable` event carrying `rate_limited`. That event is emitted by
`CatalogUnavailable.__init__` and carries the status, the URL and the cooldown detail, so the
WARN prints what actually happened. Everything else that fails to answer is now a FAIL that
reports `advice_kind`, `error`, `blocking` and the tail of the trace. The empty `blocking`
list is deliberately in that output: it is the thing that proves the bundle accepted rather
than blocked, which is the distinction that took a debugging cycle to see.

`tests/test_verify_brujula.py` pins the judgement rather than the plumbing — the live checks
reach the network and cannot be unit-tested, but *what counts as an excuse* is pure, and it
is the part that was wrong. It loads the script by path on purpose: `scripts/` is not a
package and must not become one, since adding a fourth `pythonpath` root to reach one helper
would put every script name into the suite's import namespace.

One of those tests exists in its current form because a mutation probe caught it being
useless. Written the obvious way — a `model.error` event with a bare `{"status": 429}` — the
"a rate limit from elsewhere is not an excuse" test passed even with the event-name check
deleted, because the payload had no `rate_limited` key for the mutant to find. It now carries
`rate_limited: True`, so only the event name can reject it. Six probes, six caught.

Found alongside it and fixed in the same commit: `catalog._get`'s refusal inside the cooldown
rendered `cooldown_remaining()` — time *left* — as "refused N s ago". The same number,
described as its own opposite, in the string that reaches the trace panel and this script. A
diagnostic that lies about time is expensive precisely when it is being read.

### 2026-07-30 · The token endpoint is the one on the host that is not moving

Strava exposes the token exchange at both `https://www.strava.com/oauth/token` and
`https://www.strava.com/api/v3/oauth/token`. Measured 30 Jul 2026 by POSTing
`grant_type=authorization_code` with a bogus `client_id` to each: both answered `400` with
byte-identical bodies naming `client_id` invalid, so both are live and both process OAuth.
Only the `/api/v3` form appears in the docs' curl examples, and there is a Strava community
thread titled "'oauth/token' endpoint not working".

`client.py` uses `/oauth/token`, and the reason is the migration rather than the docs. The
1 Jun 2026 changelog entry moves the API base from `https://www.strava.com/api/v3` to
`https://api-v3.strava.com` on 4 Jan 2027, and says the OAuth host does not move. So
`/oauth/token` is the path that survives untouched and `/api/v3/oauth/token` is the one that
has to be revisited. Both are recorded in `AGENTS.md` with the measurement, because the
failure mode here is a reader comparing the code to the docs and "fixing" it.

Worth recording how this entry came to exist. The registry originally said the token endpoint
was `/oauth/token`; I read the docs, found only `/api/v3/oauth/token` in them, found the
community thread, and rewrote the registry to say the existing value was **wrong** — then told
the implementing lane to change it. None of that was a measurement. Two requests settled it in
about a second and showed the original value was fine. The repo's standing rule is "never
guess an API or payload shape — read the fixture, run the call, or read the source", and
documentation plus a forum thread is none of the three.

### 2026-07-30 · What refuses us at the storefront is not settled, and the record says so

`scripts/verify_brujula.py` cannot demonstrate Brújula on live data: every live turn this
afternoon ended `insufficient_evidence` because `products.json` answered `429`
`local_rate_limited`. That is a P0 against the success criteria, and this entry exists because
the investigation produced a clear-looking answer that then fell apart. The measurements are
worth more than the conclusion, so they are recorded and the conclusion is not.

All against `https://coros.com.co/products.json?limit=250`, one IP, 30 Jul 2026:

1. After ~5.5 min with no requests at all: `curl` → **200**, 297 399 B.
2. After ~6.7 min of quiet, `requests` deliberately first: `requests` → **429**. `curl` three
   seconds later → **200**. `requests` again with `User-Agent: curl/8.5.0` → **429**.
3. Within the next minute: `curl` over HTTP/2 → **200**. `curl --http1.1` → **200**. `httpx`
   over HTTP/1.1 → **200**. `requests` → **429**.
4. About thirty seconds later: `httpx` → **429** on its first read, pooled and unpooled alike.
   `requests` → **429**. The UCP endpoint answered `httpx` in the same minute, so nothing
   IP-wide was down.

What those support: the refusal is **not** a single global per-IP window, because `curl` was
served in the same minutes `requests` was refused. It is **not** the User-Agent, because
`requests` wearing curl's UA was still refused. It is **not** the HTTP version, because `curl`
was served over both. And `AGENTS.md`'s "a lone request is served throughout a lockout" is at
best incomplete: in (2) the lone request that went first was refused.

What they do **not** support is the conclusion I reached and stated out loud between (3) and
(4) — that the `requests`/urllib3 stack is what COROS refuses, presumably by TLS fingerprint.
Step (4) killed it: `httpx` was refused too, thirty seconds after being served. The pattern
fits per-client-fingerprint token buckets that an afternoon of probing drained at different
rates just as well as it fits a fingerprint block, and the second story also explains why
`requests` — the client every one of today's bursts and retries went through — is the one that
has never once been served.

**The experiment that would settle it, and the reason it has not been run:** a genuinely long
quiet period from this IP, 30 minutes or more with nothing touching coros.com.co, and then
**one** `requests` call as the first request of the window. Served means buckets, and the
transport is fine. Refused means the client is the discriminator, and this module has to move
to httpx. It has not been run because running it requires *not* running anything else, and
because continuing to probe is what created the hole — each experiment spends the budget the
verification itself needs.

Two things follow regardless. `catalog.py`'s docstring no longer states the no-pooling rule as
a COROS fact: the measurement behind it is Decathlon's, and it is now labelled as transplanted
and untested here. And the 429 handling is vindicated either way — the app refused to turn
"could not look" into "found nothing", said so in Spanish, and emitted `catalog.unavailable`
with the status, which is the only reason any of this was visible.

### 2026-07-30 · It is the TLS fingerprint, and `curl_cffi` is the fix

Settled, a few hours after the entry above said it was not. The experiment that entry described
was run, and then carried further because its first answer was also wrong.

After **30+ minutes of complete silence** from this IP, `requests` as the very first request of
the window: **429**. That alone kills the token-bucket story — a bucket that has not been
touched for half an hour has refilled. The script printed "the client is the discriminator —
`catalog.py` must move off `requests`", and that verdict was wrong too. In the same window,
minutes apart: `httpx` **429**, `curl` **200** (297 399 B), `requests` **429** again. Both Python
clients are refused. Moving between them changes nothing.

So what distinguishes `curl` from both? Not the User-Agent: `requests` wearing `curl/8.5.0` is
refused. Not the headers either, and this is the one that took a specific test — `requests`
sending curl's **exact** set and nothing else (`Host`, `User-Agent`, `Accept: */*`, with
`Accept-Encoding` and `Connection` removed) is still refused. Not the HTTP version: `curl` is
served over h2 and over `--http1.1` alike. Not connection pooling, which was the module's
original theory and was never a COROS measurement in the first place.

That leaves the TLS handshake, and impersonating it proves it:

| client | result |
|---|---|
| `curl_cffi`, `impersonate="chrome"` | **200**, 297 399 B |
| `curl_cffi`, no impersonation | **403**, 6 924 B Cloudflare challenge |
| system `curl` | **200**, 297 399 B |
| `httpx` | **429** |
| `requests` | **429** |
| `requests` with curl's exact headers | **429** |

Cloudflare is classifying the ClientHello. Python's `ssl`-module fingerprint — shared by
`requests` and `httpx`, which is why they behave identically — is answered `429
local_rate_limited`; a Chrome fingerprint is served. The three different statuses are three
different verdicts from the same bot-management layer, not three different load conditions, and
that is why this looked like a rate limit for an entire afternoon: the body really does say
`local_rate_limited`.

**`curl_cffi` with `impersonate="chrome"` is the fix**, and `impersonate` is load-bearing —
without it the same library gets a `403` challenge page. The two failures must stay
distinguishable in code: a `429` is a throttle and sets `rate_limited` on the trace event, a
`403` is a fingerprint problem and must not, or the verifier's "COROS refused us" excuse comes
back wearing a disguise.

**Scope, and it is narrower than it looks.** The UCP endpoint `/api/ucp/mcp` is **not**
fingerprint-gated — it answered `httpx` in the same minutes the storefront was refusing it. So
`ucp.py` stays on httpx and only `catalog.py` moves. The two really are different limiters, which
is what the original docstring said for a different reason and got right by accident.

**What this costs, honestly.** A new dependency, and one whose entire purpose is to look like a
browser to a bot-management layer. Worth saying plainly: this reads a public product feed that
the storefront serves to any browser, at one request per turn, with a circuit breaker that
refuses to retry a refusal — the same feed a person gets by opening the URL. It is not a
scraper and it is not evading a rate limit; the rate limit was never what was refusing us.

**What the earlier entry got right, and should be read as a lesson rather than deleted.** It
recorded the measurements and refused to publish the conclusion, which is the only reason the
conclusion could be corrected twice — once from "buckets" to "requests specifically", and again
from "requests specifically" to "every Python client". Both intermediate verdicts were confident
and wrong. The measurements were not.

---

### 2026-07-30 · Strava's orange is `#FC5200`, and the mark loses a surface because of it

`huella/ui/theme.py` carried `STRAVA = "#FC4C02"` under a comment reading "Verbatim from
Strava's brand assets". It was not verbatim from anything. It is Strava's *older* orange,
which most of the web still repeats, and the file asserted it rather than measuring it —
`tests/test_huella_theme.py` pinned `theme.STRAVA == STRAVA_ORANGE` with both sides typed
from the same memory, so the assertion could never have caught it.

Measured 30 Jul 2026 at three independent boundaries, all agreeing on `#FC5200`:

| boundary | result |
|---|---|
| all six orange SVGs in Strava's own `1.1-Connect-with-Strava-Buttons.zip` and `1.2-Strava-API-Logos.zip` | exactly one colour each, `#FC5200` |
| `api_logo_pwrdBy_strava_horiz_orange.png`, decoded pixel by pixel | `#FC5200` on every opaque non-black pixel |
| developers.strava.com/guidelines, §3 "Linking to Strava Data" | names `#FC5200` for the link treatment |

The two bundles are linked from the guidelines page itself, so this is the vendor's own
shipped artifact rather than a third-party summary — which is the distinction the storefront
entry above was written to teach.

**What it costs: `SHEET_2`.** The declared pairs were `STRAVA on SHEET 3.40:1` and
`STRAVA on SHEET_2 3.08:1`. With the real orange those become **3.31:1** and **2.9977:1** —
and 2.9977 is under WCAG 1.4.11's 3:1 graphic floor. There is no lever: the assets carrying
the colour may never be recoloured, so a darkened variant to reach the floor is the one fix
the brand terms forbid. `theme.EDGE_ON["STRAVA"]` is therefore `("SHEET",)` alone, and the
attribution block sits on pure white rather than on the inset sheet tint.

**The suite would have passed anyway, and that is the more useful half of this entry.**
`ratio()` rounded to two decimals *before* the floor comparison, so 2.9977 became exactly
3.0 and `assert measured >= NON_TEXT` held. Every value in [2.995, 3.0) was invisible to a
3:1 floor, and [4.495, 4.5) to AA. The helper now rounds to four places — still far inside
the 0.01 tolerance the stated-ratio comparison allows, and no longer able to round a failure
up into a pass. Mutation-probed three ways: reinstating `#FC4C02`, re-declaring `SHEET_2`,
and reverting the helper each turn the suite red.

`tests/test_brujula_theme.py` still carries the two-decimal helper, and it was audited rather
than left as a known unknown: every declared pair in **both** apps was recomputed exactly and
checked against the two dead bands — `[2.995, 3.0)` for the graphic floor and `[4.495, 4.5)`
for AA. **Zero pairs land in either.** So Brújula has the latent hole and nothing currently
falls into it; it was left alone rather than changed, because that suite is verified and
green and the fix belongs with the next colour that actually needs it.

**The tripwire fired, and it was right to.** The suite asserted `apart(STRAVA, FLAG) < 25`,
with the comment "past the separation Brújula's mark uses as tellable-apart … re-read it
before relying on either claim". The real orange is 25.6° from `FLAG`, so it fired. Re-read,
the docstring's conclusion survives but its stated reason does not: hue was never what made
the two one glyph. `STRAVA on FLAG` is **1.28:1**, and two marks that close in luminance are
one mark at any angle. The assertion now keys on the contrast, which is the figure the
never-share-a-surface rule actually rests on.

**Assets are vendored rather than hot-linked**, byte-identical, at
`apps/huella/assets/strava/`. Shipping them is what the guidelines' own download links are
for, and a copy in the repo is the only way "unmodified" is checkable — the sha256 of each
file is what a future edit would have to change.

---

### 2026-07-30 · Red means "do not lean on this", not "the window is thin"

`huella/ui/theme.py`'s docstring said red was the uncertainty flag and *only* that —
"an app whose whole premise is uncertainty-aware reasoning cannot spend its flag colour on
a generic error". Its own registries, in the same file, had never agreed: `OUTCOME_COLOR`
maps `fail` to `FLAG` and `LEVEL_COLOR` maps `error` to `FLAG_INK`, and both were written
deliberately. So the rule and the app disagreed, and the app was three commits older.

Two ways to close it, and they are not symmetrical.

**Push the errors to amber.** This is what the docstring literally asks for, and it makes a
hard failure and a merely thin window the same colour. That is the *exact* collapse the same
file refuses two paragraphs later when it declines COROS's `--color-warning: #ff706b` — "a
second red makes a window that will not hold weight and a refused request the same answer".
Declining a second red to protect a distinction, then destroying the same distinction from
the other side, is not a rule; it is two rules that never met.

**Widen the rule, which is what shipped.** Red now says **"do not lean on what is on
screen"** and covers three states that are one state to whoever is reading: a window too
thin or stale to reason from (`CONFIDENCE_COLOR["none"]`), a check that ran and failed
(`OUTCOME_COLOR["fail"]`), and a turn that broke (`LEVEL_COLOR["error"]`). Amber keeps its
own job unchanged — **"usable with reservations"** — and a refusal arrived at correctly stays
uncoloured, because "no compres nada" is a right answer and not a degraded one.

**The boundary that actually needs policing is red-versus-amber, not red-versus-error.**
`advice.py` already drew it correctly and for the right reason: "no pude confirmarlo" is a
check *nobody could run*, which is amber, and a check that ran and failed is red. Those are
different sentences about the same bundle and they were never in danger of being confused
until the docstring implied both belonged to amber.

**No token, no mapping and no rendered pair changed.** This is prose catching up to code
that was already written this way in both registries and in every module that renders them —
which is the argument for the direction: describe the app that ships rather than make the
app chase a docstring. The moves
were `theme.py`'s docstring, its `FLAG`, `OUTCOME_COLOR` and `UNCERTAINTY` comments,
`connect.py`'s now-dangling pointer to the old rule, `advice.py`'s two restatements,
`tests/test_huella_theme.py`'s stray-red failure message (which restated the old rule as the
reason for a check that is really about the palette), and `docs/VISUAL-BRIEF-HUELLA.md`.

**`theme.UNCERTAINTY` keeps its name and its test keeps its assertion.** The scan rejects a
saturated red outside the five-token family, and that guards the *palette* — a sixth red
would be a second way of saying one thing — not the *usage*, which is what widened. Renaming
it would have been the change that looked like work.

**No ratio was restated.** `tests/test_huella_theme.py` recomputes every `X on Y N.NN:1` in
theme.py's prose from the two tokens it names and refuses a bare figure outright, so amended
prose is the exact place a stale number would land. The one figure the new text carries,
`FLAG on INK 4.02:1`, is the one it already carried; 121 theme tests green after the edit.

---

### 2026-07-30 · The pre-Strava vault value is `""`, and `CHANGE_ME` was a live bug

`vault.yml.example` shipped `vault_strava_client_id: "CHANGE_ME"` and the same for the
secret, alongside every other key it marks that way. For those two it is not a placeholder
convention — it is a value that changes what the app renders, and the wrong one.

Measured 30 Jul 2026, both halves:

| what | result |
|---|---|
| `env.j2` rendered with `StrictUndefined`, keys present but `""` | clean file, `STRAVA_CLIENT_ID=` |
| same template, keys **absent** | `UndefinedError: 'vault_strava_client_id' is undefined` |
| `client.is_configured()` with `""` | **False** |
| `client.is_configured()` with `"CHANGE_ME"` | **True** |

Three consequences, in the order they bite.

**Absent is not empty.** `roles/huella/templates/env.j2` references both vars
unconditionally and `error_on_undefined_vars` is on, so deleting a line does not "leave
Strava unconfigured" — it kills the play at Jinja render. The error names the variable and
nothing else, so it reads as a templating fault rather than a missing integration, and the
search starts in the wrong file.

**`CHANGE_ME` is worse than absent, because it succeeds.** `is_configured()` is
`bool(client_id() and client_secret() and redirect_uri())`, and `STRAVA_REDIRECT_URI` is
always templated to a real URL from `huella_host`. So any truthy placeholder makes all three
truthy, `connect.py` takes the configured branch, and Huella renders the official "Connect
with Strava" button — which redirects to Strava with `client_id=CHANGE_ME` and fails there,
in the vendor's UI, after the person has already committed to the action. The app's own rule
is that no dead button is ever offered; a placeholder that satisfies a boolean breaks it.

**So `""` is the correct value, not a stopgap.** It is what makes `is_configured()` false and
`connect.py` say "esta instancia no tiene credenciales de Strava configuradas" — a true
sentence about the deployment, rendered instead of a control that cannot work. Phase 6
replaces the two strings and changes nothing else.

The general shape is worth keeping: **a placeholder convention is only safe where the code
tests for the placeholder, and this codebase tests for truthiness.** `CHANGE_ME` is fine for
`vault_brujula_password`, where any non-empty value is a working gate. It is not fine for a
credential that is validated by a third party.

### 2026-07-30 · Huella's stylesheet was ahead of its components, and the gate was the gap

Huella's UI was reported as thin next to Brújula's. Measured rather than assumed, and the
premise held in one place and inverted in another.

**Where it held.** The gate was a left-aligned lockup over a single row — field, reveal
toggle and submit crammed together — with no reason given for the lock, no busy state, no
error affordance beyond a line of text, and no footer. Brújula's, by contrast, is a centred
lockup, a blurb, a stacked full-width field/error/submit, and a footer. The gate was also
the one Huella surface with no module of its own: it lived inline in `app.py`, which is why
it lagged while `advice.py` (677 lines) and `trace_panel.py` grew past their Brújula
counterparts.

**How the gap was found without guessing.** Three `hu-` rule sets were defined in
`assets/huella.css` and named by no component: `hu-shake`, `hu-dock` and `hu-skip`. Each
belonged to exactly one of the unfinished surfaces — the gate's refusal, the composer, and
the skip link — and `hu-shake`'s own comment names this screen ("the gate card is the whole
page, so the refusal has to be felt"). The stylesheet had described the intended design and
the components never adopted it. `tests/test_huella_ui.py` said so in a docstring and
treated it as permanent.

**What was NOT done, and why.** No entrance animation was added. `huella.css` states the
kit's arrival is "the only thing in the app with an entrance — nothing else rises", and
`test_the_animated_classes_are_the_three_this_suite_knows_about` pins `ANIMATED` to exactly
`hu-kit`, `hu-pulse`, `hu-shake` — so an `hu-rise` would have failed a test as well as
contradicted a stated decision. The gate card does not rise. No Brújula token crossed over
either: the two palettes are separately measured, and the gate is INK on DASH with a TRACE
rule, not paper and brass.

**One thing the browser caught that no test could.** With the well styled and `auto_focus`
on the field, three rings stacked: Radix's own inset ring, the well's `_focus_within`, and
the stylesheet's global `:focus-visible` outline on the `<input>`. A style prop cannot fix
the last one — Reflex puts props on Radix's TextField **Root** while the outline is on the
`<input>` inside it — which is precisely the job `.hu-dock` already did for the composer.
So the gate's well carries `hu-dock` too, and the class is now documented as belonging to
any wrapper that draws its own field rather than to the composer alone.

**Unrelated finding, recorded here because it is the same failure mode one app over.**
Brújula's components name `bj-halo`, `bj-rise`, `bj-shake`, `bj-clamp-2`, `bj-scroll` and
`bj-rail`; `assets/brujula.css` defines only `.brujula-*` and has no `rise` at all; and
`brujula/app.py` passes `stylesheets=[FONT_HREF]`, never registering the file. Verified
against production: neither prefix appears in the served bundle, and `/brujula.css` is
reachable but unlinked. Every class-driven behaviour in Brújula — the halo, the refusal
shake, the custom scrollbars, the clamp, the skip link, the sr-only text — is inert. Not
fixed here: it is a different app, and AGENTS.md's mark entry asserts the halo *is*
`bj-halo` with a reduced-motion swap, so the registry and `tests/test_brujula_brand.py`
have to move in the same commit.
