"""Brújula's design tokens: COROS's own values, in Brújula's own register.

The seven anchors below are read off coros.com.co's inline theme stylesheet, not picked
off a screenshot: six are CSS custom properties, and the seventh — the red — is a literal
repeated across their own section styles. What sits on top of them is ours.

COROS's storefront is white, black and grey, and its warmth is a single deliberate
choice: `--color-border: #EEEEE0`, a warm ivory hairline on an otherwise neutral page.
Brújula takes that one value as its premise. The page is paper, the type is COROS's own
near-black, and the accent is brass — a compass needle's metal, warm enough to belong on
ivory and quiet enough that nothing competes with the products.

**Red is the refusal, and that is an inversion of COROS's own usage.** `#ea2e41` is what
their storefront spends on buy buttons (`.buy-btn`, `.button-primary`, section headings).
Here it never sells anything: it is reserved for "COROS Colombia no vende eso" and "no
compres nada", which are Brújula's hardest and most valuable sentences. Spending it on a
call to action would make the honest refusal arrive in the same colour as a purchase. It
is also not a colour for words at any size on our surfaces — ON_DARK on DANGER 4.23:1 is
what their own white-on-red button measures — so DANGER_INK carries the sentences and
DANGER carries the icon and the rule.

**COROS's `--color-warning: #ff706b` is deliberately not used.** It is another red, and a
rate limit is not a refusal; the amber below keeps those two answers apart.

**COROS's own `--color-success: #3a8735` cannot carry words here.** SUCCESS on CARD
4.47:1 lands three hundredths under the AA floor, so it stays the tick and the fill and
SUCCESS_INK says "en stock" in words.

DecaBot's indigo appears nowhere. Two demos on one VPS that share a palette read as one
demo with two front doors.

Every colour is classified by SURFACES, TYPE_ON, EDGE_ON or RULE_ONLY — a colour may be
both a surface and a type colour, never both a type colour and RULE_ONLY — and every pair
TYPE_ON or EDGE_ON names carries its measured ratio in the comment above it.
`tests/test_brujula_theme.py` recomputes each of those figures from the two colours it
names: a ratio here is a measurement, never a memory.
"""

from __future__ import annotations

# ── COROS's published values ──────────────────────────────────────────────────
# coros.com.co, inline theme stylesheet. Left column is their variable name.
INK = "#161d25"  # --color-body-text
GRAPHITE = "#404040"  # --color-primary, and their own button fill
GRAPHITE_DEEP = "#212121"  # --color-primary-darker
SUB = "#9A9A9A"  # --color-sub-text
RULE = "#EEEEE0"  # --color-border
SUCCESS = "#3a8735"  # --color-success
DANGER = "#ea2e41"  # their CTA red, reserved here for the refusal

# ── Brújula's paper ───────────────────────────────────────────────────────────
# PAPER and PAPER_DEEP are RULE's warmth carried up into the page; CARD is COROS's own
# --color-main-background, so a product sits on the same white their storefront uses.
#   INK on PAPER 15.98:1     INK on PAPER_DEEP 14.50:1     INK on CARD 16.98:1
#   GRAPHITE on PAPER 9.76:1     GRAPHITE on PAPER_DEEP 8.85:1     GRAPHITE on CARD 10.37:1
#   GRAPHITE_DEEP on PAPER 15.15:1     GRAPHITE_DEEP on CARD 16.10:1
#   QUIET on PAPER 5.38:1     QUIET on PAPER_DEEP 4.89:1     QUIET on CARD 5.72:1
PAPER = "#FAF8F1"
PAPER_DEEP = "#F1EDE0"
CARD = "#FFFFFF"
# The one muted type colour. SUB is the obvious reach for it and cannot do the job.
QUIET = "#6B6659"
# A boundary somebody has to see: the composer's edge, a focused field, a card that has
# to separate from the paper it sits on.
#   EDGE on PAPER 3.47:1     EDGE on CARD 3.68:1
EDGE = "#89857A"

# ── brass: the accent, and the needle ─────────────────────────────────────────
#   BRASS on PAPER 5.55:1     BRASS on CARD 5.90:1     BRASS on BRASS_SOFT 5.07:1
#   BRASS_DEEP on PAPER 9.82:1     BRASS_DEEP on CARD 10.44:1     BRASS_DEEP on BRASS_SOFT 8.98:1
#   ON_DARK on GRAPHITE 10.37:1     ON_DARK on GRAPHITE_DEEP 16.10:1     ON_DARK on INK 16.98:1
#   ON_DARK on BRASS 5.90:1     ON_DARK on BRASS_DEEP 10.44:1
BRASS = "#8A5A1E"
BRASS_DEEP = "#5E3612"
BRASS_SOFT = "#F6EDDC"
ON_DARK = "#FFFFFF"

# ── the three answers that are not a recommendation ───────────────────────────
# Refusal, stock and a rate limit each get a tint and an ink, and none of the three
# borrows the other's colour: "we do not sell that", "that is sold out" and "we could not
# read the catalogue" are different sentences and a shopper has to be able to tell.
#   DANGER_INK on PAPER 5.72:1     DANGER_INK on CARD 6.08:1     DANGER_INK on DANGER_SOFT 5.24:1
#   DANGER on PAPER 3.98:1     DANGER on CARD 4.23:1
#   SUCCESS_INK on PAPER 6.00:1     SUCCESS_INK on CARD 6.37:1     SUCCESS_INK on SUCCESS_SOFT 5.50:1
#   SUCCESS on PAPER 4.21:1     SUCCESS on CARD 4.47:1
#   WARN_INK on PAPER 6.51:1     WARN_INK on CARD 6.92:1     WARN_INK on WARN_SOFT 6.03:1
DANGER_INK = "#C2172E"
DANGER_SOFT = "#FCEAEA"
SUCCESS_INK = "#2E6C2A"
SUCCESS_SOFT = "#E8F1E4"
# There is no mid amber: every one bright enough to read as amber failed the non-text floor
# as a glyph on paper. The throttle notice is WARN_SOFT with WARN_INK on it — dot and words.
WARN_INK = "#7A5205"
WARN_SOFT = "#FBEED6"

# ── the audit rail: a second register ─────────────────────────────────────────
# The rail is Brújula's instrument, not COROS's chrome, so it inverts to a warm ink slab
# — paper's negative rather than a neutral black. NONE of the tokens above transfer, and
# the two that come closest are still invisible there: BRASS on RAIL_BG 3.10:1,
# DANGER_INK on RAIL_BG 3.00:1. Secondary type is tinted from the slab's own hue, because
# a neutral grey on warm black reads as dust.
#   RAIL_INK on RAIL_BG 15.73:1     RAIL_INK on RAIL_BG_2 14.35:1
#   RAIL_MUTED on RAIL_BG 8.57:1     RAIL_MUTED on RAIL_BG_2 7.82:1
#   RAIL_BRASS on RAIL_BG 9.54:1     RAIL_BRASS on RAIL_BG_2 8.70:1
#   RAIL_DANGER on RAIL_BG 9.09:1     RAIL_DANGER on RAIL_BG_2 8.30:1
#   RAIL_SUCCESS on RAIL_BG 10.02:1     RAIL_SUCCESS on RAIL_BG_2 9.15:1
RAIL_BG = "#17150F"
RAIL_BG_2 = "#211E15"
RAIL_INK = "#F2EEE2"
RAIL_MUTED = "#B9B1A0"
RAIL_BRASS = "#E3B463"
RAIL_DANGER = "#FF9BA2"
RAIL_SUCCESS = "#8CD08F"
RAIL_LINE = "rgba(242,238,226,0.10)"

# ── what the rail and the checklist render ────────────────────────────────────
# Keyed by `coros_core.trace.Level` and `coros_core.evidence.Outcome`; a level or an
# outcome with no colour is a row that renders in whatever the previous one left behind.
LEVEL_COLOR: dict[str, str] = {"info": RAIL_MUTED, "guardrail": RAIL_BRASS, "error": RAIL_DANGER}
LEVEL_BG: dict[str, str] = {
    "info": "transparent",
    "guardrail": "rgba(227,180,99,0.14)",
    "error": "rgba(255,155,162,0.13)",
}
# `not_run` is not `fail` — a check nobody ran is not a check that failed — so it gets the
# muted type colour and a dash, never the red.
OUTCOME_COLOR: dict[str, str] = {"pass": SUCCESS, "fail": DANGER, "not_run": QUIET}

# ── the classification every colour above answers to ──────────────────────────
SURFACES: dict[str, str] = {
    "PAPER": "the page",
    "PAPER_DEEP": "the page's lower gradient stop, and an inset well",
    "CARD": "a product card, a chat bubble, the composer",
    "BRASS_SOFT": "an accent chip, and the person's own bubble",
    "DANGER_SOFT": "the refusal panel",
    "SUCCESS_SOFT": "the in-stock chip",
    "WARN_SOFT": "the notice that says the storefront is rate-limiting us",
    "GRAPHITE": "a primary button",
    "GRAPHITE_DEEP": "a primary button, pressed",
    "INK": "a full-bleed ink band",
    "BRASS": "a brass fill: the mark's needle, a badge",
    "BRASS_DEEP": "a brass fill, pressed",
    "RAIL_BG": "the audit rail",
    "RAIL_BG_2": "one row inside the audit rail",
}

