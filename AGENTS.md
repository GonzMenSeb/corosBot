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
- **Product types are unreliable for device identification, and on one product the field
  is simply wrong.** PACE 4 reports an empty `product_type`, and `coros-dura` reports
  `Relojes GPS` for something that is not a watch: COROS's own homepage labels that
  product card `alt="Ciclocomputador COROS DURA"`. Device matching uses the hand-authored
  registry in `devices.py`, joined by **product id** with the **handle** as a second key,
  **never** by `product_type`. Enforced by an AST scan in `tests/test_devices.py` — a
  docstring may name the field, code may not read it.
- **The DURA takes no strap.** It is a bike computer; no product in the feed lists a strap
  for it, and the 24 mm straps that exist say "solo compatible con el APEX 4 46mm". A
  `strap_mm` for it would be invented.
- **Strap widths, and where each one is written down.** All four verified 30 Jul 2026:
  PACE 4 → 22 mm, APEX 4 42 mm → 22 mm, APEX 4 46 mm → 24 mm, NOMAD → 24 mm,
  VERTIX 2/2S → 26 mm. Four live descriptions read "Solo compatible con el APEX 4 42mm,
  PACE 4, PACE Pro, PACE 3, APEX 2 Pro, APEX Pro … `Ancho: 22 mm`"; two read "Solo
  compatible con el APEX 4 46mm … `Ancho: 24 mm`"; three NOMAD titles say `24mm`; two
  VERTIX titles say `26 mm`. Enforced deterministically in `devices.py` — the model will
  hallucinate a strap fit, and the case size (42/46 mm) is the number it reaches for.
- **Equal width does not mean interchangeable, and the vendor says so.** COROS sells a
  24 mm strap "solo compatible con el APEX 4 46mm" and, separately, 24 mm NOMAD straps.
  Compatibility in `devices.py` is curated per product from COROS's own words; it is never
  derived from a width, and `StrapFit.strap_mm` exists to be cross-checked, not matched on.
- **A width that lives only in a handle is not recorded.** The PACE 2 and APEX 2 straps
  are `20mm-silicone-band` and `20mm-nylon-band` and no title or description states a
  width, so those two devices carry none. Handles lie here (see the storefront section),
  so `strap_width()` returning `None` for them is the rule working.
- **Ten devices are not sold in Colombia, not six.** The plan for `devices.py` listed
  PACE Pro, PACE 3, APEX 2, APEX 2 Pro, VERTIX 2, VERTIX 2S. The feed also carries straps
  for **PACE 2** and **APEX Pro** with no watch SKU, and names **APEX** and **VERTIX**
  (gen 1) in the chargers' own copy ("Compatible con COROS PACE 2/APEX/APEX Pro/VERTIX/
  VERTIX 2"). All ten are registry rows, because "we do not sell that here" has to be a
  fact and not a silence. The four that ARE sold: PACE 4, APEX 4, NOMAD, DURA.
- **"A strap for my APEX 4" is a question, not a request.** It is the only device with two
  cases and they take different widths, so `strap_width()` and `straps_for()` raise
  `CaseUnspecified` rather than pick. An empty tuple there would read as "COROS sells no
  APEX 4 straps", which is false.
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
  Colombia catalog in one request, no pagination; the only top-level key is `products`.
  Verified live 29 and 30 Jul 2026.
- **Retrieval must use `requests` MODULE, not a Session.** Reused connections are refused
  with 429, which cascades to the UCP rate limiter. See DecaBot `AGENTS.md:111-128` for the
  measured evidence; the pattern is identical here. `catalog.py` uses `requests.get()`, not
  a pooled session. Enforced by an AST scan in `tests/test_catalog.py` — the docstring is
  free to explain what a Session is, the code may not construct one.
- **`product_type` is empty on 24 of 45 products (53%)**, PACE 4 included. The four values
  in use are `""`, `Accesorios`, `Bandas`, `Relojes GPS`. Carried through normalization and
  never keyed on; `devices.py` is the registry.
- **A handle is a URL slug, not a description, and three of them lie outright.**
  `correa-de-nylon-de-24-mm-morada-para-apex-4-46-copia` is a **22 mm white silicone**
  strap for the **APEX 4 42** — title, option label and photo all agree with each other and
  disagree with the handle. Somebody duplicated a product and edited everything but the
  URL. Two more from the same afternoon's duplications:
  `correa-de-nylon-de-24-mm-morada-para-apex-4-42mm` is titled "Correa de nylon de **22
  mm** morada para Apex 4 42mm", and `correa-nylon-nomad-copia` is "Correa de **Apex 4 -
  42mm** | Edición Kilian Jornet" and has nothing to do with the NOMAD. Nothing derives a
  spec from a handle; titles and option labels are the sources.
- **`sku` is null on 30 of 126 variants** and normalizes to `""` — never the string
  `"None"`. Nothing joins on a sku; it is carried for display.
- **`"Default Title"` is Shopify's placeholder** for the 10 products with exactly one
  variant, and `"Title"` the matching option name. Both normalize to `""`: rendered
  verbatim they read to a shopper as a choice they have to make.
- **Two products are tagged `gwp-hidden`** — `camisa-blanca-hombre` and
  `camisa-blanca-mujer`, gift-with-purchase dress shirts, in stock and priced at
  $120.000. `get_products()` excludes them by default, which is why the usual count is 43
  and not 45. They are flagged, not dropped, so the discrepancy is explainable.
- **Variant labels are colour/material/series composites, not sizes.** `Serie / Material
  de la correa / Color` on PACE 4, `Color / Tamaño` on APEX 4 (where `Tamaño` is the 42 mm
  or 46 mm case), bare `Color` on 35 of them. This corrects the entry inherited from
  DecaBot about phrased shoe sizes: COROS sells watches, and the only `Talla` options in
  the feed belong to the two hidden shirts. `option_names` on `CatalogProduct` is what
  says which component is which.
- **`body_html` is not prose.** The largest is 36 160 characters of a **BeeFree email
  template** (`coros-pod-2`): meta tags, a Google Fonts link, a full stylesheet, then the
  copy. `coros-apex-4` is 19 981 characters opening with a CSS reset. So the sanitizer
  must remove the **content** of `style`/`script`/`head`/`noscript`/`svg`/`template`, not
  just the tags — DecaBot's sanitizer replaces tags with newlines and returns **pure CSS**
  for both of those products. Fuzzy CSS sniffing is *not* the fix: measured over all 45
  descriptions and all 30 articles (1 931 segments), precise opaque-element removal leaves
  zero CSS behind, while a looser sniffer ate 22 segments of real copy (store hours, an
  Instagram handle, a NIT). `coros-dura` is 2 825 characters of nothing but `<img>` tags
  and correctly sanitizes to `""` — that is a fact about the feed, not a failed fetch.
- **Tags are stripped BEFORE unescaping, and that order is load-bearing.** One live
  article contains `href="&gt;https://support.coros.com/…"`. Unescape first and that
  `&gt;` becomes a real `>` that ends the tag early, spilling the URL and a
  `style="color: rgb(255, 255, 255);"` attribute into the copy as if a human wrote it.
  Roles and tags are re-checked after unescaping, because `&lt;system&gt;` is a role
  marker too. Injection patterns cover Spanish as well as English: the storefront is
  Spanish and so is the likeliest attempt.
- **The whole cycling range is two sensors, and neither measures power.** `COROS Bike
  Cadence Sensor` and `COROS Bike Speed Sensor`, $159.000 each; the cadence one is **out
  of stock** and the speed one is not (30 Jul 2026). No product in the feed — none of the
  45 — measures power, in cycling or in running, and no title or handle names one. So
  `cycling_power` is a `capability.py` dead end while `cycling_cadence` stays **capable**:
  out of stock is `check_stock`'s answer about a product COROS makes and will restock, and
  collapsing the two says COROS does not make a cadence sensor. Both sensors pair over
  Bluetooth only — **"No compatible con dispositivos ANT+"**, their own words, which is
  what the `ant_plus` dead end rests on.
- **COROS denies running power itself, 4 916 characters past where anything can read it.**
  The POD 2's FAQ asks "¿El POD 2 mide la potencia de funcionamiento?" and answers "No, el
  POD 2 usa Effort Pace" — inside the 36 KB BeeFree template, and `DESCRIPTION_CHARS` is
  400, so the sanitised prose stops long before it. The vendor's denial exists and
  retrieval structurally cannot deliver it, which is why `running_power` is a typed dead
  end carrying the sentence rather than a hope that the model reads far enough.
- **The blog: `blogs/blog.json` is 404, and `blogs/blog.atom` serves 30 entries and
  ignores `?page=`.** `?page=2` returns the identical 30 ids. The site really has **58**
  articles under `/blogs/blog/` — that is `sitemap_blogs_1.xml`, and the 58–60 figure in
  the plan came from there — but they are not reachable through the feed, so there is no
  pagination loop to write. A second blog handle `nn` exists with no articles.

### COROS's own brand values — the anchors under both palettes

Read off coros.com.co's **inline theme stylesheet** (CSS custom properties, not hexes
picked off a screenshot). First taken live 29 Jul 2026; re-read 30 Jul 2026 from the
Wayback snapshot `20251012171246id_` because the storefront was mid-lockout, and **all
seven values match across nine months** — the palette is stable, and re-verifying it does
not need a rested IP.

- `--color-body-text: #161d25` · `--color-primary: #404040` · `--color-primary-darker:
  #212121` · `--color-sub-text: #9A9A9A` · `--color-border: #EEEEE0` · `--color-success:
  #3a8735`, on `--color-main-background: #ffffff`. `#EEEEE0` is the storefront's only
  warmth and is what Brújula's paper register is derived from.
- **`#ea2e41` is not a custom property.** It is a literal, repeated across their own
  section styles as a *call to action* — `.buy-btn`, `.button-primary`,
  `.form-launch-btn`, and section headings. Brújula inverts it to the refusal, which is a
  deliberate divergence from COROS, not a misreading: see `docs/DECISIONS.md`, 30 Jul.
- **COROS's own success green cannot carry words on white.** `#3a8735` measures 4.47:1 —
  three hundredths under AA. It is the tick and the fill; `SUCCESS_INK` says it in words.
  Their white-on-`#ea2e41` button measures 4.23:1, the same story.
- **`--color-warning: #ff706b` exists and is deliberately unused.** It is a second red,
  and a rate limit is not a refusal. Amber keeps the two answers apart.
- **They self-host PF Din Text Pro** (`PFDinTextPro-Regular.ttf`, `-Bold.ttf`, on their
  Shopify CDN, aliased `SF-Heading-font`/`SF-Body-font`). It is a licensed Parachute face,
  so we cannot ship it: Barlow stands in for the interface on the same DIN skeleton, and
  Fraunces is Brújula's own editorial voice rather than anything of COROS's.
- The `apps/brujula/brujula/ui/theme.py` docstring holds the same list with its measured
  ratios; `tests/test_brujula_theme.py` recomputes every figure written in that file from
  the two colours it names, so a stale ratio fails the build rather than a review.

### Brújula's mark and its presence dot (`brujula/ui/brand.py`)

- **The dot's amber is the storefront throttle, not a fixture replay.** DecaBot's dot is
  amber while replaying a fixture and the plan asked for the same; nothing here reads
  `fixtures/` at runtime, so that amber could never light. It is wired to
  `State.throttled` instead — COROS is rate-limiting us and the answer came off a partial
  read — and `state.py` clears that flag in its `finally`, so the amber only ever appears
  mid-turn, beside the halo. Do not "restore" a fixture mode to give it back its old
  meaning without deciding where the throttle then says so.
- **The amber pip is hollow while the green and brass ones are solid.** `BRASS vs WARN_INK
  1.17:1` — at 7px, in the same hue family, a solid amber dot and a solid brass dot are the
  same dot. The throttle pip is therefore theme.py's own throttle notice in miniature,
  `WARN_SOFT` with a `WARN_INK` rim, and it spends its ring on that rim instead of on the
  surface colour the other two use to lift off the bezel. `tests/test_brujula_brand.py`
  requires 25° of hue or 1.8:1 of contrast between any two pips.
