"""Huella's identity: an instrument face with a series plotted on it, one word, and a dot
that cannot lie.

Brújula's mark is a dial that has taken a bearing. Huella's is deliberately not one — its
mark is the trace, and `tests/test_huella_app.py` says so in those words. Huella points
nowhere: it reads what somebody already did. So the mark is a plate with five readings
plotted across it, drawn here rather than borrowed from an icon set. The plate is INK_2, the
elevation step the theme already reserves for a well; an L of axis sits under the series at
`AXIS on INK_2 1.57:1`, deliberately below every floor, which is the job `theme.RULE_ONLY`
gives that token; and the series is the accent at `TRACE on INK_2 8.18:1`. The plate's
hairline is measured against what is BEHIND the mark rather than the fill it encloses —
`EDGE on INK 4.03:1` at the default, with `EDGE on DASH 4.50:1` and `EDGE on INK_2 3.54:1`
the other two the registry declares.

**The series falls before it rises, and that is not a doodle.** An ascending zigzag is
Strava's own silhouette, and nothing in this mark may resemble any part of their logo — so
the plot carries a real descent and a near-plateau, sits inside a plate with an axis under
it, and is never drawn in their orange, which the accent is the far side of the wheel from.
A later hand tidying that path into a clean rise is how this regresses.

**The mark is dark-register only and there is no sheet variant.** TRACE and AMBER_INK are
declared on DASH, INK and INK_2 and on neither sheet, so a mark handed `surface=SHEET` would
paint pips nothing measured. The Strava attribution block is a sheet; the lockup does not go
on it.

**Nothing here rotates.** There is no bearing to hold — and had there been one, the
transform would go on a `<g>`: on a shape Reflex renders it into CSS, where a rotate with
three arguments is not valid syntax. The pulse on the presence dot is the app's only ambient
animation, and it is a class rather than a style prop so `assets/huella.css` can swap it for
a still pip under `prefers-reduced-motion`.

**The dot says three things and all three are true.** Idle is green only when there is a key
to answer with: every turn dies at `gemini.client()` without one, and a green dot in front of
that is the one lie this file could tell for free. Working is the accent, pulsing. And the
throttle outranks the turn, exactly as `state.py`'s caption does — `State.throttled` while
`State.is_thinking` shows the degraded pip instead — where that flag covers Strava's
quarter-hour window as well as COROS's latch: either one means what follows came off a
partial read.

**Two pips have to be tellable apart, and only one pair here rests on hue alone.** The live
pip is COROS's own fill green, which is what theme.py says that value is for. `TRACE on
SUCCESS 2.45:1` and `AMBER_INK on SUCCESS 2.64:1` clear the bar Brújula's suite sets on
contrast as well as on hue. Amber and the accent do not — `AMBER_INK on TRACE 1.07:1` — and
they sit 145° apart, which is where theme.py's warning about the vendor's orange has to be
read for what it says: that pair cleared the angle by half a degree, and six times the
separation is not the same claim. Every pip also clears the non-text floor on both surfaces
it straddles, the plate and the surface behind the mark; the tightest is `SUCCESS on INK_2
3.33:1`.

**The uncertainty red is not here and must not arrive.** FLAG_INK says "do not lean on what
is on screen" — a window too thin or too stale to reason from, a check that ran and failed, a
turn that broke — and a key that was never configured is none of the three, because nothing
has been asked yet and nothing has gone wrong; that is the amber's job. A red pip
would say the advice is thin when what it meant was that a key is missing, and
`theme.UNCERTAINTY` exists to keep that colour off everything else.

The mark casts no shadow: on the instrument elevation is a lighter surface, and theme.py's
shadows belong to the sheets. Below about 28px the axis and the head node close up. The
header and the gate both sit well above that, and `dot=False` is for the places that are not
reporting anything.
"""

from __future__ import annotations

import reflex as rx

from coros_core import gemini

from huella.state import State
from huella.ui.theme import (
    AMBER_INK,
    AXIS,
    EDGE,
    FONT,
    FONT_DISPLAY,
    INK,
    INK_2,
    RADIUS_PILL,
    READOUT,
    SUB,
    SUCCESS,
    TRACE,
    TRACK_DISPLAY,
    TRACK_EYEBROW,
)

NAME = "Huella"
# The same words Brújula uses, on purpose: Huella reads training and recommends equipment.
# It does not coach anybody, and a role that said "asesor de entrenamiento" would be the
# claim this app is likeliest to be believed about.
ROLE = "Asesor de equipos"
VENDOR = "COROS"
TAGLINE = "Lo que tu entrenamiento ya demostró"

# Defined in assets/huella.css, which owns the `hu-` namespace and the reduced-motion
# fallback. Keyframes are the one thing a Reflex style prop cannot express.
PULSE_CLASS = "hu-pulse"