TYPE_ON: dict[str, tuple[str, ...]] = {
    "INK": ("PAPER", "PAPER_DEEP", "CARD"),
    "GRAPHITE": ("PAPER", "PAPER_DEEP", "CARD"),
    "GRAPHITE_DEEP": ("PAPER", "CARD"),
    "QUIET": ("PAPER", "PAPER_DEEP", "CARD"),
    "BRASS": ("PAPER", "CARD", "BRASS_SOFT"),
    "BRASS_DEEP": ("PAPER", "CARD", "BRASS_SOFT"),
    "ON_DARK": ("GRAPHITE", "GRAPHITE_DEEP", "INK", "BRASS", "BRASS_DEEP"),
    "DANGER_INK": ("PAPER", "CARD", "DANGER_SOFT"),
    "SUCCESS_INK": ("PAPER", "CARD", "SUCCESS_SOFT"),
    "WARN_INK": ("PAPER", "CARD", "WARN_SOFT"),
    "RAIL_INK": ("RAIL_BG", "RAIL_BG_2"),
    "RAIL_MUTED": ("RAIL_BG", "RAIL_BG_2"),
    "RAIL_BRASS": ("RAIL_BG", "RAIL_BG_2"),
    "RAIL_DANGER": ("RAIL_BG", "RAIL_BG_2"),
    "RAIL_SUCCESS": ("RAIL_BG", "RAIL_BG_2"),
}

EDGE_ON: dict[str, tuple[str, ...]] = {
    "EDGE": ("PAPER", "CARD"),
    "BRASS": ("PAPER", "CARD"),
    "DANGER": ("PAPER", "CARD"),
    "SUCCESS": ("PAPER", "CARD"),
}

RULE_ONLY: dict[str, str] = {
    "SUB": (
        "COROS's own --color-sub-text and the obvious reach for muted copy. SUB on PAPER "
        "2.65:1 — under AA, and a projector at a demo is worse than any monitor. Inert "
        "glyphs and rules only: never words, and never an icon that carries the answer. "
        "QUIET is the muted type colour."
    ),
    "RULE": (
        "COROS's own --color-border, and the value Brújula's whole paper register is "
        "derived from. RULE on PAPER 1.10:1 — a hairline between rows, decorative by "
        "design. A border somebody has to see is EDGE."
    ),
}

# The scan in tests/test_brujula_theme.py rejects a saturated red anywhere outside this
# tuple, which is what keeps the refusal's colour from being spent on a button.
REFUSAL: tuple[str, ...] = ("DANGER", "DANGER_INK", "DANGER_SOFT", "RAIL_DANGER")

# ── type ──────────────────────────────────────────────────────────────────────
# COROS sets their storefront in PF Din Text Pro, self-hosted from their Shopify CDN as
# SF-Heading-font/SF-Body-font. It is a licensed Parachute face and we cannot ship it, so
# Barlow — the same DIN-derived skeleton, on Google Fonts — stands in for the interface.
# Fraunces is Brújula's own editorial voice and is not COROS's: the wordmark, the headings
# and nothing else. The mono belongs to the audit rail.
FONT_DISPLAY = "Fraunces, 'Iowan Old Style', Georgia, serif"
FONT = "Barlow, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
# One request for all three, verified 30 Jul 2026 to serve latin-ext — Spanish is the
# whole interface, and a family without the accents is a family that renders "Brujula".
FONT_HREF = (
    "https://fonts.googleapis.com/css2"
    "?family=Barlow:wght@400;500;600;700"
    "&family=Fraunces:opsz,wght@9..144,600;9..144,700"
    "&family=JetBrains+Mono:wght@400;500"
    "&display=swap"
)

TRACK_TIGHT = "-0.015em"
TRACK_DISPLAY = "-0.025em"
TRACK_EYEBROW = "0.14em"

# ── geometry and depth ────────────────────────────────────────────────────────
RADIUS = "10px"
RADIUS_SM = "5px"
RADIUS_LG = "16px"
RADIUS_PILL = "999px"

# Shadows are cast in the paper's own warmth. A neutral grey shadow on #FAF8F1 reads as a
# smudge on the page rather than as depth.
SHADOW_XS = "0 1px 2px rgba(58,44,20,0.05)"
SHADOW_SM = "0 1px 2px rgba(58,44,20,0.05), 0 2px 6px rgba(58,44,20,0.05)"
SHADOW_MD = "0 2px 4px rgba(58,44,20,0.05), 0 10px 24px rgba(58,44,20,0.07)"
SHADOW_LG = "0 4px 10px rgba(58,44,20,0.07), 0 20px 46px rgba(58,44,20,0.11)"
SHADOW_BRASS = "0 6px 18px rgba(138,90,30,0.24)"

# The ring is the halo; BRASS is the visible edge inside it, which is why BRASS is an
# EDGE_ON colour as well as a type colour.
FOCUS_RING = "0 0 0 4px rgba(138,90,30,0.18)"

EASE = "cubic-bezier(0.22,0.61,0.36,1)"
EASE_EXPO = "cubic-bezier(0.16,1,0.3,1)"

CONTENT_W = "58rem"
RAIL_W = "24rem"

# ── what rx.theme() is handed ─────────────────────────────────────────────────
# Radix's own scales still draw the components' insides, so the two have to agree: bronze
# and sand are the closest Radix pair to the brass-on-ivory above. rxconfig.py cannot read
# this file — get_config() imports it with sys.path cut to its own directory — so these
# live here and app.py passes them to rx.theme().
APPEARANCE = "light"
RADIX_ACCENT = "bronze"
RADIX_GRAY = "sand"
RADIX_RADIUS = "medium"
RADIX_SCALING = "100%"