- **Idle is green only when `gemini.api_key()` returns something**, read once at import.
  A green dot in front of a process with no key promises an answer that dies at
  `gemini.client()`.
- **The needle sits at `BEARING = 34`, and nothing in the mark moves.** The halo on the pip
  is the app's only ambient animation, and it is the class `bj-halo` rather than a style
  prop so `assets/brujula.css` can swap it for a static ring under
  `prefers-reduced-motion`. `transform` is only ever set on a `<g>`: on a shape Reflex
  renders it into CSS, where `rotate(34 16 16)` is not valid syntax.

### Strava integration (Huella only)

Re-read from `developers.strava.com/docs/authentication` and `/docs/changelog` on
**30 Jul 2026**, which corrected two entries that had been carried in from the plan.

- **The token endpoint is `https://www.strava.com/api/v3/oauth/token`, not
  `https://www.strava.com/oauth/token`.** `www.strava.com/oauth/*` is the *user-facing* half
  — authorize and revoke live there — and every API call goes through `/api/v3`. Both grant
  types (`authorization_code` and `refresh_token`) POST form-encoded to that one URL. The
  wrong path is the plausible one and it is what this registry used to say; there is a Strava
  community thread titled "'oauth/token' endpoint not working" for exactly this reason.
- **Authorize `https://www.strava.com/oauth/authorize`; revoke
  `https://www.strava.com/oauth/revoke`.** Revoke replaced `POST /oauth/deauthorize`, which
  is retired 1 Jun 2027 — never call `deauthorize`. Scopes that exist: `read`, `read_all`,
  `profile:read_all`, `profile:write`, `activity:read`, `activity:read_all`,
  `activity:write`. Huella asks for `read,activity:read_all,profile:read_all`.
- **`redirect_uri` is validated against the app's "Authorization Callback Domain", not
  against a full registered URI.** Verbatim: "Must be within the callback domain specified by
  the application. `localhost` and `127.0.0.1` are white-listed." So local dev needs no
  second registration and the dev port is free to move. Do not write exact-URI-match logic.
- **The API base URL moves to `https://api-v3.strava.com` on 4 Jan 2027, and is not live
  yet.** Until then it is `https://www.strava.com/api/v3`, spelled once as a module constant
  so the migration is one line. A widely-circulated third-party summary gives the new host as
  `www.api-v3.strava.com` with a Jun 2027 date; **both are wrong** — the changelog is the
  source. The OAuth host does not move.
- **Access tokens expire in 6 hours; a refresh invalidates the previous refresh token
  immediately.** Verbatim: "Please expect that this value can change anytime you retrieve a
  new access token", and once a new one is returned "the older code will no longer work" —
  applications must "persist the refresh token contained in the response, and always use the
  most recent refresh token." Persist the new pair atomically or the user is locked out with
  no way back but a fresh authorization. `_SESSIONS` dict update is not atomic across a
  retry; use a lock.
- **Rate limits:** 200 requests per 15 minutes + 2000 per day (overall); 100 per 15 min +
  1000 per day (read endpoints). The API returns `X-RateLimit-*` and `X-ReadRateLimit-*`
  headers, each a comma-separated `15min,daily` pair. A 429 response means the request was
  refused, never silently drop it to an empty list. Strava sends no `Retry-After`: the
  15-minute windows are wall-clock quarter-hours, so the wait is computed to the next one.
- **Activities have no `type` enum.** They carry a `sport_type` string which may be any of
  40+ values (e.g. `"AlpineSki"`, `"NordicSki"`, `"BackcountrySki"`, `"IceSkate"`,
  `"InlineSkate"`, `"RollerSki"`, `"Skateboarding"`, `"Snowboarding"`, `"Snowshoeing"`,
  `"Trail Run"`, `"TrailRun"`, `"TrackRun"`, `"Run"`, …). The model must not invent
  categories, and **the list grows**: the changelog added Basketball, Cricket, Dance, Padel,
  Physical Therapy and Volleyball on 30 Apr 2026. Parse it as `str`; an enum here is a
  self-inflicted outage on Strava's next release.
- **Deprecated 1 Sep 2026, so nothing may be built on them:** `/clubs/{id}/activities`,
  `/clubs/{id}/admins`, `/clubs/{id}/members`, and `segments/explore` (restricted to an
  approved Extended Access tier). `athlete/clubs` survives. Huella needs none of them.
- **Standard Tier API access now requires an active paid Strava subscription** — 1 Jun 2026
  for new developers, 30 Jun 2026 for existing ones, currently $11.99/month; developers who
  were already active were offered three months free by emailed code. Strava's stated reason
  is AI scraping, with developer applications up 448% year-to-date. **This is a blocker on a
  person, not on code:** Huella's `live` Strava tests and the real OAuth round-trip in the
  success criteria cannot run until the repo owner holds a subscription *and* has registered
  an app. Everything else about Huella — the client, the privacy boundary, the agent, the UI,
  the whole offline suite — is built and tested without it, and `tests/test_strava.py`'s live
  half skips when `STRAVA_CLIENT_ID` is unset rather than failing.

### Gemini and the model client (`packages/coros_core/gemini.py`)

All of these were run against `google-genai` 2.14.0 on **30 Jul 2026**.

- **Model `gemini-3.6-flash`, present in the live model list** as `models/gemini-3.6-flash`
  under the shared credential. Spelled once, in `gemini.MODEL`; `generate()` defaults to it
  so no call site repeats it, and an AST scan in `tests/test_gemini.py` enforces that.
