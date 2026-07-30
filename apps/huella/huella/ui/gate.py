"""The one door. A password screen that says what is behind it and why it is locked.

The gate is the first thing anybody sees, so it carries the full lockup — mark, wordmark,
credit and tagline — and states the reason for the lock rather than presenting a bare
field: what is behind it reads a real training history and spends real model quota.

**The refusal is `hu-shake`, and this module is what makes that rule reachable.**
`assets/huella.css` has carried the keyframes and their reduced-motion alternative — a held
FLAG outline — since the stylesheet was written, under a comment naming this screen ("the
gate card is the whole page, so the refusal has to be felt"). Nothing applied it, so
`tests/test_huella_ui.py` checked a pair of rules no browser ever reached. It is the only
*animated* class here: the kit's arrival stays the app's one entrance, so the card does not
rise, and `hu-rise` is deliberately not a thing this module invents.

**The animated class sits on a wrapper box, not on the form.** `rx.form` is a Radix
primitive and its render does `self.class_name or ""`, which raises on a Var.

The reveal toggle is `type="button"`. Inside a form, a button without it submits.

The field's own well is INK_2 and not the card it sits on: a field the same colour as its
card is a rectangle you have to find by the border alone. The well carries `hu-dock` — the
stylesheet's rule for a wrapper that draws its own field — because a style prop cannot do
that job here: Reflex puts props on Radix's TextField Root, and both the ring to strip and
the `:focus-visible` outline to strip are on the `<input>` inside it.

No Strava mark on this screen. `brand.py` is explicit that the lockup is dark-register only
and that the vendor's marks live on the sheet `connect.attribution()` owns; the footer here
is prose.
"""

from __future__ import annotations

import reflex as rx

from huella.state import State
from huella.ui import brand
from huella.ui.theme import (
    DASH,
    EDGE,
    FLAG,
    FLAG_INK,
    GRID,
    INK,
    INK_2,
    RADIUS,
    RADIUS_LG,
    READOUT,
    SHADOW_MD,
    SUB,
    TRACE,
    TRACE_DEEP,
)

SHAKE_CLASS = "hu-shake"
DOCK_CLASS = "hu-dock"

_BLURB = (
    "Huella lee tu historial de entrenamiento y gasta cuota de modelo en cada respuesta, "
    "así que corre detrás de una contraseña. Escríbela para entrar."
)
_FOOTER = "Lee lo que ya entrenaste. No compra nada por ti."


def _field() -> rx.Component:
    return rx.hstack(
        rx.icon("lock_keyhole", size=17, color=SUB, flex_shrink="0"),
        rx.input(
            name="password",
            type=rx.cond(State.gate_reveal, "text", "password"),
            placeholder="Contraseña",
            aria_label="Contraseña",
            auto_focus=True,
            disabled=State.gate_busy,
            size="3",
            width="100%",
            color=READOUT,
        ),
        rx.button(
            rx.icon(rx.cond(State.gate_reveal, "eye_off", "eye"), size=16),
            on_click=State.toggle_reveal,
            type="button",
            aria_label="Mostrar u ocultar la contraseña",
            variant="ghost",
            size="2",
            cursor="pointer",
            flex_shrink="0",
            color=SUB,
            _hover={"color": READOUT},
        ),
        # `hu-dock` is the rule for a wrapper that draws the field, and this is the app's
        # second one. Without it Radix draws its own ring and background inside the well, and
        # the global `:focus-visible` adds a third ring on the input the moment `auto_focus`
        # lands — none of which a style prop can reach, because Reflex puts props on the
        # TextField Root and the outline is on the `<input>` inside it.
        class_name=DOCK_CLASS,
        spacing="2",
        width="100%",
        align="center",
        padding="0.35rem 0.4rem 0.35rem 0.75rem",
        background=INK_2,
        # The whole declaration per branch: an f-string over a Var stringifies the Var.
        border=rx.cond(State.gate_error != "", f"1px solid {FLAG}", f"1px solid {EDGE}"),
        border_radius=RADIUS,
        # Only the border here. `.hu-dock:focus-within` already owns the ring, and a style
        # prop compiles to a class of the same specificity — the two would race.
        _focus_within={"border_color": TRACE},
    )


def _error() -> rx.Component:
    return rx.cond(
        State.gate_error != "",
        rx.hstack(
            rx.icon("circle_alert", size=15, color=FLAG_INK, flex_shrink="0"),
            rx.text(State.gate_error, color=FLAG_INK, size="2", weight="medium"),
            role="alert",
            spacing="2",
            align="center",
            width="100%",
        ),
    )


def _submit() -> rx.Component:
    return rx.button(
        rx.cond(
            State.gate_busy,
            rx.hstack(
                rx.spinner(size="2"),
                rx.text("Comprobando…", size="2", weight="bold"),
                spacing="2",
                align="center",
            ),
            rx.hstack(
                rx.text("Entrar", size="2", weight="bold"),
                rx.icon("arrow_right", size=16),
                spacing="2",
                align="center",
            ),
        ),
        type="submit",
        size="3",
        disabled=State.gate_busy,
        cursor="pointer",
        width="100%",
        color=INK,
        background=TRACE,
        _hover={"background": TRACE_DEEP},
    )


def _form() -> rx.Component:
    return rx.form(
        rx.vstack(
            brand.mark(size="3rem", surface=INK),
            rx.vstack(
                brand.wordmark(size="2rem"),
                brand.credit(),
                spacing="2",
                align="center",
            ),
            brand.tagline(size="0.85rem"),
            rx.text(
                _BLURB,
                color=SUB,
                size="2",
                line_height="1.7",
                text_align="center",
                max_width="36ch",
            ),
            rx.vstack(_field(), _error(), _submit(), spacing="3", width="100%"),
            spacing="5",
            align="center",
            width="100%",
        ),
        on_submit=State.unlock,
        reset_on_submit=True,
        width="100%",
        padding=["1.75rem 1.35rem", "2.25rem 2rem", "2.5rem 2.25rem"],
        background=INK,
        border=f"1px solid {GRID}",
        # The card's own rule in the instrument's accent, rather than a wash behind it.
        border_top=f"4px solid {TRACE}",
        border_radius=RADIUS_LG,
        box_shadow=SHADOW_MD,
    )


def screen() -> rx.Component:
    return rx.center(
        rx.vstack(
            # The class is a Var, so it cannot go on the form itself — see the docstring.
            rx.box(
                _form(),
                class_name=rx.cond(State.gate_error != "", SHAKE_CLASS, ""),
                width="100%",
            ),
            rx.text(_FOOTER, color=SUB, size="1", line_height="1.6", text_align="center"),
            spacing="4",
            align="center",
            width="100%",
            max_width="27rem",
        ),
        width="100%",
        min_height="100vh",
        padding="1.25rem",
        background=DASH,
    )
