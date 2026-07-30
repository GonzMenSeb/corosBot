"""The Strava connection, and the notices the round trip leaves behind.

**This is the one light surface in the app, and it is a theme rule rather than a taste.**
The official marks carry BLACK text and `theme.EDGE_ON["STRAVA"]` names `SHEET` and nothing
else — on `SHEET_2` the vendor's real orange measures two thousandths under the 3:1 graphic
floor, and the one fix that would rescue it, darkening the asset, is the one Strava's terms
forbid. So the whole block sits on pure white and the instrument's tokens stop at its edge:
`READOUT on SHEET` is 1.12:1, which is a sentence nobody can read.

**The orange rule is drawn INSIDE the sheet.** A border is measured against what is behind
it, and a `border_left` on this panel would have DASH on its outer side — a pair the theme
never measured. The bar beside the eyebrow has SHEET on every side of it, which is the pair
`EDGE_ON` actually declares.

**Both marks are the vendored files, rendered as images.** `assets/strava/` holds them
byte-identical and Reflex serves that directory at the web root, so they arrive as
`/strava/<file>`. Never retyped as inline SVG, never recoloured, never inside anything that
animates, and never the app's own icon — Strava's binding rules, verbatim, and the reason
the connect control is a Radix button stripped of its own surface rather than a button with
the words typed out. The mark stays subordinate to Huella's own name, which lives an `h1`
and a header away from here, and the line under it says out loud what guideline one asks: a
mark near our name may not imply Strava built this.

**The notices stay on the instrument.** Only the marks need a light surface; a turn that
broke has no mark near it, so `State.error` keeps the register the rest of the column is in.
Their red is the pairing `app.py` already shipped — `theme.FLAG_INK on FLAG_WELL`, a
declared pair — and it is the right one: red in this app says "do not lean on what is on
screen", which is exactly what a redirect Strava refused and a turn that broke both say.
The success notice is deliberately not its mirror image: it sits on `INK_2` rather than a
green well, because "conectado" is the ordinary day and a well would announce it.
"""

from __future__ import annotations

import reflex as rx

from huella.state import State
from huella.ui.theme import (
    FLAG_INK,
    FLAG_WELL,
    FOCUS_RING_SHEET,
    GRAPHITE,
    GRAPHITE_DEEP,
    INK,
    INK_2,
    MONO,
    ON_FILL,
    RADIUS,
    RADIUS_PILL,
    RADIUS_SM,
    SHADOW_SM,
    SHEET,
    SHEET_2,
    SHEET_EDGE,
    SHEET_LINE,
    SHEET_QUIET,
    SHEET_TRACE,
    STRAVA,
    SUCCESS_INK,
    TRACK_EYEBROW,
    TRACK_READOUT,
)

CONNECT_MARK = "/strava/btn_strava_connect_with_orange.svg"
POWERED_MARK = "/strava/api_logo_pwrdBy_strava_horiz_orange.svg"

CONNECT_LABEL = "Conectar con Strava"
POWERED_ALT = "Con tecnología de Strava"

_PAD_X = "1.05rem"

_CONNECTED = "Cuenta conectada. Leo tus actividades, nunca tus datos personales."
_DISCONNECTED = (
    "Sin Strava conectado. Huella funciona igual — te pregunta en vez de deducir, y lo dice."
)
_UNCONFIGURED = "Esta instancia no tiene credenciales de Strava configuradas."
_NOT_SPONSORED = "Huella no está desarrollada ni patrocinada por Strava."
_SCOPES_LABEL = "permisos que diste"


def _eyebrow(label: str) -> rx.Component:
    return rx.text(
        label,
        color=SHEET_QUIET,
        font_size="0.62rem",
        font_weight="700",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )


def _rule() -> rx.Component:
    return rx.box(
        width="1.6rem",
        height="3px",
        flex_shrink="0",
        background=STRAVA,
        border_radius=RADIUS_PILL,
    )


def _readout(label: str, value: rx.Var) -> rx.Component:
    """A label and a figure. The figure is set in the mono at zero tracking because it is a
    readout — tightening a monospace undoes the only reason it is one."""
    return rx.hstack(
        rx.text(label, color=SHEET_QUIET, size="1", flex_shrink="0"),
        rx.text(
            value,
            color=INK,
            size="1",
            font_family=MONO,
            letter_spacing=TRACK_READOUT,
            word_break="break-all",
        ),
        spacing="2",
        align="center",
        width="100%",
    )


def _scope(scope: rx.Var[str]) -> rx.Component:
    return rx.text(
        scope,
        color=SHEET_QUIET,
        size="1",
        font_family=MONO,
        letter_spacing=TRACK_READOUT,
        line_height="1.2",
        padding="0.2rem 0.5rem",
        background=SHEET_2,
        border_radius=RADIUS_PILL,
    )


def _scopes() -> rx.Component:
    """Strava's own scope names, verbatim. They are what the athlete granted and what the
    client asks for; translating them would name a permission Strava does not have."""
    return rx.cond(
        State.scopes.length() > 0,
        rx.vstack(
            rx.text(_SCOPES_LABEL, color=SHEET_QUIET, size="1"),
            rx.flex(
                rx.foreach(State.scopes, _scope),
                wrap="wrap",
                gap="0.3rem",
                width="100%",
            ),
            spacing="1",
            align="start",
            width="100%",
        ),
    )


