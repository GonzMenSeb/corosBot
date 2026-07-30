# Visual direction brief — Brújula

**For the agent picking this up: this file is your complete instruction set. Read it
in full before touching anything.** Unlike DecaBot's `../Los-Prompteros-Project/docs/VISUAL-BRIEF.md`
(a forward-looking work order), this one is written **after** the identity and every
UI module shipped — it is the canonical record of what was decided and why, and the
reference PR 11's Playwright QA pass measures against.

Read [`AGENTS.md`](../AGENTS.md) first — it is canonical for everything about this
repo and outranks this file wherever they touch the same subject. This brief only
governs **visual direction** for Brújula (`apps/brujula/`).

---

## 0. What Brújula is, in five lines

A conversational agent that turns a described sporting need into an honest, in-stock
recommendation from **COROS Colombia's real catalogue** — a watch, a strap, a sensor,
or the truthful answer that none of them fit. It interviews, derives requirements,
retrieves live products, and presents at most a handful of matches with a stated
reason. There is no cart: `create_cart`/`create_checkout` are absent from the tool
list by construction, not by instruction.

It shares a VPS and a Gemini key with DecaBot but is not a re-skin of it: same
scaffolding (Reflex 0.9.7, staged agent loop, audit rail, load-bearing-facts
discipline), a different vendor, a different palette, a different personality.

---

## 1. The decision that was made

DecaBot's brief records a chosen-vs-rejected direction for a hackathon deliverable.
Brújula's equivalent decision was made once, in the session that wrote `theme.py`,
and is recorded here rather than re-litigated:

**Brújula is COROS's monochrome storefront read as warm paper, not COROS's site with
a different signature.** Six of COROS's seven published CSS custom properties (`INK`,
`GRAPHITE`, `GRAPHITE_DEEP`, `SUB`, `RULE`, `SUCCESS`) and their own CTA red
(`DANGER`) are load-bearing anchors, verified live against `coros.com.co`'s inline
theme stylesheet — see `tests/test_brujula_theme.py::TestTheAnchorsAreTheOnesCorosPublishes`,
which fails the build the day any of the seven drift. On top of those anchors sits a
register COROS does not have: an ivory page (`PAPER #FAF8F1`), an editorial display
face (Fraunces) for the wordmark and headings, and a brass accent (`BRASS #8A5A1E`) —
a compass needle's metal — standing in for a brand hue neither COROS nor DecaBot own.

**Red is inverted from COROS's own usage.** Their storefront spends `#ea2e41` on buy
buttons. Here it is reserved for the refusal: "COROS Colombia no vende eso" and "no
compres nada" are the two hardest sentences Brújula says, and they may never arrive in
the same colour as a purchase. `theme.REFUSAL` is the enforced allowlist —
`tests/test_brujula_theme.py::TestRedIsReservedForTheRefusalMoment` scans every colour
in the file by hue/saturation/lightness and fails on any saturated red outside it.

**DecaBot's indigo `#3643BA` appears nowhere.** Two demos on one VPS that share a
palette read as one demo with two front doors —
`TestTheAnchorsAreTheOnesCorosPublishes::test_none_of_decabots_indigo_survives_here`
scans the module source for DecaBot's seven hex values and fails the build if any
land here, including in a mixed surface.

**Do not revisit any of the above without re-verifying the live COROS stylesheet
first** and writing the correction into `AGENTS.md`'s facts registry in the same
commit — the standing convention for this whole repo.

---

## 2. Non-negotiables

Breaking any of these breaks a guarantee the project makes, not a style opinion.

### 2.1 Architecture — from `AGENTS.md`, do not "fix" these

| Fact | Why |
|---|---|
| `Url = Annotated[str, AfterValidator(...)]` in `packages/coros_core/models.py` | A pydantic `HttpUrl` silently serializes to `null` over Reflex's wire encoder. Never put a raw `HttpUrl` in anything that reaches the UI. |
| `price_display` arrives pre-formatted from `money.minor_to_display` | `money.py` is a Python function and cannot be called on a Var inside a component — a price that has not been through it cannot reach `product.py` at all. This is also what keeps the storefront's major units and UCP's minor units from ever meeting on screen. |
| `image_url` is `""`, never `None`, when COROS ships no photo | `rx.cond` needs a value to test, and an in-stock product without a photo is still buyable. |
| `theme.py` imports nothing and defines nothing | `rxconfig.py` imports it with `sys.path` cut to its own directory — a token file with an import or a function is one the config can never read. Enforced by `TestTheTokenFileStaysReadableFromAnywhere`. |
| `vite_allowed_hosts=True`, `app_module_import`, pinned ports in `rxconfig.py` | Demo-killers, same as DecaBot. |

### 2.2 Guarantees the UI must keep expressing

