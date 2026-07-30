"""Brújula's identity: a dial that has taken a bearing, one word, and a dot that cannot lie.

The compass is drawn here rather than borrowed from an icon set. A cream dial inside a
hairline somebody can see — `BRASS_SOFT on CARD 1.16:1`, so without `EDGE on BRASS_SOFT
3.17:1` the coin has no silhouette at all — four engraved ticks that are meant to stay quiet
(`SUB on BRASS_SOFT 2.42:1`, which is why they are inert glyphs and never the answer), and
one brass needle at `BRASS on BRASS_SOFT 5.07:1`: `theme.SURFACES` already reserves BRASS
for the mark's needle, and that ratio is what makes it the only thing the eye lands on. The
needle sits off north with an index mark cut into the bezel at the same bearing — an
instrument that took a reading, not an arrow that was handed a direction.

**The needle does not turn.** The halo on the presence dot is the app's only ambient
animation, and it is a class rather than a style prop so `assets/brujula.css` can swap it
for a static ring under `prefers-reduced-motion`.

**The dot's amber is the throttle, not a fixture replay.** DecaBot's dot is green on live
data and amber while replaying a fixture; nothing here reads `fixtures/` at runtime, so that
amber could never light. It says the one thing that genuinely degrades an answer instead:
COROS is rate-limiting us and what follows came off a partial read of the catalogue.
`state.py` clears `throttled` in its `finally`, so the amber only ever appears mid-turn,
beside the halo rather than instead of it.

Idle is green only when there is a key to answer with — every turn dies at
`gemini.client()` without one, and a green dot in front of that is the one lie this file
could tell for free. All three dot colours clear the non-text floor on both surfaces the
mark sits on; the tightest is `SUCCESS on PAPER 4.21:1`.

Below about 28px the ticks and the pivot close up. The header and the gate both sit well
above that, and `dot=False` is for the places that are not reporting anything.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import State
from brujula.ui.theme import (
    BRASS,
    BRASS_SOFT,
    CARD,
    EDGE,
    FONT,
    FONT_DISPLAY,
    GRAPHITE_DEEP,
    INK,
    ON_DARK,
    QUIET,
    RADIUS_PILL,
    SHADOW_XS,
    SUB,
    SUCCESS,
    TRACK_DISPLAY,
    TRACK_EYEBROW,
    WARN_INK,
    WARN_SOFT,
)
from coros_core import gemini

NAME = "Brújula"
ROLE = "Asesor de equipos"
VENDOR = "COROS"
TAGLINE = f"{ROLE} {VENDOR}"

# Defined in assets/brujula.css, which owns the `bj-` namespace and the reduced-motion
# fallback. Keyframes are the one thing a Reflex style prop cannot express.
HALO_CLASS = "bj-halo"

DOT_LIVE = SUCCESS
DOT_DEGRADED = WARN_INK
DOT_DEGRADED_FILL = WARN_SOFT
DOT_THINKING = BRASS
# Read once, at import. Importing `brujula.state` above is what guarantees `.env` has
# already been parsed: it calls `gemini.load_env()` before its own module-level env reads.
IDLE_DOT = DOT_LIVE if gemini.api_key() else DOT_DEGRADED

# Degrees clockwise from north, about the dial's centre. Off north on purpose — see above.
BEARING = 34
_ROTATE = f"rotate({BEARING} 16 16)"

_NEEDLE = "M16 4.2 C17.6 8.9 18.6 12.7 18.7 15.9 L16 17.1 L13.3 15.9 C13.4 12.7 14.4 8.9 16 4.2 Z"
_TAIL = "M16 22.9 L17.2 16.4 L16 15.9 L14.8 16.4 Z"
_TICKS = (
    ("16", "4.5", "16", "6.9"),
    ("16", "27.5", "16", "25.1"),
    ("27.5", "16", "25.1", "16"),
    ("4.5", "16", "6.9", "16"),
)


def _dial() -> rx.Component:
    return rx.el.svg(
        rx.el.circle(cx="16", cy="16", r="14", fill=BRASS_SOFT, stroke=EDGE, stroke_width="1.15"),
        rx.el.g(
            *(rx.el.line(x1=x1, y1=y1, x2=x2, y2=y2) for x1, y1, x2, y2 in _TICKS),
            stroke=SUB,
            stroke_width="1.15",
            stroke_linecap="round",
        ),
        # One group for everything on the bearing, because `transform` is a real attribute on
        # <g> and lands in CSS on the shapes — where `rotate(34 16 16)` is not valid syntax.
        rx.el.g(
            rx.el.path(d=_NEEDLE, fill=BRASS),
            rx.el.path(d=_TAIL, fill=EDGE),
            rx.el.line(
                x1="16",
                y1="1.9",
                x2="16",
                y2="4.6",
                stroke=BRASS,
                stroke_width="1.6",
                stroke_linecap="round",
            ),
            transform=_ROTATE,
        ),
        rx.el.circle(cx="16", cy="16", r="1.6", fill=ON_DARK),
        view_box="0 0 32 32",
        width="100%",
        height="100%",
        display="block",
        aria_hidden="true",
    )


def _dot(ink: str, halo: bool, surface: str, fill: str | None = None) -> rx.Component:
    """An 18% disc inset 8.8%, which puts its centre on the dial's south-east arc at any size.

    The bezel's outer edge is 45.55% of the box from its centre, so the 45° point is 17.79%
    in from two edges and the inset is that minus the disc's own half. The ring is normally
    the surface behind the mark rather than white: on paper a white ring is invisible, and
    the ring is what separates the dot from the bezel it sits on.

    A `fill` makes the pip hollow and spends its ring on the ink instead, which the throttle
    needs: `BRASS vs WARN_INK 1.17:1` — a solid amber dot and a solid brass dot are the same
    dot at 7px, so the amber is theme.py's own throttle notice in miniature, a light chip
    with a dark rim. `WARN_INK on BRASS_SOFT 5.95:1` is what draws that rim over the dial.
    """
    return rx.box(
        position="absolute",
        right="8.8%",
        bottom="8.8%",
        width="18%",
        height="18%",
        box_sizing="content-box",
        border_radius=RADIUS_PILL,
        background=fill or ink,
        border=f"2px solid {ink if fill else surface}",
        class_name=HALO_CLASS if halo else "",
    )


def mark(size: str = "2.5rem", dot: bool = True, surface: str = CARD) -> rx.Component:
    return rx.box(
        _dial(),
        rx.cond(
            State.is_thinking,
            # The throttle outranks the turn, the way it does in `state.py`'s caption: being
            # on a partial read is the more important thing to be saying.
            rx.cond(
                State.throttled,
                _dot(DOT_DEGRADED, True, surface, fill=DOT_DEGRADED_FILL),
                _dot(DOT_THINKING, True, surface),
            ),
            _dot(IDLE_DOT, False, surface),
        )
        if dot
        else rx.fragment(),
        position="relative",
        display="flex",
        flex_shrink="0",
        width=size,
        height=size,
        border_radius=RADIUS_PILL,
        box_shadow=SHADOW_XS,
    )


def wordmark(size: str = "1.45rem", as_: str = "h1") -> rx.Component:
    return rx.heading(
        NAME,
        as_=as_,
        color=INK,
        font_family=FONT_DISPLAY,
        font_size=size,
        font_weight="600",
        letter_spacing=TRACK_DISPLAY,
        line_height="1.05",
    )


def tagline(size: str = "0.66rem") -> rx.Component:
    """What we do, then whose catalogue we read — the seam is the point.

    COROS's name is set in COROS's own `--color-primary-darker` and ours in QUIET, which
    states the relationship in type instead of in a sentence nobody reads twice.
    """
    return rx.hstack(
        rx.text(ROLE, color=QUIET),
        rx.text(VENDOR, color=GRAPHITE_DEEP, font_weight="700"),
        spacing="1",
        align="center",
        font_family=FONT,
        font_size=size,
        font_weight="600",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )
