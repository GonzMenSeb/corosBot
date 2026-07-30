"""Huella's design tokens: the same COROS values as Brújula, in the opposite register.

The seven anchors below are the ones read off coros.com.co's inline theme stylesheet — six
CSS custom properties and one literal red — and they are shared with `brujula/ui/theme.py`
on purpose. What sits on top of them is not shared at all.

**Reading a training history is reading instrumentation.** Brújula is paper: one product at
a time, warm, editorial. Huella is a dashboard over eighteen months of somebody's own
efforts — dense rows, figures that align down a column, a surface that does not compete
with a chart drawn on it. So Huella is dark-primary, and the dark is COROS's own
`--color-body-text` used as a *surface*: DASH is one step below it, INK is it, INK_2 is one
step above. An inverted paper palette was the other option and it is a muddy grey, not an
instrument.

**Two of COROS's seven values are light-register-only, and that is measured, not a
preference.** `--color-primary` is a button on their white storefront: GRAPHITE on SHEET
10.37:1, and GRAPHITE on DASH 1.83:1 — invisible on the instrument, and not rescuable with
a border, because EDGE on GRAPHITE 2.46:1 too. GRAPHITE and GRAPHITE_DEEP are therefore
buttons inside a sheet and nowhere else, and the instrument's own control fill is TRACE.

**Red is the uncertainty flag, and that is a different single job from Brújula's.**
`#ea2e41` is COROS's call to action; Brújula spends it on the honest refusal, Huella on
"this advice leans on thin or stale data". Nothing else may have it — an app whose whole
premise is uncertainty-aware reasoning cannot spend its flag colour on a generic error.
It is not a colour for words on our surfaces at any size (FLAG on INK 4.02:1), so FLAG_INK
carries the sentence and FLAG carries the glyph and the rule.

**COROS's `--color-warning: #ff706b` is deliberately not used.** A second red makes a thin
data window and a refused request the same answer. AMBER_INK is the middle answer here.

**COROS's own `--color-success: #3a8735` cannot carry words.** SUCCESS on SHEET 4.47:1
lands three hundredths under the AA floor — their figure, not a rounding of ours — so it
stays the tick and the fill, and the word-carrying greens are SUCCESS_INK and
SHEET_SUCCESS_INK.

**`--color-sub-text: #9A9A9A` inverts between the two apps.** Brújula keeps it out of type
because SUB on SHEET 2.81:1; on the instrument the same grey is a working secondary type
colour at SUB on INK 6.04:1. It is the one anchor Huella can use for words where Brújula
cannot, and it is why the light half of this file needs its own SHEET_QUIET.

**`--color-border: #EEEEE0` is declined.** It is the storefront's only warmth and the
premise of Brújula's entire register; here RULE on DASH 16.20:1 makes a "hairline" in it
the brightest thing on the screen. The instrument's hairlines are GRID.

**Strava's orange is the only colour in this file we do not own.** `#FC4C02` arrives inside
official assets — the "Powered by Strava" mark, the "Connect with Strava" button, the "View
on Strava" link glyph — which may never be recoloured, modified or animated. So the
attribution block is a declared *light* surface and the figure that matters is STRAVA on
SHEET 3.40:1, which clears the graphic floor for an unmodified mark. There is no darkened
Strava orange in this file and there must never be one.

**The vendor's orange and our flag red are not tellable apart as glyphs.** They are 23.8°
of hue apart and STRAVA on FLAG 1.24:1. Two dots that close are one dot. The guarantee is
that they never share a surface — STRAVA is declared on the sheets, FLAG on the instrument
— and that the flag is never a bare dot: it is FLAG_INK words on FLAG_WELL.

**`_INK` names the variant that carries the words, not a darker one.** On the sheets that
means darker than COROS's value; on the instrument it means lighter. Same convention as
Brújula, opposite direction, because the surface is on the other side of the type.

DecaBot's indigo appears nowhere. Two demos on one VPS that share a palette read as one
demo with two front doors.

Every colour is classified by SURFACES, TYPE_ON, EDGE_ON or RULE_ONLY — a colour may be
both a surface and a type colour, never both a type colour and RULE_ONLY — and every pair
TYPE_ON or EDGE_ON names carries its measured ratio in the comment above it.
`tests/test_huella_theme.py` recomputes each of those figures from the two colours it
names: a ratio here is a measurement, never a memory.
"""

from __future__ import annotations

# ── COROS's published values ──────────────────────────────────────────────────
# coros.com.co, inline theme stylesheet. Left column is their variable name.
INK = "#161d25"  # --color-body-text, and here a surface
GRAPHITE = "#404040"  # --color-primary; a sheet button only, see the docstring
GRAPHITE_DEEP = "#212121"  # --color-primary-darker
SUB = "#9A9A9A"  # --color-sub-text
RULE = "#EEEEE0"  # --color-border, declined
SUCCESS = "#3a8735"  # --color-success
FLAG = "#ea2e41"  # their CTA red, reserved here for the uncertainty flag

# ── the instrument ────────────────────────────────────────────────────────────
# Three steps, with COROS's own near-black as the middle one: the page, a panel on it, a
# row inside the panel. On a dark surface elevation is a lighter surface, not a shadow —
# a drop shadow on DASH is invisible, which is why SHADOW_* below belongs to the sheets.
#   READOUT on DASH 16.98:1     READOUT on INK 15.20:1     READOUT on INK_2 13.34:1
#   SUB on DASH 6.74:1     SUB on INK 6.04:1     SUB on INK_2 5.29:1
DASH = "#0C1116"
INK_2 = "#1F2833"
# Not pure white. At the row density this app renders, pure white type on a near-black
# surface haloes and the figures stop aligning to the eye even though they align on the
# grid. ON_FILL is the pure white, and it only ever sits on a fill.
READOUT = "#EEF3F7"
# A boundary somebody has to see: a focused field, a panel that has to separate from the
# page, the edge of a selected row. INK_2 is in here because a panel really does sit on a
# row sometimes — a chart inside an expanded row draws its edge against the lighter stop.
#   EDGE on DASH 4.50:1     EDGE on INK 4.03:1     EDGE on INK_2 3.54:1
EDGE = "#6B7E8C"
# The chart's own furniture, deliberately below every floor. See RULE_ONLY.
AXIS = "#3A4753"
# Hairlines and gridlines, alpha over the readout so they sit in the surface's own hue.
GRID = "rgba(238,243,247,0.09)"

# ── the trace: the accent, and the plotted series ─────────────────────────────
# Cyan, not a warm accent: it is the far side of the wheel from both COROS's red and
# Strava's orange, so a plotted series can never be mistaken for a flag. Brass would have
# been Brújula's, and on a near-black it is a dim brown.
#   TRACE on DASH 10.41:1     TRACE on INK 9.32:1     TRACE on INK_2 8.18:1
#   INK on TRACE 9.32:1     INK on TRACE_DEEP 5.52:1
#   ON_FILL on GRAPHITE 10.37:1     ON_FILL on GRAPHITE_DEEP 16.10:1
#   ON_FILL on SHEET_TRACE 5.90:1
TRACE = "#4FD1E0"
TRACE_DEEP = "#33A0B4"
# The words on a fill, in either register — the one register-neutral type colour, because
# white on a dark fill is white on a dark fill on both halves of the app.
ON_FILL = "#FFFFFF"

# ── the three answers about the data itself ───────────────────────────────────
# `evidence.Confidence` has three values and they get three hues, not three shades of one:
# "the window is thick and recent", "it is thinner than I would like" and "this leans on
# thin or stale data" are different sentences and an athlete has to be able to tell.
#   FLAG_INK on DASH 9.59:1     FLAG_INK on INK 8.59:1     FLAG_INK on INK_2 7.54:1
#   FLAG_INK on FLAG_WELL 8.72:1
#   FLAG on DASH 4.48:1     FLAG on INK 4.02:1     FLAG on INK_2 3.52:1
#   AMBER_INK on DASH 11.19:1     AMBER_INK on INK 10.02:1     AMBER_INK on INK_2 8.79:1
#   AMBER_INK on AMBER_WELL 9.35:1
#   SUCCESS_INK on DASH 10.14:1     SUCCESS_INK on INK 9.08:1
#   SUCCESS_INK on INK_2 7.97:1     SUCCESS_INK on SUCCESS_WELL 8.77:1
#   SUCCESS on DASH 4.24:1     SUCCESS on INK 3.80:1     SUCCESS on INK_2 3.33:1
FLAG_INK = "#FF9DA6"
FLAG_WELL = "#2A1518"
AMBER_INK = "#F0C05A"
AMBER_WELL = "#2A2113"
SUCCESS_INK = "#7FD07F"
SUCCESS_WELL = "#12231A"