- **`tools=` must hold `types.Tool`, not `types.FunctionDeclaration`.** A bare declaration
  raises `AttributeError: 'FunctionDeclaration' object has no attribute
  'function_declarations'` from *inside* the SDK, before any HTTP call, for a
  `GenerateContentConfig` and a plain dict config alike — so it reads as a bug in our code.
  `gemini.as_tools()` collects bare declarations into one `Tool` and `generate()` runs every
  config through it; a `Tool`, a dict and a plain callable pass through in place. Pinned in
  `tests/test_contracts.py`.
- **A `genai.Client` nobody holds closes its own transport.** `Client.__del__` calls
  `close()`, and neither `client.models` nor `client.aio.models` keeps the client alive, so
  `genai.Client(...).models.generate_content(...)` in one expression raises
  `RuntimeError: Cannot send a request, as the client has been closed.` — never an API
  error, never a code a caller can branch on. This is why `gemini.client` is `lru_cache`d
  and why `generate()` binds the *client* for the whole retry ladder instead of
  `.aio.models`. Do not "simplify" either one.
- **`HttpOptions.timeout` is milliseconds.** `120_000` is two minutes. Without it a stalled
  request hangs the turn forever, which from the outside is a crash.
- **`generate_content` takes `model`, `contents`, `config` and nothing else** — there is no
  `previous_interaction_id`. History is threaded client-side as `list[types.Content]`.
- **A `response_schema` may not be a model with `extra="forbid"`.** Pydantic renders it as
  `additionalProperties: false`, the SDK passes it through, and the API answers
  `400 INVALID_ARGUMENT · Unknown name "additional_properties" at
  'generation_config.response_schema.properties[0].value.items'`. Every policy model in
  `models.py` sets `extra="forbid"` deliberately, so **none of them can be a response
  schema**: `loop.py` carries plain wire models (`loop.SCHEMAS`) and validates them into
  the frozen ones in code, which is where a key outside the allowlist is dropped anyway.
  A `str | int | bool` union renders as `anyOf` and *is* accepted — `budget_minor` came
  back as an int on the same probe. Pinned in `tests/test_brujula_agent.py`.
- **Every `function_call` in one model turn needs a matching response part in ONE
  `Content`.** Split across two, or one left unanswered, and the *next* request 400s —
  which surfaces a turn later, at a stage that looks unrelated. `loop._retrieve` answers
  even the calls it refuses for budget, and answers them with `TIMEOUT`: "we stopped" is a
  different sentence from "there is nothing".
- **429 is ordinary here, not exotic.** One per-project quota is shared by Brújula, Huella
  and DecaBot (`vault_decabot_gemini_api_key`, by decision — there is no key pool and no
  `bind_key()`, unlike DecaBot, which rotates one for a QR-code audience). `gemini-3.6-flash`
  also answers 503 "experiencing high demand" intermittently. `generate()` retries
  `{429, 500, 503, 504}` on a `(1.0, 3.0, 7.0)` s ladder — four attempts total — and then
  re-raises the SDK error with `.code` intact, because which refusal it was decides what the
  person is told. `errors.ClientError` and `errors.ServerError` both carry `.code`/`.status`.
- **`.env` is loaded from two named absolute roots, and finding neither is not an error.**
  `load_dotenv()` with no argument resolves against the *calling* file and silently finds
  nothing. `gemini.env_candidates()` returns the repo root and — one directory shallower —
  the flattened `/app` layout the image uses. A container is handed its environment by
  docker's `env_file` and ships no `.env` at all.
- For the app-layer Gemini facts this repo has not re-verified — `grounding_metadata` being
  `None`, why built-in search may never share a request with custom tools, and
  `response_schema` returning a typed `response.parsed` — read DecaBot `AGENTS.md:196-215`.
  **Do not restate them here.**

### Rate limits and pacing

- **No measurable rate limit on COROS UCP yet.** Decathlon's documented 20 sequential / 40
  concurrent is a safe working assumption; DecaBot `AGENTS.md:66-81` covers pacing policy.
  COROS has not triggered the same lockout. If it does, apply the same latch-pacing rule
  (Semaphore, never un-latch on success).
- **Storefront has its own limiter**, separate from UCP, and it is the harsher of the two.
  Measured against `products.json` on 30 Jul 2026:
  - It tolerates a short burst — **4 requests over ~20 s were served** — then refuses with
    HTTP **429**, `Retry-After: 60`, Cloudflare, body `local_rate_limited` as `text/plain`.
  - **The hint is not honest and polling prolongs the lockout.** After one 429, **30
    consecutive requests spaced 10 s apart were all refused, for five unbroken minutes.**
    It cleared after ~100 s of *complete quiet*, and a later lockout outlasted 120 s.
  - So **a 429 is never retried and never polled.** It latches, and every request inside
    the cooldown fails immediately as a typed `CatalogUnavailable(rate_limited=True)`
    **without touching the network**. Our own retry is the thing keeping the door shut.
    Two divergences from DecaBot, which spaces and keeps sending, and retries a 429 to its
    budget. The latch still **decays** (unlike `ucp.py`'s): the feed is one request per
    turn, so a permanent latch would end the session over one transient refusal.
  - Mid-lockout some attempts get **no response at all** — the connection is dropped and
    `requests` raises `ConnectTimeout`. A network exception is the same condition in
    different clothes, and is retried only while the latch is open.
  - **The `User-Agent` is not the discriminator.** A browser UA was served four times in a
    row and then refused exactly like `python-requests/2.x`; the apparent success was the
    cooldown expiring. Do not "fix" a 429 by spoofing a header.
  - Consequence for `make verify` and for demos: **`tests/test_catalog.py`'s live probes
    skip rather than fail on a lockout**, and two turns inside a minute can legitimately
    fail to reach the catalog. Whether that is answered with an honest "the storefront is
    rate-limiting us" or with a retrieval cache is an open decision — see
    `docs/DECISIONS.md`, 30 Jul 2026.

### Reflex / frontend and serving

- Copy from DecaBot `AGENTS.md:218-252`, sections "Reflex & serving" and "Container
  deployment". The app architecture is identical: one `rx.State`, two ports in dev (frontend
  3000, backend 8000), one port in prod (both on 8000), compile into `.web/build/client`,
  domain-agnostic `api_url=http://localhost:8000`, skip-compile in prod, `granian` not
  `uvicorn`, session state to `./.states` on disk (DISK state manager, not NOOP).
- **Do NOT re-read those sections here. Link to DecaBot's AGENTS.md.** The entries below are
  ours, and correct or sharpen what that file says.
- **`rxconfig.py` is imported with `sys.path` reduced to its own directory.** `get_config()`
  clears the path down to the current directory, and retries with the ambient one only if that
  raises. Verified both halves 30 Jul 2026: an `import coros_core` there raises on a bare
  `reflex run` in `apps/brujula` and succeeds when the caller happens to export a `PYTHONPATH`
  carrying `packages/`. Keep those files to `reflex` and the stdlib. It is also why
  **`rx.App(theme=...)` stays on the app** although 0.9.7 deprecates it in favour of
  `rx.plugins.RadixThemesPlugin(theme=…)` in `rxconfig.py`: the theme reads
  `brujula/ui/theme.py`'s tokens and the config cannot. Revisit when the pin moves.
- **The module name `rxconfig` is global to the test suite.** Reflex fixes both the filename
  and the bare name it imports it as, and `pytest.ini` puts both app directories on `sys.path`
  — so `import rxconfig` resolves by path order and caches the winner in `sys.modules`, handing
  one app's config back for the other with no error anywhere. Tests load it by file path.
- **`reflex init` fires on the first `reflex run`/`export` in a fresh clone** and seeds the app
  directory with its own `.gitignore` and a `requirements.txt` holding nothing but the reflex
  pin. Both are gitignored, and `tests/test_layout.py` fails if either is committed: that
  requirements.txt shadows the root pins for anything installing from the app directory.
- **`reflex.lock/` tracks the component set, not the Reflex version.** Brújula's export drops
  the whole markdown chain — `react-markdown`, `react-syntax-highlighter`, the rehype/remark
  plugins — because nothing renders markdown; the presentation prompt asks for plain prose and
  the bubbles are `rx.text(white_space="pre-wrap")`. Re-export and re-commit the lockfile
  whenever an app's component set changes.
- **Reflex 0.9.7 DOES hold a dataclass state var** — verified 30 Jul 2026. It wraps one in a
  `MutableProxy`, tracks mutations through it and serialises it (sets and all) to the
  browser. So "Reflex state cannot hold a dataclass" is not why `ConversationSession` lives
  in `state._SESSIONS`: the reason is **cost**. As a state var, every mutation the agent loop
  makes to the transcript, the requirements and the advice is broadcast to the browser, and
  `StateManagerDisk` pickles the same bytes into `.states/`. Backend-only (`_`-prefixed) vars
  stay off the wire but are still pickled. Pinned by
  `tests/test_brujula_state.py::TestTheConversationDoesNotLiveInAStateVar`.
- **The `MutableProxy` / `json.dumps` trap is the COMPACT path only.** The C encoder does an
  exact type check, misses the proxy and falls through to `default=`, so a payload comes out
  as a Python repr inside a JSON string. Passing `indent=` selects the pure-Python encoder,
  which goes through `isinstance` and survives — so removing `state.plain()` breaks a compact
  dump and silently passes an indented one. `plain()` runs regardless.
- **`rx.match` renders its cases at construction time.** `Match.match_cases` holds rendered
  dicts, not components, so any walk over a component tree — a test, a lint, an audit — loses
  every branch of every match in it. Verified 30 Jul 2026. `tests/test_brujula_ui.py` therefore
  walks `Component.render()`, which is also what actually ships: `props` as strings, `children`
  / `true_value` / `false_value` / `match_cases` / `default` as the branches.
- **`rx.form` cannot take a Var `class_name`; `rx.box` can.** Verified 30 Jul 2026:
  `rx.form(…, class_name=rx.cond(...)).render()` raises `VarTypeError: Cannot convert Var …`,
  because the Radix primitive wants a real string. A conditional class goes on a wrapper box —
  `ui/gate.py` puts the entrance and the refusal shake there, and an AST check in
  `tests/test_brujula_ui.py` keeps them off the form.
- **An f-string over a Var is only safe in a child, never in a prop.** `f"1px solid {var}"`
  produces a `<reflex.Var>…</reflex.Var>` sentinel string, which Reflex re-parses when it is
  rendered as text and does not when it is a style value — so a conditional style prop has to
  be the whole declaration per branch: `rx.cond(err, f"1px solid {DANGER}", f"1px solid {EDGE}")`.
  Same for an `rx.match` resolver returning a `border` shorthand.
- **The rendered tree escapes non-ASCII and reaches row fields through optional chaining.**
  `aria_label="Contraseña"` arrives in the props as `Contrase\u00f1a`, and a `rx.foreach` row
  field as `item_rx_state_?.["title"]`. Both matter to anything that greps the rendered output:
  in a Spanish interface every second label carries an accent.
- **`hmac.compare_digest` raises `TypeError` on non-ASCII `str`.** Comparing the passwords
  themselves therefore turns an accented password — in a Spanish app, the likely kind — into a
  500 rather than a refusal. `state._digest()` is what gets compared: hex, ASCII, equal
  length, which is what a constant-time comparison wants anyway.

### The OAuth callback route (Huella only)

Measured end to end on **30 Jul 2026** by `scripts/spike_api_transformer.py` — a throwaway
Reflex app served the way the container serves it (`granian --factory`, `__REFLEX_SKIP_COMPILE`,
`__REFLEX_MOUNT_FRONTEND_COMPILED_APP=true`, a real `reflex export`), 14 probes, all passing.
`make spike-oauth` re-runs it; do that whenever the `reflex` pin moves, because every fact
below is a fact about `App.__call__` and nothing warns you when it changes.

- **A route on the `api_transformer` outranks the compiled frontend's catch-all.** In prod
  Reflex appends `get_frontend_mount()` — a `Mount` that serves `.web/build/client` and answers
  *anything* — and that is what would hand Strava a 404. It does not, because the mount goes on
  `asgi_app.routes`, Reflex's own router, and `App.__call__` then makes that whole router a
  single `Mount("")` **inside our Starlette**. Starlette matches in list order, so our routes are
  simply earlier. Probed: `/oauth/strava/callback?code=…` → our 200 with the query string intact,
  no `code` → our 400 not a static 404, `POST` → 405 (a swallowed path would 404),
  `/oauth/strava/nope` → 404 (the prefix is not a wildcard), and `/`, `/ping` and `/_event/`
  (101) all still served.
- **A route registered *after* `rx.App(...)` still wins.** The mount happens inside
  `App.__call__`, which granian invokes per worker boot — not at construction — so
  `api.routes.append(Route(...))` after the `rx.App(...)` line is reachable. Verified live.
  The inverse is the failure to fear: a route appended after a `Mount("")` on the *same* router
  is unreachable, and the symptom is a 404 on a route that plainly exists, with nothing in any
  log. Both halves pinned by `tests/test_contracts.py::TestAnOauthCallbackRouteOutranksTheFrontendMount`.
- **The `api_transformer`'s own `lifespan=` never runs.** `App.__call__` ends by building
  `Starlette(lifespan=self._run_lifespan_tasks)` and mounting everything else into *that*, and a
  mounted ASGI app is never sent a lifespan scope. A `lifespan=` on the transformer is therefore
  dead code that fails silently — the spike's marker file was never written. Huella's startup
  work goes through `app.register_lifespan_task`, which is the hook that does fire.
- **Reflex puts its own CORS policy on our route, and the default mirrors any origin.**
  `App._add_cors(api_transformer)` runs on the line above the mount, `cors_allowed_origins`
  defaults to `("*",)`, and `_add_cors` passes `allow_credentials=True` — so under the default
  config `Origin: https://evil.example` came back as `access-control-allow-origin`, on the
  callback, with credentials allowed. **`apps/huella/rxconfig.py` must pin
  `cors_allowed_origins`**; phase 2 of the spike proved the one-line narrowing drops the foreign
  mirror, keeps ours, and costs nothing — `/ping`, `/` and the websocket upgrade all still serve.
  Strava's own redirect is unaffected either way: a top-level navigation carries no `Origin`, so
  CORS never applies to the request that matters. Pinned by
  `tests/test_contracts.py::TestReflexPutsItsOwnCorsPolicyOnOurRoute`.
- **The callback still must not answer with a body worth stealing.** CORS is a browser policy,
  not a server one, and the narrowing above is defence in depth rather than the guarantee. The
  callback exchanges the code server-side and answers with a redirect and an empty body, so
  there is nothing for a mirrored origin to read even if the policy regresses.

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
  and auditable; a `product_type` field is never used for device identification, which an
  AST scan in `tests/test_devices.py` enforces. Two curated tables — `DEVICES` (14 rows)
  and `STRAPS` (26 rows) — each row carrying the sentence it was read from, and
  `audit(products)` re-derives the whole join against the live feed so the registry never
  self-certifies. Nothing infers a fit from a width, a spec from a handle, or a device
  from a title outside `resolve()`.
- **Nothing issues a model request except `gemini.py`.** `generate()` is the only call site,
  `gemini.client()` the only constructor, and `gemini.MODEL` the only spelling of the model
  name. Three AST scans in `tests/test_gemini.py` enforce all three — a docstring may name
  the model, code may not. A second call site would bring its own retry ladder and its own
  share of a quota three deployments already share.
- **`create_cart` and `create_checkout` are not exposed as model tools.** Human-in-the-loop
  is enforced by *absence* from the tool list, not by a prompt instruction. They are named
  in `capability.WITHHELD` as plain strings so the omission is auditable rather than a
  silence, and a test asserts no `ToolId` carries either value — an id that cannot be
  spelled cannot be offered. `ucp.call_ucp()` can still reach both from a click handler.
- **UCP is the cart surface, so no model-facing tool may reach it.** `brujula/agent/tools.py`
  never calls `call_ucp()` or `rpc()`: an AST scan in `tests/test_brujula_agent.py` rejects
  the attribute. This is why `search_products` is a literal match over the per-turn
  snapshot rather than a call to UCP's `search_catalog`, and it costs nothing — a UCP hit
  absent from the snapshot cannot pass `check_provenance(candidates, catalog)` anyway, so a
  semantic path could only ever re-rank products the turn already holds. See
  `docs/DECISIONS.md`, 30 Jul 2026.
- **Brújula's retrieval reads one snapshot; it does not navigate.** All four of its tools
  answer from the single `products.json` read the turn already paid for, so
  `list_collections`/`get_collection_products` cost no network and a zero-result
  `search_products` is **conclusive** — it read all 43. That is what
  `check_buy_nothing(retrieval_conclusive=True)` rests on, and it is the opposite of
  DecaBot, where an empty result is usually a partial look. The three groups (`relojes` 4,
  `correas` 26, `accesorios` 13) are a partition derived from `devices.DEVICES` and
  `devices.STRAPS` with the remainder as the third; `product_type` and `tags` are banned
  in `tools.py` by the same kind of AST scan that bans them in `devices.py`.
- **Every tool name is spelled once, in `capability.ToolId`, and the map is the only
  authority on which one can serve a request.** Both apps' schemas are written against
  those ids; `SURFACES` says which app exposes each. `capable_tools()` returning `()` is
  never a search result — `check_capability()` turns it into a typed dead end, and
  `CapabilityVerdict` refuses to hold an empty tool list with no reason attached.
- **Nothing retrieves from the catalog except `catalog.py`, and catalog.py never caches.**
  Live retrieval every turn; `fixtures/` is for offline development only and is never read
  by the running app.

## Guardrail principle

**A guardrail written in the prompt is a suggestion; a guardrail written in Python
is a guarantee.** Every check is deterministic code between the model and the world,
and every one emits a trace event at `level="guardrail"`.