def _connected() -> rx.Component:
    return rx.vstack(
        rx.text(_CONNECTED, color=INK, size="2", line_height="1.6"),
        _readout("huella del par", State.token_fingerprint),
        rx.cond(State.rotations > 0, _readout("renovaciones del par", State.rotations)),
        _scopes(),
        rx.hstack(
            rx.button(
                "Desconectar",
                on_click=State.disconnect_strava,
                size="2",
                cursor="pointer",
                color=ON_FILL,
                background=GRAPHITE,
                _hover={"background": GRAPHITE_DEEP},
                _focus_visible={"box_shadow": FOCUS_RING_SHEET},
            ),
            rx.button(
                "Borrar todo lo mío",
                on_click=State.forget_me,
                size="2",
                cursor="pointer",
                color=SHEET_TRACE,
                background=SHEET,
                border=f"1px solid {SHEET_EDGE}",
                _hover={"background": SHEET_2},
                _focus_visible={"box_shadow": FOCUS_RING_SHEET},
            ),
            spacing="2",
            wrap="wrap",
            width="100%",
        ),
        spacing="3",
        align="start",
        width="100%",
    )


def _connect_button() -> rx.Component:
    """Strava's own asset, inside a control stripped of everything Radix would draw on it.

    The mark may not be modified, so the button contributes no surface, no radius over it
    and no hover of any kind — only the pointer and, because the ring was stripped with the
    rest, a focus ring measured for a sheet.
    """
    return rx.button(
        rx.image(
            src=CONNECT_MARK,
            alt=CONNECT_LABEL,
            width="237px",
            height="auto",
            max_width="100%",
            display="block",
        ),
        on_click=State.connect_strava,
        aria_label=CONNECT_LABEL,
        type="button",
        cursor="pointer",
        height="auto",
        padding="0",
        background="transparent",
        box_shadow="none",
        # Radix's own variant paints a fill on hover and on press. Behind an asset that may
        # not be recoloured, that is the vendor's mark on a surface nothing measured.
        _hover={"background": "transparent"},
        _active={"background": "transparent"},
        _focus_visible={"box_shadow": FOCUS_RING_SHEET},
    )


def _disconnected() -> rx.Component:
    return rx.vstack(
        rx.text(_DISCONNECTED, color=INK, size="2", line_height="1.6"),
        rx.cond(
            State.strava_configured,
            _connect_button(),
            # An instance with no credentials must not offer a button that cannot do
            # anything; the sentence is what it offers instead.
            rx.text(_UNCONFIGURED, color=SHEET_QUIET, size="1", line_height="1.5"),
        ),
        spacing="3",
        align="start",
        width="100%",
    )


def attribution() -> rx.Component:
    """"Powered by Strava", unmodified, and carrying its own sheet so it is legible wherever
    it is placed. Small on purpose: it may appear near Huella's name and never above it."""
    return rx.vstack(
        rx.image(
            src=POWERED_MARK,
            alt=POWERED_ALT,
            width="11rem",
            height="auto",
            max_width="100%",
            display="block",
        ),
        rx.text(_NOT_SPONSORED, color=SHEET_QUIET, size="1", line_height="1.5"),
        spacing="2",
        align="start",
        width="100%",
        padding=f"0.75rem {_PAD_X} 0.85rem",
        background=SHEET,
        border_top=f"1px solid {SHEET_LINE}",
    )


def panel() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            rx.hstack(
                _rule(),
                _eyebrow("Strava"),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.cond(State.connected, _connected(), _disconnected()),
            spacing="3",
            align="start",
            width="100%",
            padding=f"0.95rem {_PAD_X} 0.9rem",
        ),
        attribution(),
        spacing="0",
        align="start",
        width="100%",
        background=SHEET,
        border_radius=RADIUS,
        box_shadow=SHADOW_SM,
        overflow="hidden",
    )


def _notice(
    message: rx.Var[str],
    *,
    icon: str,
    ink: str,
    well: str,
    role: str,
    live: str,
) -> rx.Component:
    return rx.cond(
        message != "",
        rx.hstack(
            rx.icon(icon, size=16, color=ink, flex_shrink="0"),
            rx.text(message, color=ink, size="2", line_height="1.6"),
            role=role,
            # The region is on screen only while it has something to say, so without an
            # explicit live value the whole announcement depends on the role's default
            # being read by a reader that only just mounted it.
            aria_live=live,
            spacing="2",
            align="start",
            width="100%",
            padding="0.7rem 0.85rem",
            background=well,
            border_radius=RADIUS_SM,
        ),
    )


def notices() -> rx.Component:
    """The three sentences the connection can leave behind: what Strava's redirect answered,
    what a disconnection did, and what a turn broke on. A fragment, not a stack — an empty
    stack is a gap in the column on the ordinary day when there is nothing to say."""
    return rx.fragment(
        _notice(
            State.strava_error,
            icon="triangle_alert",
            ink=FLAG_INK,
            well=FLAG_WELL,
            role="alert",
            live="assertive",
        ),
        _notice(
            State.strava_notice,
            icon="circle_check",
            ink=SUCCESS_INK,
            well=INK_2,
            role="status",
            live="polite",
        ),
        _notice(
            State.error,
            icon="triangle_alert",
            ink=FLAG_INK,
            well=FLAG_WELL,
            role="alert",
            live="assertive",
        ),
    )