# ── the sheets: a full second palette ─────────────────────────────────────────
# A card, a modal, and the Strava attribution block. NONE of the instrument's tokens
# transfer — READOUT on SHEET 1.12:1 and TRACE on SHEET 1.82:1 are the two that come
# closest — so the light surfaces carry their own muted ink, their own edge, their own
# accent and their own three semantic inks. The semantic inks are COROS's own red and
# green taken down to AA on white by the same rule Brújula uses, so where a value here
# lands near one of Brújula's it is the derivation agreeing, not a copy.
# SHEET is COROS's own --color-main-background, which is also the surface their own brand
# values were measured on, and the one Strava's marks are legible on unmodified.
#   INK on SHEET 16.98:1     INK on SHEET_2 15.38:1
#   SHEET_QUIET on SHEET 5.86:1     SHEET_QUIET on SHEET_2 5.31:1
#   SHEET_EDGE on SHEET 3.48:1     SHEET_EDGE on SHEET_2 3.15:1
#   SHEET_TRACE on SHEET 5.90:1     SHEET_TRACE on SHEET_2 5.35:1
#   SHEET_FLAG_INK on SHEET 6.36:1     SHEET_FLAG_INK on SHEET_2 5.76:1
#   SHEET_FLAG_INK on SHEET_FLAG_WELL 5.44:1
#   SHEET_AMBER_INK on SHEET 7.59:1     SHEET_AMBER_INK on SHEET_2 6.88:1
#   SHEET_AMBER_INK on SHEET_AMBER_WELL 6.60:1
#   SHEET_SUCCESS_INK on SHEET 7.03:1     SHEET_SUCCESS_INK on SHEET_2 6.36:1
#   SHEET_SUCCESS_INK on SHEET_SUCCESS_WELL 5.99:1
#   SUCCESS on SHEET 4.47:1     SUCCESS on SHEET_2 4.05:1
SHEET = "#FFFFFF"
SHEET_2 = "#F1F4F7"
SHEET_QUIET = "#5A6673"
SHEET_EDGE = "#7F8B97"
SHEET_TRACE = "#0C6E80"
SHEET_FLAG_INK = "#BC1730"
SHEET_FLAG_WELL = "#FBE9EC"
SHEET_AMBER_INK = "#714D06"
SHEET_AMBER_WELL = "#F9EEDA"
SHEET_SUCCESS_INK = "#2A6527"
SHEET_SUCCESS_WELL = "#E5F0E4"
SHEET_LINE = "rgba(22,29,37,0.10)"

# ── Strava's own value, unmodified ────────────────────────────────────────────
# Verbatim from Strava's brand assets. It is here so that nothing has to eyedrop it, and
# so the surface it sits on is measured rather than assumed.
#   STRAVA on SHEET 3.40:1     STRAVA on SHEET_2 3.08:1
STRAVA = "#FC4C02"

# ── what the rows and the panels render ───────────────────────────────────────
# Keyed by `coros_core.trace.Level`, `coros_core.evidence.Outcome` and
# `coros_core.evidence.Confidence`; a key with no colour is a row that renders in whatever
# the previous one left behind. All three are read on INK, the panel they are drawn on.
LEVEL_COLOR: dict[str, str] = {"info": SUB, "guardrail": TRACE, "error": FLAG_INK}
LEVEL_BG: dict[str, str] = {
    "info": "transparent",
    "guardrail": "rgba(79,209,224,0.12)",
    "error": "rgba(255,157,166,0.12)",
}
# `not_run` is not `fail` — a check nobody ran is not a check that failed — so it gets the
# secondary type colour and a dash, never the flag.
OUTCOME_COLOR: dict[str, str] = {"pass": SUCCESS, "fail": FLAG, "not_run": SUB}
# The whole reason Huella exists as a separate app. `none` is the flag moment.
CONFIDENCE_COLOR: dict[str, str] = {
    "high": SUCCESS_INK,
    "medium": AMBER_INK,
    "none": FLAG_INK,
}
CONFIDENCE_BG: dict[str, str] = {
    "high": SUCCESS_WELL,
    "medium": AMBER_WELL,
    "none": FLAG_WELL,
}

# ── the classification every colour above answers to ──────────────────────────
SURFACES: dict[str, str] = {
    "DASH": "the instrument: the page every row and chart is drawn on",
    "INK": "a panel on the instrument — COROS's own body-text value, used as a surface",
    "INK_2": "one row inside a panel, an inset well, and elevation",
    "TRACE": "the instrument's control fill: a primary button, the active tab",
    "TRACE_DEEP": "that fill, pressed",
    "FLAG_WELL": "the uncertainty panel on the instrument",
    "AMBER_WELL": "the thin-window chip on the instrument",
    "SUCCESS_WELL": "the thick-window chip on the instrument",
    "SHEET": "a card, a modal, and the Strava attribution block — the surface the official marks sit on unmodified",
    "SHEET_2": "an inset row inside a sheet",
    "SHEET_FLAG_WELL": "the uncertainty panel on a sheet",
    "SHEET_AMBER_WELL": "the thin-window chip on a sheet",
    "SHEET_SUCCESS_WELL": "the thick-window chip on a sheet",
    "SHEET_TRACE": "the accent fill inside a sheet",
    "GRAPHITE": "a button inside a sheet, and only there",
    "GRAPHITE_DEEP": "that button, pressed",
}

TYPE_ON: dict[str, tuple[str, ...]] = {
    "READOUT": ("DASH", "INK", "INK_2"),
    "SUB": ("DASH", "INK", "INK_2"),
    "TRACE": ("DASH", "INK", "INK_2"),
    "FLAG_INK": ("DASH", "INK", "INK_2", "FLAG_WELL"),
    "AMBER_INK": ("DASH", "INK", "INK_2", "AMBER_WELL"),
    "SUCCESS_INK": ("DASH", "INK", "INK_2", "SUCCESS_WELL"),
    "INK": ("SHEET", "SHEET_2", "TRACE", "TRACE_DEEP"),
    "ON_FILL": ("GRAPHITE", "GRAPHITE_DEEP", "SHEET_TRACE"),
    "SHEET_QUIET": ("SHEET", "SHEET_2"),
    "SHEET_TRACE": ("SHEET", "SHEET_2"),
    "SHEET_FLAG_INK": ("SHEET", "SHEET_2", "SHEET_FLAG_WELL"),
    "SHEET_AMBER_INK": ("SHEET", "SHEET_2", "SHEET_AMBER_WELL"),
    "SHEET_SUCCESS_INK": ("SHEET", "SHEET_2", "SHEET_SUCCESS_WELL"),
}

EDGE_ON: dict[str, tuple[str, ...]] = {
    "EDGE": ("DASH", "INK", "INK_2"),
    "TRACE": ("DASH", "INK", "INK_2"),
    "FLAG": ("DASH", "INK", "INK_2"),
    "SUCCESS": ("DASH", "INK", "INK_2", "SHEET", "SHEET_2"),
    "SHEET_EDGE": ("SHEET", "SHEET_2"),
    "STRAVA": ("SHEET", "SHEET_2"),
}

RULE_ONLY: dict[str, str] = {
    "RULE": (
        "COROS's own border ivory, the storefront's only warmth, and the premise of "
        "Brújula's entire paper register. On the instrument it is the loudest value in "
        "the file: RULE on DASH 16.20:1 and RULE on INK 14.51:1, so a hairline drawn in "
        "it reads as a highlight. Huella's hairlines are GRID; a boundary somebody has "
        "to see is EDGE."
    ),
    "AXIS": (
        "the chart's gridlines and the baseline under a sparkline. AXIS on INK 1.78:1 and "
        "AXIS on DASH 1.99:1 — deliberately under every floor, because a gridline that "
        "competes with the series is a gridline that hides it. Nothing that carries a "
        "value is drawn in this: a figure is READOUT, a label is SUB."
    ),
    "STRAVA": (
        "Strava's own orange, and the only colour here we do not own. It clears the "
        "graphic floor on the surface it is declared on and nothing more, and it may not "
        "be darkened to reach AA, because the brand assets it arrives in may never be "
        "recoloured. So it never carries words: a 'View on Strava' link is set in "
        "SHEET_TRACE, and the mark beside it is the unmodified asset."
    ),
}

# The scan in tests/test_huella_theme.py rejects a saturated red anywhere outside this
# tuple, which is what keeps the flag's colour from being spent on a generic error.
UNCERTAINTY: tuple[str, ...] = (
    "FLAG",
    "FLAG_INK",
    "FLAG_WELL",
    "SHEET_FLAG_INK",
    "SHEET_FLAG_WELL",
)

# ── type ──────────────────────────────────────────────────────────────────────
# COROS sets their storefront in PF Din Text Pro, self-hosted from their Shopify CDN as
# SF-Heading-font/SF-Body-font. It is a licensed Parachute face and we cannot ship it, so
# Barlow — the same DIN-derived skeleton, on Google Fonts — stands in for the interface.
# There is no second family for display. Brújula's Fraunces is its editorial voice and an
# instrument does not have one: a serif over a table of splits is a magazine pretending to
# be a dashboard. FONT_DISPLAY is deliberately the same stack, and weight and tracking do
# the work a second family would otherwise do.
FONT = "Barlow, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
FONT_DISPLAY = FONT
# The figures are set in the mono, and that is the instrument argument rather than a
# stylistic one: a monospace is tabular by construction, so a column of paces and
# distances aligns without depending on a proportional family shipping `tnum`. Nothing
# here claims Barlow has tabular figures — it was not verified.
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
# One request for both families, verified 30 Jul 2026 to return all six weights and
# latin-ext subsets — Spanish is the whole interface, and a family without the accents is
# a family that renders "Huella" beside "sesion".
FONT_HREF = (
    "https://fonts.googleapis.com/css2"
    "?family=Barlow:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500"
    "&display=swap"
)

TRACK_TIGHT = "-0.01em"
TRACK_DISPLAY = "-0.02em"
TRACK_EYEBROW = "0.16em"
# A figure never gets negative tracking. Tightening a monospace column undoes the only
# reason it is a monospace.
TRACK_READOUT = "0"

# ── geometry and depth ────────────────────────────────────────────────────────
# Tighter than Brújula's. A row in a table is not a card, and a 10px corner on a 28px row
# is a pill pretending to be data.
RADIUS = "6px"
RADIUS_SM = "3px"
RADIUS_LG = "10px"
RADIUS_PILL = "999px"

# Shadows belong to the sheets. A drop shadow on DASH is a darker black on a near-black —
# on the instrument, elevation is INK_2. These are cast in the instrument's own blue-black
# so a modal over it does not fringe warm.
SHADOW_SM = "0 1px 2px rgba(6,10,14,0.28)"
SHADOW_MD = "0 2px 4px rgba(6,10,14,0.30), 0 10px 24px rgba(6,10,14,0.34)"
SHADOW_LG = "0 4px 10px rgba(6,10,14,0.34), 0 24px 56px rgba(6,10,14,0.44)"

# The ring is the halo; EDGE is the visible boundary inside it on the instrument and
# SHEET_EDGE on a sheet, which is why both are EDGE_ON colours.
FOCUS_RING = "0 0 0 3px rgba(79,209,224,0.34)"
FOCUS_RING_SHEET = "0 0 0 3px rgba(12,110,128,0.28)"

EASE = "cubic-bezier(0.22,0.61,0.36,1)"
EASE_EXPO = "cubic-bezier(0.16,1,0.3,1)"

# Wider than Brújula's, and a wider rail: the content is a table of weeks, not a column of
# prose, and the rail carries provenance beside every parsed figure.
CONTENT_W = "76rem"
RAIL_W = "26rem"
# The one number that is a layout claim rather than a taste: a row has to stay a row at
# 414px, so the readout column is capped and the label column is what truncates.
ROW_H = "2.25rem"

# ── what rx.theme() is handed ─────────────────────────────────────────────────
# Radix's own scales still draw the components' insides, so the two have to agree: cyan
# and slate are the closest Radix pair to the trace-on-near-black above, and 95% scaling
# is the data density. Every value verified against reflex_components_radix's own Literal
# types. rxconfig.py cannot read this file — get_config() imports it with sys.path cut to
# its own directory — so these live here and app.py passes them to rx.theme().
APPEARANCE = "dark"
RADIX_ACCENT = "cyan"
RADIX_GRAY = "slate"
RADIX_RADIUS = "small"
RADIX_SCALING = "95%"
