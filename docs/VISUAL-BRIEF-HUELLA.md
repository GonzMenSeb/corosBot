# Visual direction brief — Huella

**For the agent picking this up: this file is your complete instruction set. Read it in
full before touching anything.** Like [`VISUAL-BRIEF-BRUJULA.md`](VISUAL-BRIEF-BRUJULA.md)
and unlike DecaBot's forward-looking work order, this one is written **after**
`huella/ui/theme.py`, the five UI modules and `assets/huella.css` shipped: it is the
canonical record of what was decided and why, and the reference the Playwright QA pass
measures against.

Read [`AGENTS.md`](../AGENTS.md) first — it is canonical for everything about this repo and
outranks this file wherever they touch the same subject, and its **Strava section is not
negotiable by anything written here**. This brief only governs **visual direction** for
Huella (`apps/huella/`).

`apps/huella/huella/ui/theme.py`'s docstring is the argument this file is the instruction
set for. Where the two touch, the docstring wins; this file is what you read to *rebuild*
the register, not a second copy of the reasoning.

---

## 0. What Huella is, in five lines

A conversational agent that reads an athlete's **real Strava training history**, derives a
requirement from demonstrated performance and consistency, runs it through the same
COROS Colombia retrieval pipeline Brújula uses, and answers with equipment — or with one of
four refusals. Its one addition over Brújula is a layer that says out loud **how much the
advice leans on the data**: how thick the window was, how stale, how much was dropped.

It shares a VPS, a Gemini key, a price boundary, a device registry and seven brand anchors
with Brújula. It shares no surface, no register and no second-tier token with it.

---

## 1. The decision that was made

**Huella is the same COROS values as Brújula, read as an instrument instead of as paper.**
Reading a training history is reading instrumentation: dense rows, figures that align down
a column, a surface that does not compete with a chart drawn on it. So Huella is
dark-primary, and the dark is COROS's own `--color-body-text` used as a *surface* — `DASH`
is one step below it, `INK` is it, `INK_2` is one step above. Inverting Brújula's paper
palette was the other option and it produces a muddy grey, not an instrument.

**There are two registers and they do not share tokens.** The instrument is the app; the
sheets are a full second palette for a card, a modal and the Strava attribution block. This
is not a preference. `READOUT on SHEET` is **1.12:1** and `TRACE on SHEET` is **1.82:1** —
the two instrument tokens that come *closest* to surviving on white. §4.3 is the whole
table, and every row of it is a sentence nobody could read. So the light surfaces carry
their own muted ink, their own edge, their own accent and their own three semantic inks,
and **the instrument's tokens stop at a sheet's edge.**

**Red says "do not lean on what is on screen".** `#ea2e41` is COROS's call to action;
Brújula spends it on the honest refusal. Huella spends it on three states that are one
state to whoever is reading — a window too thin or too stale to reason from
(`CONFIDENCE_COLOR["none"]`), a check that ran and failed (`OUTCOME_COLOR["fail"]`), and a
turn that broke (`LEVEL_COLOR["error"]`). Each is the app saying the figures beside it will
not hold weight, and one sentence gets one colour. **Amber is "usable with reservations"** —
a window thinner than you would like, a bundle that could not be confirmed — and **a
refusal arrived at correctly is not coloured at all**: "no compres nada" and "eso aquí no
está" are right answers, not degraded ones. See §2.2 for the boundary that actually needs
policing, and `docs/DECISIONS.md`, 30 Jul, *"Red means 'do not lean on this', not 'the
window is thin'"* for why widening the rule was the honest move rather than pushing the
errors down to amber.

**DecaBot's indigo appears nowhere.** Two demos on one VPS that share a palette read as one
demo with two front doors —
`TestTheAnchorsAreTheOnesCorosPublishes::test_none_of_decabots_indigo_survives_here` scans
the module source for DecaBot's seven hex values and fails the build if any land here.

**Do not revisit any of the above without re-verifying the live COROS stylesheet first**,
and write the correction into `AGENTS.md`'s facts registry in the same commit — the standing
convention for this whole repo.

---

## 2. Non-negotiables

Breaking any of these breaks a guarantee the project makes, not a style opinion.

### 2.1 The seven anchors, and the two that are light-register-only

Read off `coros.com.co`'s inline theme stylesheet — six CSS custom properties and one
literal — and shared byte-for-byte with `brujula/ui/theme.py`.
`TestTheAnchorsAreTheOnesCorosPublishes` fails the build the day any of the seven drift.

| Token | Value | Their name | Its job here |
|---|---|---|---|
| `INK` | `#161d25` | `--color-body-text` | a **surface**: the panel every row is drawn on |
| `GRAPHITE` | `#404040` | `--color-primary` | a button **inside a sheet**, and nowhere else |
| `GRAPHITE_DEEP` | `#212121` | `--color-primary-darker` | that button, pressed |
| `SUB` | `#9A9A9A` | `--color-sub-text` | working secondary type — **on the instrument only** |
| `RULE` | `#EEEEE0` | `--color-border` | **declined outright** |
| `SUCCESS` | `#3a8735` | `--color-success` | the tick and the fill, never words |
| `FLAG` | `#ea2e41` | their CTA literal | the glyph and the rule of "do not lean on this" |

- **`GRAPHITE` and `GRAPHITE_DEEP` are light-register-only, and that is measured.**
  `GRAPHITE on SHEET` is **10.37:1**; `GRAPHITE on DASH` is **1.83:1** — invisible on the
  instrument — and it is not rescuable with a border, because `EDGE on GRAPHITE` is
  **2.46:1** too. The instrument's own control fill is `TRACE`.
  `test_coros_own_button_grey_cannot_be_a_control_on_the_instrument` asserts both figures
  are under the non-text floor *and* that nothing but `ON_FILL` is ever set on either.