`LocalAvailabilityVerdict.is_available = Literal[True]` means a region-blocked device
cannot be represented as a cleared one at all — the guardrail is enforced by the type
system, not by a branch. `BuyNothingVerdict` means "buy nothing" is a reachable typed
outcome, not prose that slipped through. `ProvenanceVerdict` proves a price matches the
live feed; an unbacked `"$X COP"` claim is rejected.

| check | trace event | what it guarantees |
|---|---|---|
| `check_provenance` | `guardrail.provenance` | every field of a rendered item is rebuilt from the feed. The model contributes a product id, a variant id and its reasoning; a title, URL or price it also sent is recorded as a `FieldMismatch` and then discarded |
| `check_stock` | `guardrail.stock` | availability is read out of the feed, never off the candidate. A model that labels a sold-out variant `available: true` changes nothing |
| `check_budget` | `guardrail.budget` | integer arithmetic in minor units, done in code. `nothing_fits` is a different answer from `over_by`, and an empty selection under a budget is the former |
| `check_local_availability` | `guardrail.local_availability` | an absent watch is named, never swapped. `UnavailableDevice` has no `alternative`/`instead`/`closest` field, and both its sentences are templates over one device's own name |
| `check_buy_nothing` | `guardrail.buy_nothing` | "buy nothing" is reachable, and `retrieval_conclusive=False` keeps a 429 from being reported as "nothing fits" |
| `find_unbacked_claims` / `scrub_prose` | `guardrail.prose` | a spec figure in prose has to appear in a retrieval-derived field. `AdviceItem.rationale` is not one of them |
| `catalog.strip_untrusted` | `guardrail.untrusted_text` | an injected segment was removed from vendor free text. Emitted only when something was removed, and it carries counts — never the matched text, which an evidence bundle would paste back into a model |
| `evidence.build` | `evidence.bundle` | the advice agrees with the verdicts, and every check a recommendation requires actually ran. Derived from the trace, so a stage cannot self-certify; a required check with no event means `accepted=False` |
| `capability.check_capability` | `guardrail.capability` | a request no tool can serve is a typed `NO_CAPABILITY`, never an empty search that reads as "COROS has nothing". `CapabilityVerdict` cannot be built with no tools and no reason, and `DeadEnd.outcome` is restricted to the escalating outcomes so "out of stock" cannot be dressed as "does not exist" |
| `tools.lookup_device_compat` | `guardrail.device_compat` | a strap fit is read out of `devices.py`, never derived from a width or a title. Records the slug, the case and the counts — the widths themselves are the registry's to state |
| `tools.lookup_device_compat` | `guardrail.case_unspecified` | an APEX 4 with no case size gets the **question** back as `NOT_ELIGIBLE`. An empty strap list would read as "COROS sells no APEX 4 straps", and picking a case is a guess |
| `tools.get_collection_products` | `guardrail.handle_rejected` | an unvalidated group handle is never looked up. The rejection is a tool *answer* carrying the three live names, so the model retries instead of the turn raising. Records the handle's length, never the handle |
| `tools.get_collection_products` | `guardrail.empty_collection` | a group that is live and carries nothing is `UNAVAILABLE` with a reason, not an empty `OK`. `Snapshot` refuses to be built `OK`-with-no-products at all, so a 429 cannot arrive here disguised as an empty catalogue |
| `loop._advise` | `guardrail.evidence_blocked` | a recommendation the bundle refused is never presented, and no stage is marked done — so the next turn resumes instead of the person getting an answer nothing verified |
| `loop._model` | `guardrail.model_budget` | the 26th model call of a conversation raises instead of running. The budget is per conversation, not per turn |
| `loop._retrieve` | `guardrail.tool_budget` | the 7th tool call of a turn is answered with `TIMEOUT` rather than dropped. A dropped call leaves a `function_call` unanswered, which 400s the next request |
| `loop._dispatch` | `guardrail.unknown_tool` | a tool name the model invented is a tool *answer*, not an exception, and it never counts as evidence about the catalogue. Records the name only when it is one of `capability.WITHHELD` — anything else is text the model made up |
| `loop._requirements` | `guardrail.requirement_rejected` | a requirement outside `RequirementKey`, or a derived one with no sample, is dropped rather than carried. Records the count and only the keys that are our own vocabulary |
| `loop._budget` | `guardrail.budget_unreadable` | a `budget_minor` that is not a whole number of centavos is dropped, never rounded. A budget read wrong by a factor of a hundred reports "nothing fits" about a catalogue full of things that do |
| `loop._injection` | `guardrail.injection_blocked` | the payload is redacted from `session.turns` as well as ignored — the transcript is fed to the next gate call, so leaving it there re-injects it one turn later. Records the length, never the text |

Two rules inside that table are easy to undo by accident. **Ambiguity is a question, never
a pick**: a product with two variants and none named is dropped, the same way
`devices.straps_for` raises rather than choosing. And **`kind="discount"` never consults
the backing text** — `compare_at_price` is deliberately unmapped, so no pre-discount number
can reach the model at all and even a true one has no source in this pipeline.