DOT_LIVE = SUCCESS
DOT_DEGRADED = AMBER_INK
DOT_THINKING = TRACE
# Read once, at import. Importing `huella.state` above is what guarantees `.env` has already
# been parsed: it calls `gemini.load_env()` before its own module-level env reads.
IDLE_DOT = DOT_LIVE if gemini.api_key() else DOT_DEGRADED

# Five readings, in the same 32-unit box Brújula's dial is drawn in. Down, up, down, up —
# see the docstring for why the descent is load-bearing. The head node is the last of these
# rather than its own pair of numbers, so a redrawn series cannot leave it behind.
_READINGS = (
    ("7.8", "16.6"), ("11.8", "20.4"), ("15.6", "13.4"), ("19.8", "15.8"), ("24.2", "8.8"),
)
_SERIES = "M" + " L".join(f"{x} {y}" for x, y in _READINGS)
_HEAD_X, _HEAD_Y = _READINGS[-1]


def _face() -> rx.Component:
    return rx.el.svg(
        rx.el.rect(
            x="1.6",
            y="1.6",
            width="28.8",
            height="28.8",
            rx="7",
            fill=INK_2,
            stroke=EDGE,
            stroke_width="1.15",
        ),
        rx.el.g(
            rx.el.line(x1="6.4", y1="8.8", x2="6.4", y2="24.2"),
            rx.el.line(x1="6.4", y1="24.2", x2="24.2", y2="24.2"),
            stroke=AXIS,
            stroke_width="1.15",
            stroke_linecap="round",
        ),
        rx.el.path(
            d=_SERIES,
            fill="none",
            stroke=TRACE,
            stroke_width="2.2",
            stroke_linecap="round",
            stroke_linejoin="round",
        ),
        # The newest reading, ringed in the plate's own colour so it reads as a point on the
        # series rather than a thicker end to the stroke.
        rx.el.circle(
            cx=_HEAD_X,
            cy=_HEAD_Y,
            r="2.4",
            fill=TRACE,
            stroke=INK_2,
            stroke_width="1.1",
        ),
        view_box="0 0 32 32",
        width="100%",
        height="100%",
        display="block",
        aria_hidden="true",
    )


def _dot(ink: str, pulse: bool, surface: str) -> rx.Component:
    """An 18% disc inset 5%, which straddles the plate's bottom-right corner at any size.

    The ring is the surface behind the mark reasserted, not an edge drawn against it: at 7px
    the pip has to separate from the plate it overlaps, and the plate's own hairline is
    already doing the other job. That straddle is why every pip colour is measured on INK_2
    as well as on the surface.
    """
    return rx.box(
        position="absolute",
        right="5%",
        bottom="5%",
        width="18%",
        height="18%",
        box_sizing="content-box",
        border_radius=RADIUS_PILL,
        background=ink,
        border=f"2px solid {surface}",
        class_name=PULSE_CLASS if pulse else "",
    )


def mark(size: str = "2.5rem", dot: bool = True, surface: str = INK) -> rx.Component:
    return rx.box(
        _face(),
        rx.cond(
            State.is_thinking,
            # The throttle outranks the turn, the way it does in `state.py`'s caption: being
            # on a partial read — of COROS's catalogue or of Strava's window — is the more
            # important thing to be saying.
            rx.cond(
                State.throttled,
                _dot(DOT_DEGRADED, True, surface),
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
    )


def wordmark(size: str = "1.45rem", as_: str = "h1") -> rx.Component:
    """One word, and no second family to set it in — theme.FONT_DISPLAY is the interface
    stack on purpose, so the weight and the tracking are what make it a wordmark."""
    return rx.heading(
        NAME,
        as_=as_,
        color=READOUT,
        font_family=FONT_DISPLAY,
        font_size=size,
        font_weight="700",
        letter_spacing=TRACK_DISPLAY,
        line_height="1.05",
    )


def tagline(size: str = "0.72rem") -> rx.Component:
    """The claim, in the register that ranks it under the name: `SUB on INK 6.04:1`.

    It is a sentence rather than an eyebrow, so it keeps its case and its accent — "ya
    demostró" is the whole difference between this app and one that asks."""
    return rx.text(
        TAGLINE,
        color=SUB,
        font_family=FONT,
        font_size=size,
        font_weight="500",
        line_height="1.35",
    )


def credit(size: str = "0.62rem") -> rx.Component:
    """What we do, then whose catalogue we read — the seam is the point.

    Brújula sets COROS's name in COROS's own `--color-primary-darker`; here that token is a
    sheet button and nothing else, so the seam is the step from SUB up to READOUT instead.
    The direction is Brújula's: theirs is the strong one, ours is the quiet one.
    """
    return rx.hstack(
        rx.text(ROLE, color=SUB),
        rx.text(VENDOR, color=READOUT, font_weight="700"),
        spacing="1",
        align="center",
        font_family=FONT,
        font_size=size,
        font_weight="600",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )
