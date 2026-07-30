"""The one door. A password screen that says what is behind it and why it is locked.

The gate is the first thing anybody sees, so it carries the full lockup — dial, wordmark and
tagline — and states the reason for the lock rather than presenting a bare field: what is
behind it spends real requests against COROS's live storefront and a real model quota.

**The animated class sits on the box, not on the form.** `rx.form` is a Radix primitive and
its render does `self.class_name or ""`, which raises on a Var. The entrance and the refusal
shake are the same prop on the same wrapper, chosen by whether there is an error to report.

The reveal toggle is `type="button"`. Inside a form, a button without it submits.

The field's own well is `PAPER_DEEP` and not the card it sits on: a field the same colour as
its card is a rectangle you have to find by the border alone.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import State
from brujula.ui import brand
from brujula.ui.theme import (
    BRASS,
    CARD,
    DANGER,
    DANGER_INK,
    EDGE,
    FOCUS_RING,
    GRAPHITE,
    GRAPHITE_DEEP,
    INK,
    ON_DARK,
    PAPER,
    PAPER_DEEP,
    QUIET,
    RADIUS,
    RADIUS_LG,
    SHADOW_LG,
)

_BLURB = (
    "Brújula consulta el catálogo real de COROS Colombia y gasta cuota de modelo en cada "
    "respuesta, así que corre detrás de una contraseña. Escríbela para entrar."
)
_FOOTER = "Lee el catálogo de COROS Colombia en vivo. No compra nada por ti."


def _field() -> rx.Component:
    return rx.hstack(
        rx.icon("lock_keyhole", size=17, color=QUIET, flex_shrink="0"),
        rx.input(
            name="password",
            type=rx.cond(State.gate_reveal, "text", "password"),
            placeholder="Contraseña",
            aria_label="Contraseña",
            auto_focus=True,
            disabled=State.gate_busy,
            size="3",
            width="100%",
            color=INK,
            # The well draws the field. Radix's own inset ring inside it reads as a second
            # border, and its background covers the well.
            background="transparent",
            box_shadow="none",
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
            color=QUIET,
            _hover={"color": INK},
        ),
        spacing="2",
        width="100%",
        align="center",
        padding="0.35rem 0.4rem 0.35rem 0.75rem",
        background=PAPER_DEEP,
        # The whole declaration per branch: an f-string over a Var stringifies the Var.
        border=rx.cond(State.gate_error != "", f"1px solid {DANGER}", f"1px solid {EDGE}"),
        border_radius=RADIUS,
        _focus_within={"border_color": BRASS, "box_shadow": FOCUS_RING},
    )


def _error() -> rx.Component:
    return rx.cond(
        State.gate_error != "",
        rx.hstack(
            rx.icon("circle_alert", size=15, color=DANGER_INK, flex_shrink="0"),
            rx.text(State.gate_error, color=DANGER_INK, size="2", weight="medium"),
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
        color=ON_DARK,
        background=GRAPHITE,
        _hover={"background": GRAPHITE_DEEP},
    )


def _form() -> rx.Component:
    return rx.form(
        rx.vstack(
            brand.mark(size="3rem", surface=CARD),
            rx.vstack(
                brand.wordmark(size="2rem"),
                brand.tagline(),
                spacing="2",
                align="center",
            ),
            rx.text(
                _BLURB,
                color=QUIET,
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
        background=CARD,
        border=f"1px solid {EDGE}",
        # The card's own brass rule, rather than a brass wash behind it. A gradient is not a
        # surface anything was measured against, and the footer below this card would end up
        # sitting on one.
        border_top=f"4px solid {BRASS}",
        border_radius=RADIUS_LG,
        box_shadow=SHADOW_LG,
    )


def screen() -> rx.Component:
    return rx.center(
        rx.vstack(
            # The class is a Var, so it cannot go on the form itself — see the docstring.
            rx.box(
                _form(),
                class_name=rx.cond(State.gate_error != "", "bj-shake", "bj-rise"),
                width="100%",
            ),
            rx.text(_FOOTER, color=QUIET, size="1", line_height="1.6", text_align="center"),
            spacing="4",
            align="center",
            width="100%",
            max_width="27rem",
        ),
        width="100%",
        min_height="100vh",
        padding="1.25rem",
        background=f"linear-gradient(180deg, {PAPER} 0%, {PAPER_DEEP} 100%)",
        background_attachment="fixed",
    )