**Termination is governed by verification, never by the model deciding it is finished.**
A turn takes `trace.mark()` before it starts, calls `evidence.build(advice,
trace.since(mark))` before it renders, and presents nothing when `accepted` is False —
the blocking reasons are what the person is told instead. `bind_sink()` runs **before**
`asyncio.create_task()`: contextvars are copied at task-creation time, so the reverse
order routes the turn's verdicts somewhere the bundle cannot see them, and a bundle that
sees no events accepts nothing.

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
| `packages/coros_core/devices.py` | `tests/test_devices.py` — the offline half and the `live` audit together — plus the device and strap-width entries in this facts registry. `audit()` is what proves a row still matches the feed; run `make verify` before believing a curated change |
| `packages/coros_core/catalog.py` | `tests/test_catalog.py` — the offline half and the `live` probes together — plus the storefront section of this facts registry |
| `packages/coros_core/models.py` | `tests/test_models.py`, and `catalog.py`'s `map_product` if the change touches `CatalogProduct` or `CatalogVariant` |
| any tool schema | `packages/coros_core/capability.py` (`ToolId`, `SURFACES`, `MAP`), `brujula/agent/tools.py`, `brujula/agent/prompts.py`, `huella/agent/tools.py`, `huella/agent/prompts.py`, and the trace event names |
| `packages/coros_core/capability.py` (a need, a dead end, the tool registry) | `tests/test_capability.py` + the guardrail table + the cycling-range entries in this facts registry. A need moving between `MAP` and `DEAD_ENDS` is a claim about the live catalogue: `tests/test_contracts.py` and the live probe in `tests/test_catalog.py` pin it, and both move in the same commit |
| `packages/coros_core/ucp.py` (wire shape, error taxonomy, rate-limiting policy) | this facts registry + `tests/test_ucp.py` — the offline half and the `live` probes together |
| `packages/coros_core/gemini.py` (the model name, the retry ladder, the tool normaliser) | `tests/test_gemini.py` + the Gemini entries in this facts registry, and the SDK pins in `tests/test_contracts.py`. Adding a call site is a change to *this* file: the AST scans reject a second one |
| a guardrail | the guardrail table above + `tests/test_guardrails.py` + the trace event name. A check with no row in that table is a check nobody can review |
| `packages/coros_core/trace.py` (event shape or levels) | every `emit(...)` call site, `tests/test_trace.py`, and the guardrail table's trace-event column |
| `packages/coros_core/evidence.py` (a declared check, a required set, an assumption) | `tests/test_evidence.py` + the guardrail table. A check the bundle requires but nothing emits blocks every recommendation, so the two move together |
| `apps/brujula/brujula/agent/tools.py` (a tool, a group, `_slim`'s whitelist) | `tests/test_brujula_agent.py` + `brujula/agent/prompts.py`, whose stage prompts describe the tools by name + the guardrail table's four `tools.*` rows. A key added to `_slim` is a change to every prompt that renders one |
| `apps/brujula/brujula/agent/prompts.py` (a stage, a template) | `tests/test_brujula_agent.py` — one test asserts a canned template exists for every non-advice `Intent`, so a new intent without one is a model call spent letting the model improvise a refusal |
| `apps/brujula/brujula/state.py` (a var, a caption, the gate) | `tests/test_brujula_state.py`. A caption key must be an event something actually emits or it can never fire; a new `RequirementKey` needs a word in `_REQUIREMENT_ES` and a new declared check needs one in `_CHECK_ES`, or the rail shows an English enum to a Colombian reader; a new handler that spends a model call or reaches COROS needs the `GATE_ON and not self.unlocked` re-check, because conditional rendering is not a guard |
| `apps/brujula/brujula/ui/theme.py` (a colour, a ratio comment, a registry) | `tests/test_brujula_theme.py` + `docs/VISUAL-BRIEF-BRUJULA.md` + `assets/brujula.css`, which mirrors the tokens as custom properties. A new colour needs a line in `SURFACES`, `TYPE_ON`, `EDGE_ON` or `RULE_ONLY` and its measured ratio in a comment, or the coverage scan fails; a ratio is recomputed from the two colours it names, so editing a colour without editing its comment fails too |
| `apps/brujula/brujula/ui/brand.py` (a dot state, the dial, the wordmark) | `tests/test_brujula_brand.py` + the mark's entries in this facts registry. `HALO_CLASS` must be defined in `assets/brujula.css` with its `prefers-reduced-motion` fallback, or the thinking dot silently stops moving; a new dot state has to be tellable apart from the other two by hue or by contrast, and a ratio written in this file is recomputed from the two colours it names |
| `apps/brujula/brujula/ui/{chat,product,advice,trace_panel,gate}.py` | `tests/test_brujula_ui.py`, which walks the rendered tree of every entry point in that file's `ENTRIES` table — a new one belongs there or nothing checks it. A colour must be a token: a literal fails the scan, and a pair that is not in `theme.TYPE_ON` / `EDGE_ON` has to be added to `theme.py` **with its measured ratio** before it can be used. The rail may not import a light token and no light module may import a `RAIL_*` one. A new `AdviceKind` needs a row in `advice._KINDS`, a new `Confidence` a phrase in `advice._CONFIDENCE_ES`, a new `Outcome` a glyph in `advice._OUTCOME_ICON`. Every `bj-` class a component names must exist in `assets/brujula.css`, with its `prefers-reduced-motion` fallback if it animates. An icon-only control needs an `aria_label`; the bundle's English (`State.blocking`, `CheckRow.detail`) stays on the rail |
| `apps/brujula/brujula/agent/loop.py` (a stage, a budget, a wire model) | `tests/test_brujula_agent.py` + the guardrail table's `loop.*` rows. A new stage needs a name in `REOPENED` or it re-runs on every resume; a new `response_schema` needs a place in `loop.SCHEMAS` or nothing checks it for the `additionalProperties` 400; a check the bundle can block on needs a Spanish name in `loop._CHECKS_ES` or the person is told a check failed without being told which |
| anything Strava-scoped | `tests/test_strava.py` + `tests/test_privacy_boundary.py` — token atomicity and state isolation are release blockers |
| `apps/huella/huella/app.py`'s `api_transformer`, its route list, or the `reflex` pin | `tests/test_contracts.py` (the two OAuth classes) **and** a re-run of `make spike-oauth`. The unit tests pin the mechanism against Reflex's source; only the spike proves the whole shape under granian with a real export, and it is the only thing that would catch the frontend catch-all winning. A new route goes on the transformer *before* nothing in particular — order against Reflex's mount is automatic — but a route added to `asgi_app` instead is unreachable |
| `rxconfig.py` | `tests/test_brujula_app.py`; recompile the frontend; note the new URL in `docs/DEPLOY.md` and `docs/RUNBOOK.md`. Every value in that file is load-bearing for a running instance, not a preference |
| `apps/brujula/brujula/app.py` (a route, the gate branch, the component set) | `tests/test_brujula_app.py`, and `reflex export --frontend-only` in `apps/brujula` followed by re-committing `reflex.lock/` — the lockfile is derived from which components the tree actually uses |
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