- **`create_cart`/`create_checkout` are not model tools.** Human-in-the-loop is
  enforced by their *absence* from `agent/tools.py`'s tool list, never by a prompt
  instruction. Do not add a route to either from the UI.
- **Every factual attribute on a card comes from a `ProductCard` field, never from
  model prose.** `rationale` is the one exception, and it carries a visible quote rule
  so it reads as prose against a card that otherwise reads as the catalogue.
- **The four non-recommendation `AdviceKind`s each get their own panel, not a shared
  template with a colour swapped in.** `advice.py`'s `_panel()` takes a verdict's ink
  and surface together as one pair, because a colour resolved independently is a pair
  the theme never measured. The four: `buy_nothing`, `not_sold_locally`,
  `insufficient_evidence`, `needs_human`. None may look like a lesser version of
  `recommend` — each is a different sentence and must read as one at a glance.
- **`State.blocking` (the evidence bundle's own English reasons, e.g. "stock did not
  run") renders only on the audit rail**, never in `advice.py`. `loop.py` already
  says the same thing to the person in Spanish in the transcript; duplicating it in
  the panel would be the same fact in two registers.
- **The presence dot is the only ambient animation and the only "is this trustworthy
  right now" signal.** Idle is green only when a Gemini key is actually present
  (`brand.IDLE_DOT`); amber is the live storefront throttle, never a fixture replay —
  nothing in Brújula reads `fixtures/` at runtime, so a fixture-amber dot would be a
  lie the code cannot even produce by accident, and it must stay that way.

### 2.3 Accessibility — measured, not eyeballed, must not regress

Every ratio below is recomputed by `tests/test_brujula_theme.py` from the two hex
values it names, every run. A ratio written in prose without both colour names is
rejected by `TestEveryRatioWrittenDownIsTheMeasuredOne::test_no_ratio_is_written_without_naming_the_two_colours` —
an unverifiable figure is indistinguishable from an invented one.

- **`SUB` (`#9A9A9A`, COROS's own secondary grey) is never type.** `SUB on PAPER`
  measures 2.65:1 — under AA, and a demo projector is worse than any monitor. `QUIET
  #6B6659` carries muted copy instead. `SUB` is icon-and-rule-only, classified in
  `theme.RULE_ONLY` with the reason inline.
- **`RULE` (`#EEEEE0`, COROS's own border colour) is decorative only** — 1.10:1 on
  `PAPER`. A border somebody has to see (a focused field, a card edge, the mark's
  bezel) is `EDGE #89857A`, which clears the 3:1 non-text floor on every surface it
  sits on (tightest: `EDGE on PAPER_DEEP` 3.15:1).
- **`SUCCESS` (`#3a8735`, COROS's own) cannot carry words** — `SUCCESS on CARD`
  measures 4.47:1, three hundredths under AA. `SUCCESS_INK #2E6C2A` (6.37:1 on CARD)
  says "en stock" in words; `SUCCESS` stays the tick and the fill.
- **`DANGER` (`#ea2e41`) cannot carry words either** — 4.23:1 on CARD. `DANGER_INK
  #C2172E` (6.08:1 on CARD) carries the refusal sentences; `DANGER` is the icon and
  the rule.
- **The audit rail is a second register — none of the light-surface tokens
  transfer.** `BRASS on RAIL_BG` is 3.10:1, `DANGER_INK on RAIL_BG` is 3.00:1: both
  fail AA there, which is the whole reason `RAIL_INK`/`RAIL_MUTED`/`RAIL_BRASS`/
  `RAIL_DANGER`/`RAIL_SUCCESS` exist as a separate set, tinted from the slab's own
  warm-black hue rather than neutral grey. `trace_panel.py` imports only `RAIL_*`
  colours; a light-surface colour appearing there is a review-visible mistake, not
  just a contrast failure.
- Keep: `role="banner"`/`role="main"`/`role="complementary"`, `aria-live` on the
  transcript, accessible names on icon-only buttons (the reveal-password toggle, the
  rail's collapse control), 44px minimum touch targets, and a real
  `prefers-reduced-motion` path for the halo class (`assets/brujula.css` swaps it for
  a static ring, never a blanket motion kill).

### 2.4 Tests and process

- `make check` must stay green.
- Every stated ratio in `theme.py` is re-derived by `tests/test_brujula_theme.py`,
  not asserted by hand. Changing a colour means updating the comment **and** this
  file's table (§4) in the same commit — the test enforces the comment; nothing
  enforces the table but discipline.
- Append to `docs/DECISIONS.md`; it is append-only.
- Comments stay minimal — a counterintuitive fact, never a restatement of the code.

---

## 3. The contrast helper

Every ratio anywhere in this repo — in `theme.py`'s comments, in `tests/test_brujula_theme.py`,
and in the table below — is the output of these two functions, copied verbatim from
`tests/test_brujula_theme.py::lum`/`ratio` (themselves ported from DecaBot's brief).
There is exactly one implementation; if you need to check a colour by hand, paste
this rather than eyeballing a swatch or trusting a browser extension's rounding.

```python
def lum(value):
    h = value.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(a, b):
    la, lb = lum(a), lum(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 2)
```

`ratio("#161d25", "#FAF8F1")` → `15.98` — `INK` on `PAPER`, the transcript's own
answer-bubble pairing.

AA floor for anything that reads as words: **4.5:1**. Non-text floor for an edge or
an icon that has to be seen (focus ring, card border, status glyph): **3:1**. A
colour that cannot clear either floor for a given surface goes in `theme.RULE_ONLY`
with the reason, or is restricted to `theme.EDGE_ON`/`SURFACES` — never quietly used
as type anyway.

---

## 4. Contrast table

Generated from `theme.TYPE_ON` and `theme.EDGE_ON` — the file's own claim about which
colour sits on which surface — with the ratio each pair actually measures today.
Anything here drifting from `theme.py`'s comments is a test failure, not a doc-only
problem: `tests/test_brujula_theme.py::TestEveryRatioWrittenDownIsTheMeasuredOne`
recomputes every one of these on every run.

### Type (AA floor 4.5:1)

| Colour | On | Ratio |
|---|---|---|
| INK | PAPER | 15.98:1 |
| INK | PAPER_DEEP | 14.50:1 |
| INK | CARD | 16.98:1 |
| GRAPHITE | PAPER | 9.76:1 |
| GRAPHITE | PAPER_DEEP | 8.85:1 |
| GRAPHITE | CARD | 10.37:1 |
| GRAPHITE_DEEP | PAPER | 15.15:1 |
| GRAPHITE_DEEP | CARD | 16.10:1 |
| QUIET | PAPER | 5.38:1 |
| QUIET | PAPER_DEEP | 4.89:1 |
| QUIET | CARD | 5.72:1 |
| BRASS | PAPER | 5.55:1 |
| BRASS | CARD | 5.90:1 |
| BRASS | BRASS_SOFT | 5.07:1 |
| BRASS_DEEP | PAPER | 9.82:1 |
| BRASS_DEEP | CARD | 10.44:1 |
| BRASS_DEEP | BRASS_SOFT | 8.98:1 |
| ON_DARK | GRAPHITE | 10.37:1 |
| ON_DARK | GRAPHITE_DEEP | 16.10:1 |
| ON_DARK | INK | 16.98:1 |
| ON_DARK | BRASS | 5.90:1 |
| ON_DARK | BRASS_DEEP | 10.44:1 |
| DANGER_INK | PAPER | 5.72:1 |
| DANGER_INK | CARD | 6.08:1 |
| DANGER_INK | DANGER_SOFT | 5.24:1 |
| SUCCESS_INK | PAPER | 6.00:1 |
| SUCCESS_INK | CARD | 6.37:1 |
| SUCCESS_INK | SUCCESS_SOFT | 5.50:1 |
| WARN_INK | PAPER | 6.51:1 |
| WARN_INK | CARD | 6.92:1 |
| WARN_INK | WARN_SOFT | 6.03:1 |
| WARN_INK | BRASS_SOFT | 5.95:1 |
| RAIL_INK | RAIL_BG | 15.73:1 |
| RAIL_INK | RAIL_BG_2 | 14.35:1 |
| RAIL_MUTED | RAIL_BG | 8.57:1 |
| RAIL_MUTED | RAIL_BG_2 | 7.82:1 |
| RAIL_BRASS | RAIL_BG | 9.54:1 |
| RAIL_BRASS | RAIL_BG_2 | 8.70:1 |
| RAIL_DANGER | RAIL_BG | 9.09:1 |
| RAIL_DANGER | RAIL_BG_2 | 8.30:1 |
| RAIL_SUCCESS | RAIL_BG | 10.02:1 |
| RAIL_SUCCESS | RAIL_BG_2 | 9.15:1 |

### Edges (non-text floor 3:1)

| Colour | On | Ratio |
|---|---|---|
| EDGE | PAPER | 3.47:1 |
| EDGE | PAPER_DEEP | 3.15:1 |
| EDGE | CARD | 3.68:1 |
| EDGE | BRASS_SOFT | 3.17:1 |
| BRASS | PAPER | 5.55:1 |
| BRASS | PAPER_DEEP | 5.03:1 |
| BRASS | CARD | 5.90:1 |
| DANGER | PAPER | 3.98:1 |
| DANGER | CARD | 4.23:1 |
| SUCCESS | PAPER | 4.21:1 |
| SUCCESS | CARD | 4.47:1 |

### Declared but never type (`theme.RULE_ONLY`)

| Colour | Measures | Reason |
|---|---|---|
| SUB | 2.65:1 on PAPER | COROS's own secondary grey; under AA. Icon and rule glyphs only. |
| RULE | 1.10:1 on PAPER | COROS's own border colour; decorative hairline only. A border somebody has to see is `EDGE`. |

---

## 5. Type, geometry, motion — as shipped

- **Display face**: Fraunces (variable, opsz 9–144), for the wordmark and headings
  only — it is Brújula's own editorial voice and not COROS's.
- **Interface face**: Barlow, standing in for COROS's licensed PF Din Text Pro (same
  DIN-derived skeleton, available on Google Fonts).
- **Mono**: JetBrains Mono, reserved for the audit rail — earned there because it is
  real log data, not a costume elsewhere.
- One `<link>` for all three (`theme.FONT_HREF`), verified to serve `latin-ext` — the
  whole interface is in Spanish and a family without the accents renders "Brujula".
- Tracking floors: `TRACK_DISPLAY -0.025em`, `TRACK_TIGHT -0.015em`,
  `TRACK_EYEBROW 0.14em` (the tagline's uppercase run only — Brújula otherwise avoids
  the eyebrow-above-a-heading pattern DecaBot's brief banned).
- Radii: `RADIUS 10px` general, `RADIUS_SM 5px`, `RADIUS_LG 16px` (the gate card),
  `RADIUS_PILL` for the mark and buttons.
- Shadows are cast in the paper's own warmth (`rgba(58,44,20,…)`), never neutral grey
  — a grey shadow on `#FAF8F1` reads as a smudge on the page, not depth.
- **One authored moment**: the presence-dot halo (`assets/brujula.css`'s `bj-halo`
  keyframe), the app's only ambient animation. The gate's entrance (`bj-rise`) and
  its refusal shake (`bj-shake`) are the same class slot on the same wrapper, chosen
  by whether there is an error — not two competing motions.

---

## 6. What you must NOT do

- Do not introduce a second brand hue beyond COROS's seven anchors plus brass.
- Do not let `DANGER`/`DANGER_INK`/`DANGER_SOFT` (or any saturated red) appear
  outside `theme.REFUSAL` — that is what keeps the refusal from reading as a call to
  action. The theme test enforces this by hue scan; do not weaken the test to ship a
  red button.
- Do not use `SUB` or `RULE` as type or as a visible edge — they are COROS's own
  values and neither clears its floor here.
- Do not add a colour to `theme.py` without classifying it in `SURFACES`, `TYPE_ON`,
  `EDGE_ON` or `RULE_ONLY` — an unclassified colour is a colour nobody measured, and
  `TestEveryColourTokenSaysWhatItIsFor` fails the build on one.
- Do not state a ratio in a comment or in this file without naming both colours —
  see §3's floor rule. A bare figure is unverifiable and the theme test rejects it.
- Do not render a `not_sold_locally`/`buy_nothing`/`insufficient_evidence`/
  `needs_human` verdict as a lesser `recommend` card. Each is its own panel.
- Do not add a spinner beside the presence dot, or a second element answering "is
  this still running" — the dot already answers it, with the throttle state folded
  in.
- Do not cache the catalogue to disk for the UI to read — a local copy reads as
  mocked, the same DecaBot rule.

---

## 7. How to run and verify

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest -m "not live" -q tests/test_brujula_theme.py
```

Local dev (needs `.env` with `GEMINI_API_KEY`, `BRUJULA_PASSWORD` — see
`.env.example`):

```bash
cd apps/brujula && ../../.venv/bin/reflex run
```

Open `http://localhost:3000`, unlock with `BRUJULA_PASSWORD`.

**Playwright MCP protocol (PR 11)**: navigate the running app at 1440px and 414px,
snapshot, screenshot, read the console. Check: type hierarchy carries structure not
just hue, no token used as type falls under the ratios in §4, focus rings visible on
the gate's field and the composer, no layout break at 414px, and each of the four
non-recommendation panels reads as visually distinct from `recommend` at a glance —
not as a paragraph with a coloured left edge. Save screenshot pairs to
`docs/screenshots/brujula-{1440,414}.png`; log console errors to
`docs/QA-BRUJULA.md`.

---

## 8. Acceptance

- [ ] `make check` green
- [ ] Every §2 non-negotiable intact
- [ ] Every ratio in §4 matches what `tests/test_brujula_theme.py` measures today
- [ ] No saturated red outside `theme.REFUSAL`
- [ ] No DecaBot indigo anywhere in `theme.py`
- [ ] The four non-recommendation `AdviceKind` panels are visually distinct from
      `recommend` and from each other
- [ ] Runs clean at 1440px and 414px (confirmed in PR 11)