- **`SUB` is the one anchor that inverts between the two apps.** Brújula keeps it out of
  type because `SUB on PAPER` fails; here `SUB on INK` is **6.04:1** and it carries column
  labels, placeholders and the tagline. It still cannot carry words on a sheet — `SUB on
  SHEET` **2.81:1** — which is exactly why the light half needs its own `SHEET_QUIET`. Both
  halves of that claim are separate tests in `TestTheSubGreyInvertsBetweenTheTwoApps`.
- **`RULE` is declined, not merely unused.** `RULE on DASH` is **16.20:1** and `RULE on INK`
  is **14.51:1** — a "hairline" in the storefront's own border ivory would be the brightest
  thing on the screen. Huella's hairlines are `GRID`; a boundary somebody has to see is
  `EDGE`.
- **`--color-warning: #ff706b` is deliberately unspent.** A second red makes a window that
  will not hold weight and a refused request the same answer. `AMBER_INK` is the middle
  answer. `test_the_second_red_coros_publishes_is_still_unspent` fails on a token holding
  that value; the docstring is free to name it and say why.
- **`SUCCESS` cannot carry words on white.** `SUCCESS on SHEET` is **4.47:1** — three
  hundredths under AA, their figure and not a rounding of ours — so it stays the tick and
  the fill, and `SUCCESS_INK` / `SHEET_SUCCESS_INK` say it in words.

### 2.2 Guarantees the UI must keep expressing

- **Red covers three states and amber covers one, and the line between them is the one
  worth policing.** A check that *ran and failed* is red. A check **nobody could run** —
  "no pude confirmarlo" — is amber, because it is an honest answer about a missing
  measurement rather than a measurement that came back wrong. `advice.py` already draws
  that line; `evidence()`'s blocked well is `AMBER_WELL` and the failed-check glyph is
  `OUTCOME_COLOR["fail"]`. Pushing the failures down to amber would put a hard failure and
  a merely thin window in the same colour, which is the exact collapse §2.1 refuses when it
  declines COROS's second red.
- **No refusal panel spends the flag colour.** `advice.py` imports no `FLAG*` token at all.
  `buy_nothing` and `not_sold_locally` are the neutral register (`READOUT` on `INK_2`, glyph
  `TRACE`); `insufficient_evidence` and `needs_human` are amber (`AMBER_INK` on
  `AMBER_WELL`). The only red that reaches that module arrives through
  `theme.OUTCOME_COLOR`.
- **A colour a panel uses lives in that panel's own row.** `advice._Refusal` carries
  `ink`/`glyph`/`well`/`seal` as one tuple and `training._badge()` indexes
  `CONFIDENCE_COLOR` and `CONFIDENCE_BG` inside the same branch. Two tables resolved
  independently can drift onto a pair the theme never measured; keyed once per branch they
  are paired structurally. Do not "simplify" either into a pair of matched lookups.
- **The four non-recommendation `AdviceKind`s each get their own panel**, built from
  `get_args(AdviceKind)` rather than from a list typed in the file, so a kind added to the
  type gets a case whether or not anybody wrote it a row and falls through to `_UNNAMED`
  when they did not. None may read as a lesser `recommend`.
- **`State.blocking` and `CheckRow.detail` render on the audit rail and nowhere else.**
  They are the evidence bundle's own English — an engineering artifact — and `loop.py`
  already says the same thing to the athlete in Spanish in the transcript.
- **The presence dot is the only ambient animation, and the throttle outranks the turn.**
  Idle is green only when `gemini.api_key()` returns something, read once at import; working
  is `TRACE` pulsing; `State.throttled` while `State.is_thinking` shows the amber pip
  instead, and that flag covers Strava's quarter-hour window as well as COROS's latch.
  `brand.py` imports no `FLAG*` token: a red pip would say the *advice* is thin when what it
  meant was that a key is missing.
- **Every level in the rail gets a glyph, not only a hue.** `LEVEL_COLOR` is one signal; the
  icon is the second and the left rule is the third. A reader who cannot separate cyan from
  grey, and anyone reading a monochrome screenshot in an issue, gets the same three answers.
- **The mark is dark-register only.** `TRACE` and `AMBER_INK` are declared on `DASH`, `INK`
  and `INK_2` and on neither sheet, so a mark handed `surface=SHEET` would paint pips
  nothing measured. The attribution block is a sheet; the lockup does not go on it.
- **The series in the mark falls before it rises.** An ascending zigzag is Strava's own
  silhouette. Tidying that path into a clean rise is how this regresses.

### 2.3 Strava — mandatory, unmodifiable, and the one colour we do not own

Every line here is `AGENTS.md`'s, restated because it constrains the visual design directly.
**Nothing in this section is a preference and none of it may be relaxed to make a layout
work.**

- **`STRAVA = "#FC5200"`, not `#FC4C02`.** The older value is what most of the web repeats;
  it is not what Strava ships. Measured 30 Jul 2026 at three boundaries that agree: all six
  orange SVGs in `1.1-Connect-with-Strava-Buttons.zip` and `1.2-Strava-API-Logos.zip`
  contain exactly one colour, `#FC5200`; the horizontal "Powered by Strava" PNG decodes to
  `#FC5200` on every opaque non-black pixel; and `developers.strava.com/guidelines` §3 names
  `#FC5200`. `tests/test_huella_theme.py::TestTheVendorsOrangeIsNotOurs` pins it.
- **`SHEET` is spelled alone in `EDGE_ON["STRAVA"]`, and the missing surface is the cost of
  the correction.** `STRAVA on SHEET` is **3.31:1** and clears the 3:1 graphic floor.
  `STRAVA on SHEET_2` is **2.9977:1** and does not — two thousandths under, where `#FC4C02`
  cleared at 3.08:1. The one lever that would rescue it is darkening the asset, and that is
  the one fix the brand terms forbid, so the attribution block sits on pure white instead.
  **This is why the suite's `ratio()` rounds to four places** (§3): at two, 2.9977 becomes
  exactly 3.0 and a `>= 3.0` assertion passes on a failure.
- **The marks are the vendored files, rendered as images.** `assets/strava/` holds
  `btn_strava_connect_with_orange.svg` (237×48) and
  `api_logo_pwrdBy_strava_horiz_orange.svg` (365×37) byte-identical, and Reflex serves that
  directory at the web root as `/strava/…`. Never retyped as inline SVG, never recoloured,
  never filtered, never animated, never inside anything that animates, and never the app's
  own icon.
- **The official marks carry BLACK text, which is what makes the block a light surface.**
  The powered-by asset is two paths — `fill="black"` for the words and `fill="#FC5200"` for
  the wordmark. On the instrument the black half is invisible. So `connect.panel()` is
  `SHEET` end to end and every token inside it is a `SHEET_*` one.
- **The orange rule is drawn INSIDE the sheet.** A border is measured against what is behind
  it, so a `border_left` on that panel would put `DASH` on the bar's outer side — a pair the
  theme never measured. `connect._rule()` is a 1.6rem × 3px box with `SHEET` on every side
  of it, which is the pair `EDGE_ON` actually declares.
- **The connect control is a Radix button stripped of everything Radix would draw.** No
  surface, no radius over the asset, no hover and no active fill — behind a mark that may not
  be recoloured, Radix's own variant fill is the vendor's mark on a surface nothing measured.
  The ring was stripped with the rest, so `FOCUS_RING_SHEET` is put back explicitly.
- **Attribution is placed once.** `connect.panel()` already carries the powered-by mark, so
  `app.py` never places `connect.attribution()` a second time: two of the vendor's marks on
  one screen is the prominence their terms are about. The mark stays subordinate to Huella's
  own `h1`, and `_NOT_SPONSORED` says out loud what guideline one asks.
- **The orange never carries words.** A "View on Strava" link is set in `SHEET_TRACE`; the
  mark beside it is the unmodified asset. `STRAVA` is in `RULE_ONLY` with that reason inline.
- **`STRAVA` and `FLAG` may never share a surface.** They are **25.6°** of hue apart and
  `STRAVA on FLAG` is **1.28:1** — and it is the *contrast* that makes them one glyph, not
  the hue. Brújula's mark takes 25° of hue **or** 1.8:1 of contrast as proof two pips differ;
  these two clear the angle by half a degree and fail the contrast outright, so the angle was
  never what was holding. The guarantee is the surfaces: the vendor's mark on the sheets, the
  flag on the instrument, and the flag never a bare dot — it is `FLAG_INK` words on
  `FLAG_WELL`.
- **No second orange may exist.** `test_no_second_orange_exists_to_be_mistaken_for_a_recoloured_one`
  fails on any token in the 14°–30° hue band other than `STRAVA`, because a second orange is
  either a copy of theirs that will drift or a darkened version that violates the terms.

### 2.4 Accessibility — measured, not eyeballed, must not regress

Every ratio in §4 is recomputed by `tests/test_huella_theme.py` from the two hex values it
names, every run. A ratio written in `theme.py`'s prose without both colour names is
rejected outright by `test_no_ratio_is_written_without_naming_the_two_colours` — an
unverifiable figure is indistinguishable from an invented one.

- AA floor for anything that reads as words: **4.5:1**. Non-text floor for an edge, a focus
  ring or a status glyph that has to be seen: **3:1** (WCAG 1.4.11).
- **A colour that cannot clear its floor goes in `RULE_ONLY` with the reason**, or is
  restricted to `EDGE_ON`/`SURFACES`. It is never quietly used as type anyway. An
  unclassified colour fails `TestEveryColourTokenSaysWhatItIsFor`, and a colour in both
  `TYPE_ON` and `RULE_ONLY` fails it too — those two are the file contradicting itself.
- **Every declared `_WELL` must have an ink measured on it.** A well is a tint behind a
  sentence; a well with no ink measured on it is a coloured box with unreadable words in it,
  which is the failure this whole suite exists to prevent.
- Keep: `role="banner"` / `role="main"` / `role="complementary"` / `role="log"`,
  `aria_live` set explicitly on each notice (the region is only on screen while it has
  something to say, so the role's default is not enough), `aria_expanded` + `aria_controls`
  on both rail toggles pointing at one spelling of `RAIL_ID`, accessible names on every
  icon-only control, 44px minimum touch targets on the rail's collapse control and its pill,
  `tab_index=0` on the log so a keyboard can scroll it, and the `hu-sr-only` word beside
  every outcome glyph — "no se ejecutó" is not "falló".
- **The figures are `hu-num`, not a font feature.** Nothing in this repo claims Barlow ships
  tabular figures; it was not verified. `.hu-num` sets `font-variant-numeric: tabular-nums`
  and `font-feature-settings: "tnum" 1` — both spellings, because the feature setting is
  what older engines read.

### 2.5 Architecture — from `AGENTS.md`, do not "fix" these

| Fact | Why |
|---|---|
| `theme.py` imports nothing and defines nothing | `rxconfig.py` imports it with `sys.path` cut to its own directory. A token file with an import or a function is one the config can never read. Enforced by `TestTheTokenFileStaysReadableFromAnywhere`. |
| `APPEARANCE`/`RADIX_*` live in `theme.py` and `app.py` passes them to `rx.theme()` | Same reason: `get_config()` cannot read that file, so the handoff is explicit. Every value verified against `reflex_components_radix`'s own `Literal` types. |
| `assets/huella.css` mirrors the tokens as `--huella-*` custom properties | A class naming a colour and a component naming the same colour must be one value. A value edited on one side only is the bug the mirror exists to make greppable. |
| `price_display` arrives pre-formatted from `money.minor_to_display` | `money.py` cannot be called on a Var inside a component, which is also what keeps the storefront's major units and UCP's minor units from meeting on screen. |
| `image_url` is `""`, never `None` | `rx.cond` needs a value to test, and a product COROS ships no photo of is still buyable — it gets a real fallback, not an empty frame. |
| `trace_panel._level_rule` returns whole `border` shorthands per branch | An f-string over a Var *in a prop* stringifies the Var into a sentinel Reflex re-parses as text. Do not interpolate a colour into a declaration there. |
| Nothing in the mark rotates, and a `transform` would go on a `<g>` | On a shape Reflex renders it into CSS, where a rotate with three arguments is not valid syntax. |

### 2.6 Tests and process

- `make check` must stay green.
- Every stated ratio in `theme.py` is re-derived by `tests/test_huella_theme.py`, not
  asserted by hand. Changing a colour means updating the comment **and** §4 of this file
  **and** `assets/huella.css` in the same commit — see `AGENTS.md`'s maintenance contract
  row for `apps/huella/huella/ui/theme.py`. The test enforces the comment; nothing enforces
  this table but discipline.
- Append to `docs/DECISIONS.md`; it is append-only.
- Comments stay minimal — a counterintuitive fact, never a restatement of the code.
  Docstrings that carry reasoning are the house style and are wanted.

---

## 3. The contrast helper

Paste this rather than eyeballing a swatch or trusting a browser extension's rounding.
`lum()` is the same function Brújula's suite uses. **`ratio()` is not:** Huella's rounds to
**four** places where `VISUAL-BRIEF-BRUJULA.md` §3 and `tests/test_brujula_theme.py` round
to two, and the difference is load-bearing rather than fussy — see the docstring below.

```python
def lum(value):
    h = value.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(a, b):
    """Four decimals, not two.

    A floor is compared against this value. Rounded to the two decimals a comment states,
    anything in [2.995, 3.0) becomes exactly 3.0 and passes a `>= 3.0` assertion it should
    fail — which is precisely where Strava's real orange lands on SHEET_2. Four places still
    sit well inside the 0.01 tolerance the stated-ratio comparison allows.
    """
    la, lb = lum(a), lum(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 4)


def hue(value):
    import colorsys

    h = value.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def apart(a, b):
    """Degrees between two hues the short way round."""
    gap = abs(hue(a) - hue(b))
    return min(gap, 360 - gap)
```

Two calls worth having in your fingers:

- `ratio("#EEF3F7", "#FFFFFF")` → `1.1171` — `READOUT` on `SHEET`, the pair that is the
  whole reason there are two registers.
- `ratio("#FC5200", "#F1F4F7")` → `2.9977` — `STRAVA` on `SHEET_2`, the pair that costs the
  vendor's mark a surface. At two decimals it reads `3.0` and looks like a pass.

Every figure in §4 and §5 below was recomputed with these functions and checked against
`theme.py`'s own comments; they agree everywhere.

---

## 4. Contrast tables

Generated from `theme.TYPE_ON`, `theme.EDGE_ON` and `theme.RULE_ONLY` — the file's own claim
about which colour sits on which surface — with the ratio each pair measures today. Anything
here drifting from `theme.py`'s comments is a test failure, not a doc-only problem.

### 4.1 Type — the instrument (AA floor 4.5:1)

| Colour | On | Ratio |
|---|---|---|
| READOUT | DASH | 16.98:1 |
| READOUT | INK | 15.20:1 |
| READOUT | INK_2 | 13.34:1 |
| SUB | DASH | 6.74:1 |
| SUB | INK | 6.04:1 |
| SUB | INK_2 | 5.29:1 |
| TRACE | DASH | 10.41:1 |
| TRACE | INK | 9.32:1 |
| TRACE | INK_2 | 8.18:1 |
| FLAG_INK | DASH | 9.59:1 |
| FLAG_INK | INK | 8.59:1 |
| FLAG_INK | INK_2 | 7.54:1 |
| FLAG_INK | FLAG_WELL | 8.72:1 |
| AMBER_INK | DASH | 11.19:1 |
| AMBER_INK | INK | 10.02:1 |
| AMBER_INK | INK_2 | 8.79:1 |
| AMBER_INK | AMBER_WELL | 9.35:1 |
| SUCCESS_INK | DASH | 10.14:1 |
| SUCCESS_INK | INK | 9.08:1 |
| SUCCESS_INK | INK_2 | 7.97:1 |
| SUCCESS_INK | SUCCESS_WELL | 8.77:1 |
| INK | TRACE | 9.32:1 |
| INK | TRACE_DEEP | 5.52:1 |

### 4.2 Type — the sheets, and the one register-neutral token (AA floor 4.5:1)

`ON_FILL` is the only token declared on fills in both halves of the app: white on a dark
fill is white on a dark fill whichever register the fill belongs to.

| Colour | On | Ratio |
|---|---|---|
| INK | SHEET | 16.98:1 |
| INK | SHEET_2 | 15.38:1 |
| SHEET_QUIET | SHEET | 5.86:1 |
| SHEET_QUIET | SHEET_2 | 5.31:1 |
| SHEET_TRACE | SHEET | 5.90:1 |
| SHEET_TRACE | SHEET_2 | 5.35:1 |
| SHEET_FLAG_INK | SHEET | 6.36:1 |
| SHEET_FLAG_INK | SHEET_2 | 5.76:1 |
| SHEET_FLAG_INK | SHEET_FLAG_WELL | 5.44:1 |
| SHEET_AMBER_INK | SHEET | 7.59:1 |
| SHEET_AMBER_INK | SHEET_2 | 6.88:1 |
| SHEET_AMBER_INK | SHEET_AMBER_WELL | 6.60:1 |
| SHEET_SUCCESS_INK | SHEET | 7.03:1 |
| SHEET_SUCCESS_INK | SHEET_2 | 6.36:1 |
| SHEET_SUCCESS_INK | SHEET_SUCCESS_WELL | 5.99:1 |
| ON_FILL | GRAPHITE | 10.37:1 |
| ON_FILL | GRAPHITE_DEEP | 16.10:1 |
| ON_FILL | SHEET_TRACE | 5.90:1 |

### 4.3 The registers do not transfer — every figure here is a **required failure**

`TestTheSheetsAreASecondPalette` parametrizes both directions and asserts each measures
**under** AA. If any row here ever clears 4.5:1, a surface drifted and the two-palette
argument is gone — re-read `theme.py`'s docstring before deleting anything.

| Instrument token | On SHEET | | Sheet token | On DASH |
|---|---|---|---|---|
| READOUT | 1.12:1 | | INK | 1.12:1 |
| TRACE | 1.82:1 | | SHEET_TRACE | 3.21:1 |
| AMBER_INK | 1.69:1 | | SHEET_AMBER_INK | 2.50:1 |
| FLAG_INK | 1.98:1 | | SHEET_FLAG_INK | 2.98:1 |
| SUCCESS_INK | 1.87:1 | | SHEET_SUCCESS_INK | 2.70:1 |
| SUB | 2.81:1 | | SHEET_QUIET | 3.24:1 |

### 4.4 Edges — a line, a ring or a glyph somebody has to see (non-text floor 3:1)

| Colour | On | Ratio |
|---|---|---|
| EDGE | DASH | 4.50:1 |
| EDGE | INK | 4.03:1 |
| EDGE | INK_2 | 3.54:1 |
| TRACE | DASH | 10.41:1 |
| TRACE | INK | 9.32:1 |
| TRACE | INK_2 | 8.18:1 |
| FLAG | DASH | 4.48:1 |
| FLAG | INK | 4.02:1 |
| FLAG | INK_2 | 3.52:1 |
| SUCCESS | DASH | 4.24:1 |
| SUCCESS | INK | 3.80:1 |
| SUCCESS | INK_2 | 3.33:1 |
| SUCCESS | SHEET | 4.47:1 |
| SUCCESS | SHEET_2 | 4.05:1 |
| SHEET_EDGE | SHEET | 3.48:1 |
| SHEET_EDGE | SHEET_2 | 3.15:1 |
| STRAVA | SHEET | 3.31:1 |

`FLAG` is in `EDGE_ON` and deliberately **not** in `TYPE_ON`: `FLAG on INK` 4.02:1 is under
AA, so it is the glyph and the rule while `FLAG_INK` carries the sentence.
`test_the_flag_never_carries_the_sentence_itself` asserts both halves.

### 4.5 Declared, and never type (`theme.RULE_ONLY`)

| Colour | Measures | Reason |
|---|---|---|
| RULE | `RULE on DASH` 16.20:1, `RULE on INK` 14.51:1 | COROS's border ivory. On the instrument it is the loudest value in the file, so a hairline drawn in it reads as a highlight. Hairlines are `GRID`; a boundary somebody has to see is `EDGE`. |
| AXIS | `AXIS on INK` 1.78:1, `AXIS on DASH` 1.99:1, `AXIS on INK_2` 1.57:1 | The chart's gridlines and the baseline under a sparkline — deliberately under every floor, because a gridline that competes with the series is a gridline that hides it. Nothing carrying a value is drawn in it. |
| STRAVA | `STRAVA on SHEET` 3.31:1; `STRAVA on SHEET_2` 2.9977:1 | The vendor's, and the only colour here we do not own. Clears the graphic floor on the one surface it is declared on and nothing more, and may not be darkened to reach AA. It never carries words. |

### 4.6 The alpha tokens

Neither is a hex, so neither appears in the registries; both are hairlines by construction
and neither may be used where a boundary has to be seen. Composited over the surfaces they
are drawn on, they measure:

| Token | Over | Composites to | Ratio |
|---|---|---|---|
| GRID `rgba(238,243,247,0.09)` | DASH | `#20252A` | 1.23:1 |
| GRID | INK | `#293038` | 1.27:1 |
| GRID | INK_2 | `#323A45` | 1.30:1 |
| SHEET_LINE `rgba(22,29,37,0.10)` | SHEET | `#E8E8E9` | 1.22:1 |
| SHEET_LINE | SHEET_2 | `#DBDEE2` | 1.22:1 |

`GRID` is alpha over the readout on purpose, so a hairline sits in its own surface's hue
rather than being a fourth grey that has to be maintained per surface.

---

## 5. The three answers are three hues, not three shades

`evidence.Confidence` has three values and Huella exists to say which one is true. "The
window is thick and recent", "it is thinner than I would like" and "this leans on thin or
stale data" are **different sentences**, so they get different hues — three steps along one
ramp is a gradient, and these are not points on a gradient.

| Confidence | Ink | Well | Ink on its own well | Hue |
|---|---|---|---|---|
| `high` | `SUCCESS_INK #7FD07F` | `SUCCESS_WELL #12231A` | 8.77:1 | 120.0° |
| `medium` | `AMBER_INK #F0C05A` | `AMBER_WELL #2A2113` | 9.35:1 | 40.8° |
| `none` | `FLAG_INK #FF9DA6` | `FLAG_WELL #2A1518` | 8.72:1 | 354.5° |

Separations, the short way round: `high`↔`medium` **79.2°**, `medium`↔`none` **46.3°**,
`high`↔`none` **125.5°**. `test_the_three_confidences_are_tellable_apart_by_hue` fails on
any pair under the 25° Brújula's brand suite treats as tellable-apart.

The other two keyed tables are read on `INK`, the panel they are drawn on:

| Table | Key | Colour | On INK | Floor |
|---|---|---|---|---|
| `LEVEL_COLOR` | `info` | SUB | 6.04:1 | AA — it is a word on the row |
| | `guardrail` | TRACE | 9.32:1 | AA |
| | `error` | FLAG_INK | 8.59:1 | AA |
| `OUTCOME_COLOR` | `pass` | SUCCESS | 3.80:1 | non-text — it is a tick |
| | `fail` | FLAG | 4.02:1 | non-text — it is a cross |
| | `not_run` | SUB | 6.04:1 | non-text — it is a dash |

`not_run` is not `fail`: a check nobody ran is not a check that failed, so it gets the
secondary type colour and a dash, never the flag. Each glyph also carries an `hu-sr-only`
Spanish word, because a glyph is the whole answer for a sighted reader and silence for
everybody else.

**`theme.UNCERTAINTY` is the enforced allowlist.** Five tokens — `FLAG`, `FLAG_INK`,
`FLAG_WELL`, `SHEET_FLAG_INK`, `SHEET_FLAG_WELL` — carry the "do not lean on this" answer
between them, and `TestRedIsReservedForTheUncertaintyMoment` scans every colour in the file
by hue, saturation and lightness and fails on any saturated red outside it. That scan guards
the **palette**, not the usage: a sixth red would only be a second way of saying one thing.

---

## 6. Type, geometry, motion — as shipped

### 6.1 Type

- **Interface face: Barlow.** COROS self-hosts PF Din Text Pro from their Shopify CDN
  (`SF-Heading-font`/`SF-Body-font`). It is a licensed Parachute face we cannot ship, so
  Barlow — the same DIN-derived skeleton, on Google Fonts — stands in.
- **There is no second display family, and that is the decision.** `FONT_DISPLAY` is
  deliberately `FONT`. Brújula's Fraunces is its editorial voice; an instrument does not
  have one — a serif over a table of splits is a magazine pretending to be a dashboard.
  Weight and tracking do the work a second family would otherwise do.
  `test_the_display_face_is_the_interface_face` and
  `test_brujulas_display_face_is_not_here` both fail if that changes. Giving Huella a display
  face needs a stated reason of its own in `DECISIONS.md`, not Brújula's.
- **Mono: JetBrains Mono, and the argument is the instrument rather than the costume.** A
  monospace is tabular by construction, so a column of paces, distances and prices aligns
  without depending on a proportional family shipping `tnum`. Every figure in the app is set
  in it — the readouts, the window counts, the prices, the whole audit rail.
- One `<link>` for both families (`FONT_HREF`), verified 30 Jul 2026 to return all six
  weights and the `latin-ext` subsets. Spanish is the whole interface, and a family without
  the accents renders "Huella" beside "sesion".
  `test_the_font_request_names_exactly_the_families_the_tokens_use` fails on a family in the
  stack but not the request, and on one in the request but not the stack.
- Tracking: `TRACK_DISPLAY -0.02em` (the wordmark and the refusal headlines),
  `TRACK_TIGHT -0.01em`, `TRACK_EYEBROW 0.16em` (uppercase runs only).
  **`TRACK_READOUT` is `0` and a figure never gets negative tracking** — tightening a
  monospace column undoes the only reason it is a monospace.

### 6.2 Geometry and depth

- Radii are **tighter than Brújula's**: `RADIUS 6px`, `RADIUS_SM 3px`, `RADIUS_LG 10px`,
  `RADIUS_PILL 999px`. A row in a table is not a card, and a 10px corner on a 28px row is a
  pill pretending to be data.
- **On the instrument, elevation is a lighter surface — not a shadow.** A drop shadow on
  `DASH` is a darker black on a near-black. The three steps are `DASH` → `INK` → `INK_2`,
  and `SHADOW_SM/MD/LG` belong to the sheets and to the gate card. They are cast in the
  instrument's own blue-black (`rgba(6,10,14,…)`) so a modal over it does not fringe warm.
- Layout: `CONTENT_W 76rem` and `RAIL_W 26rem`, both wider than Brújula's — the content is a
  table of weeks, not a column of prose, and the rail carries provenance beside every parsed
  figure. `ROW_H 2.25rem` is the one number that is a layout claim rather than a taste: a row
  has to stay a row, so the readout column is capped and the label column is what truncates.
- Two focus rings, one per register. `FOCUS_RING` is the instrument's,
  `FOCUS_RING_SHEET` the light half's. `EDGE` is the visible boundary inside the ring on the
  instrument and `SHEET_EDGE` on a sheet, which is why both are `EDGE_ON` colours.
- Easing: `EASE cubic-bezier(0.22,0.61,0.36,1)` for state changes,
  `EASE_EXPO cubic-bezier(0.16,1,0.3,1)` for the one entrance.

### 6.3 The `hu-` namespace — ten classes, three of which animate

`assets/huella.css` owns the namespace and is listed in `app.py`'s `stylesheets=`, which is
what makes the classes real: **a component naming a class with no rule behind it is a silent
no-op**. The stylesheet and the class names in `ui/*` are one contract, and the file exists
only for what a Reflex style prop cannot express — keyframes, pseudo-elements, `::selection`,
`:focus-visible`, the scrollbar, and the reset that stops Radix drawing its own field inside
the composer.

| Class | What it is for | Animates |
|---|---|---|
| `hu-sr-only` | visually hidden, still in the accessibility tree: the page's `h1`, and the word a status glyph shows instead of saying | no |
| `hu-skip` | the skip link, off-screen until focused, then a real first tab stop | no |
| `hu-num` | tabular figures, both spellings — the class, not the family, is what aligns a column | no |
| `hu-clamp-2` | two-line title clamp on a product card | no |
| `hu-scroll` | the instrument's scrollbar, with `scrollbar-gutter: stable` so a growing log does not shove every row sideways when the bar appears | no |
| `hu-rail` | pulls the log's focus ring *inside* the rail's `overflow: hidden` via `outline-offset: -2px` | no |
| `hu-dock` | strips Radix's own field ring and background inside the composer and moves the focus job outward to the dock | no |
| `hu-kit` | the kit arriving: the summary resolves and the grid follows as the same beat | **yes** |
| `hu-pulse` | the presence dot while a turn runs | **yes** |
| `hu-shake` | the gate's refusal on a wrong password | **yes** |

Two rules in that file are load-bearing and easy to undo by accident:

- **`::selection` sets the tint and no text colour.** Forcing one is the mistake the two
  registers punish: styled for the dashboard, a selection would erase a sheet
  (`READOUT on SHEET` 1.12:1). Each surface keeps its own ink.
- **There is no selector matching `/strava/…` and no bare `img` or `svg` rule** for one to
  inherit. A blanket rule reaching the vendor's assets is a modification of them, and this
  file's bar for itself is that even a `max-width` would be one. Where the marks *do* need to
  fit a narrow viewport, that is an authored per-element decision in `connect.py`, not a
  stylesheet rule — see §7, and note that the stylesheet's own sentence and `connect.py`'s
  `max_width="100%"` read as being in tension. Resolve that wording before either file is
  next edited; do not resolve it by adding a stylesheet rule.

**Three of the ten have no consumer yet, and that is a state of the code, not a spare-parts
bin.** As of this writing nothing in `apps/huella/huella/` names `hu-skip`, `hu-dock` or
`hu-shake`: `app.py::_skip_link()` re-implements the skip link with style props and a
`_focus` trigger instead of the class, the composer is a bare `rx.form` with no dock wrapper
(so Radix's own field ring is never stripped and `SUB` never reaches the placeholder), and
the gate's error branch is a plain `rx.text` with no shake on the card — which also means the
reduced-motion held-flag ring in §6.4 has nothing to attach to. Wiring each class up is the
fix; deleting the rules is not, because then the reduced-motion contract loses three of its
five entries. Whichever way it goes, the two files move in one commit.

### 6.4 The reduced-motion contract — an alternative, not an off switch

A blanket `animation-duration: 0.001ms` is what the `prefers-reduced-motion: reduce` block
replaces, and it is worth naming what that costs: the working pulse, the kit's arrival and
the gate's refusal all collapse into nothing happening, and an athlete waiting on a turn is
told **less** than they were before. Reduced motion means no vestibular triggers — no travel,
no scaling, no sweeping — **not no information**. Every moment keeps its meaning and drops
its movement:

| Moment | Full motion | Reduced |
|---|---|---|
| the kit | `hu-kit-in` 520ms + `hu-kit-follow` 380ms/120ms, both `translateY` on `EASE_EXPO` | `hu-kit-resolve` — opacity only, 300ms and 240ms/90ms, same two-beat structure |
| "working" | `hu-pulse`, an expanding `TRACE` ring, 1.7s infinite | `animation: none` plus a **held** 3px ring, for exactly as long as the turn lasts |
| the refusal | `hu-shake`, 420ms of translate | `animation: none` plus a held `2px solid FLAG` **outline**, standing while the error stands |
| everything else | authored transitions | `transition-duration: 90ms` — hover and focus feedback is a state change, not a journey; kept, shortened |
| the page | `scroll-behavior: smooth` | `auto` |

Three details that are decisions, not accidents:

- **The kit's default rendered state is the finished one.** Both keyframes run `backwards`
  from an invisible start; if the animation never runs, the kit is simply there. Nothing is
  invisible waiting on a script.
- **No blur and no clip wipe** in `hu-kit`, which is where this parts company with Brújula's
  kit: over a column of figures a blur reads as a rendering fault, not as an arrival.
- **The reduced refusal is an `outline`, not a `box-shadow`.** The gate card already spends
  its shadow on `SHADOW_MD`, and a Reflex style prop compiles to a class of the same
  specificity — the two would race.

Anything added to that file that moves must arrive with its reduced-motion counterpart in
the same commit. A new `hu-` class with no rule behind it fails silently; a new animation
with no fallback fails only for the people it fails hardest for.

---

## 7. The 414px contract

414px is the narrowest viewport the app is claimed to work at, and three things have to hold
there. `box-sizing: border-box` is global — Reflex 0.9.7's own `__reflex_style_reset.css`
sets it on `*` inside `@layer __reflex_base` — so `width: 100%` plus padding never overflows
by construction. Do not add a component that assumes otherwise.

**No horizontal overflow.**

- The shell's flex is `direction=rx.breakpoints(initial="column", lg="row")`. The rail is
  `width=["100%", "100%", "100%", RAIL_W]` — **`RAIL_W` is 26rem = 416px and only applies at
  `lg`**. A rail given its desktop width on a phone is 2px wider than the viewport. Do not
  collapse that array to a scalar.
- The main column is `flex="1"` with **`min_width="0"`**. Without it a flex item refuses to
  shrink below its content and the rail is pushed off the viewport instead of sharing it.
- `CONTENT_W 76rem` is a *max*-width and imposes no floor.
- Anything that can be a long unbroken token carries `word_break`: the rail's event names and
  summaries (`break-word`, plus `min_width="0"` on the row's inner stack — the rail is 26rem
  and event names run long), the token fingerprint (`break-all`), and the requirement values.
- The kit's grid is `columns=rx.breakpoints(initial="1", sm="2")` — one card at 414px. Two
  columns and no more anywhere: `lg` is where the rail moves in beside the column.
- Chip and figure rows are `rx.flex(wrap="wrap")`, so the scopes, the uncertainty flags, the
  window counts and the named unavailable devices all reflow instead of pushing the row wide.
- The header's two buttons hide their words below `md` (`display=["none","none","block"]`)
  and each keeps an `aria_label`, so the narrow viewport does not leave two unnamed icon
  buttons side by side.

**The Strava mark is a 237px fixed-width image whose `max_width` must hold.**

`btn_strava_connect_with_orange.svg` is intrinsically **237×48** and `connect.py` renders it
at `width="237px"`, `height="auto"`, `max_width="100%"`. The arithmetic at 414px: the column
pads `1rem` a side (382px left), `connect.panel()`'s inner stack pads `_PAD_X = 1.05rem` a
side (348.4px left), so the button fits at its natural size with ~111px to spare. The
`max_width` is the guard for every narrower case — a smaller phone, a future nested
container, a padding change — and `height="auto"` beside it is what keeps the scaling
**proportional**. The pair is the whole point, and neither half survives alone: `max_width`
without `height="auto"` squashes the mark to a fixed 48px height, which is a distorted
rendering of an asset the terms say may not be altered. This repo's own bar, recorded in
`AGENTS.md`, is "never modified, altered or animated"; nothing here has measured what Strava
would say about a proportional cap specifically, so treat the two props as one indivisible
decision rather than as a licence to resize freely. The powered-by mark is the same shape:
`width="11rem"`, `height="auto"`, `max_width="100%"`, from a 365×37 source.

**Focus rings survive.**

- The global ring is `outline: 2px solid TRACE; outline-offset: 2px` — an outline is drawn
  outside the border box and takes no layout space, so it can never *cause* overflow. What it
  can do is get clipped.
- `trace_panel.panel()` sets `overflow="hidden"` and the log inside it is focusable, so the
  ring drawn 2px outside the log's edge would be cut off exactly where it is needed.
  `.hu-rail [role="log"]:focus-visible { outline-offset: -2px }` pulls it inside. Same ring,
  same colour — the rail is the instrument too.
- `connect.panel()` also sets `overflow="hidden"`. `FOCUS_RING_SHEET` is a 3px-spread
  box-shadow with no offset, and the controls inside sit behind 1.05rem (16.8px) of panel
  padding, so the ring clears the clip. **A control moved flush to that panel's edge loses
  its focus ring silently** — keep the padding or move the control.
- A control on a sheet must carry `FOCUS_RING_SHEET` as its own style prop. No selector in
  `huella.css` can tell a light surface from a dark one — nothing in the `hu-` namespace
  marks a sheet — so the light-register ring is a token the components spend, never a rule.
- Touch targets: 44px minimum on the rail's collapse control and its reopen pill.

---

## 8. What you must NOT do

- **Do not restate red as "the uncertainty flag and nothing else".** That was the rule before
  30 Jul 2026 and it disagreed with the app's own registries. Red is "do not lean on what is
  on screen" and covers three states; amber is "usable with reservations"; a refusal arrived
  at correctly is uncoloured.
- Do not let any saturated red appear outside `theme.UNCERTAINTY`. The hue/saturation
  /lightness scan enforces it; do not weaken the test to ship a red badge.
- Do not push `OUTCOME_COLOR["fail"]` or `LEVEL_COLOR["error"]` down to amber. That puts a
  hard failure and a merely thin window in one colour — the collapse §2.1 refuses from the
  other side when it declines COROS's `--color-warning`.
- Do not colour a correct refusal. `buy_nothing` and `not_sold_locally` stay in the neutral
  register.
- **Do not use an instrument token on a sheet or a sheet token on the instrument**, and do
  not add a token that works on both. §4.3 is a table of required failures; a token that
  passes in both registers is a token in the wrong one.
- Do not use `GRAPHITE`/`GRAPHITE_DEEP` outside a sheet, and do not put anything but
  `ON_FILL` on them.
- Do not use `RULE`, `AXIS` or `GRID` as type or as a visible edge.
- **Do not recolour, darken, filter, crop, animate, re-type or wrap-in-an-animation any
  Strava asset**, do not add a second orange, do not put the mark on `SHEET_2`, do not place
  two of the vendor's marks on one screen, do not make the mark more prominent than Huella's
  own name, and do not use it as the app icon. There is no legal fix for the `SHEET_2` figure
  — the surface moves, never the orange.
- Do not add a colour to `theme.py` without classifying it in `SURFACES`, `TYPE_ON`,
  `EDGE_ON` or `RULE_ONLY` **with its measured ratio in a comment naming both colours**.
- Do not state a ratio anywhere without naming both colours. A bare figure is unverifiable
  and the theme suite rejects it outright.
- Do not round a floor comparison to two decimals. See §3.
- Do not give Huella a second display family, a warm Radix gray, or a light Radix appearance.
- Do not add a shadow to anything on the instrument — elevation there is `INK_2`.
- Do not add a second "is this still running" element beside the presence dot.
- Do not name an `hu-` class that has no rule in `assets/huella.css`, and do not add an
  animation there without its `prefers-reduced-motion` counterpart in the same commit.
- Do not put an import or a function in `theme.py`.
- Do not render `State.blocking` or `CheckRow.detail` outside the audit rail.

---

## 9. How to run and verify

```bash
cd /home/sebastian/versioned-code/corosBot
./.venv/bin/python -m pytest tests/test_huella_theme.py -p no:warnings --tb=short
```

`pytest.ini` already carries `-q` and `-m "not live"` in `addopts`; do not add a second `-q`
(it becomes `-qq` and suppresses the summary line) and do not add `PYTHONPATH=` (the import
roots are declared once, in `pytest.ini`).

Local dev needs `.env` with `GEMINI_API_KEY` and `HUELLA_PASSWORD` — see `.env.example`:

```bash
cd apps/huella && ../../.venv/bin/reflex run
```

**Playwright MCP protocol**: navigate the running app at 1440px and 414px, snapshot,
screenshot, read the console. Check: type hierarchy carries structure and not just hue; no
token used as type falls under the ratios in §4; the attribution block renders on white with
the marks unmodified; focus rings visible on the gate's field, the composer, the connect
button and the rail's log; no horizontal scroll at 414px; the four refusal panels read as
visually distinct from a recommendation and from each other; and the three confidence chips
read as three answers rather than three shades. Measure contrast **in the browser from
`getComputedStyle`** rather than only from the theme's own helper — a bug in the helper is
invisible to a test that uses it. Save screenshot pairs to
`docs/screenshots/huella-{1440,414}.png`; log console errors to `docs/QA-HUELLA.md`.

---

## 10. Acceptance

- [ ] `make check` green
- [ ] Every §2 non-negotiable intact
- [ ] Every ratio in §4 and §5 matches what `tests/test_huella_theme.py` measures today
- [ ] No saturated red outside `theme.UNCERTAINTY`, and nothing anywhere restating the
      pre-30-Jul rule
- [ ] No DecaBot indigo anywhere in `theme.py`
- [ ] No instrument token on a sheet and no sheet token on the instrument
- [ ] `STRAVA` declared on `SHEET` and nothing else; both marks byte-identical to the
      vendored files; attribution placed exactly once
- [ ] Every `hu-` class a component names exists in `assets/huella.css`, and every animation
      there has a `prefers-reduced-motion` counterpart
- [ ] Runs clean at 1440px and 414px, with no horizontal overflow and every focus ring
      visible
