"""Brújula's entrypoint: one route, and the gate inside it.

`rxconfig.py`'s `app_module_import` points here, and Reflex requires the module-level `app`.

**The gate is a branch, not a route.** A `/gate` page would be a URL that renders the shell
without the password check ever running: `on_load` is per page and `unlocked` is what the
shell branches on, so a second route is a second door. There is one page, and `index()` is
`rx.cond(State.unlocked, shell, gate)`.

Everything on screen comes off `State` — prices already formatted, requirement and check
names already in Spanish, product fields already rebuilt from the catalogue. Nothing here
reads the model's prose except the chat bubbles, which are the one place it belongs.

This file composes and owns nothing else: the tokens are `ui/theme.py`, the lockup is
`ui/brand.py`, and the five surfaces are `ui/{chat,product,advice,trace_panel,gate}.py`.
`rx.theme` is handed the Radix scales the theme picked, because `rxconfig.py` cannot read that
file — `get_config()` imports it with `sys.path` cut to its own directory.

The `h1` lives in the header, which is mounted for the whole session. The empty state's
heading is an `h2`: it unmounts the moment the first message lands.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import State
from brujula.ui import advice, brand, chat, gate, product, trace_panel
from brujula.ui.theme import (
    APPEARANCE,
    BRASS,
    BRASS_DEEP,
    BRASS_SOFT,
    CARD,
    CONTENT_W,
    DANGER_INK,
    DANGER_SOFT,
    EDGE,
    FONT,
    FONT_HREF,
    GRAPHITE,
    INK,
    PAPER,
    PAPER_DEEP,
    RADIUS,
    RADIUS_SM,
    RADIX_ACCENT,
    RADIX_GRAY,
    RADIX_RADIUS,
    RADIX_SCALING,
)

TITLE = f"{brand.NAME} — {brand.TAGLINE.lower()}"
DESCRIPTION = (
    "Cuéntale para qué entrenas. Brújula lee el catálogo de COROS Colombia, verifica cada "
    "dato contra él y te dice qué comprar — o que no compres nada."
)

MAIN_ID = "brujula-conversacion"
# The header is sticky, so the rail sticks below it and is that much shorter than the
# viewport. One number, read by both, or the rail scrolls its last rows out of reach.
HEADER_H = "4.5rem"


def _error() -> rx.Component:
    return rx.cond(
        State.error != "",
        rx.hstack(
            rx.icon("triangle_alert", size=16, color=DANGER_INK, flex_shrink="0"),
            rx.text(State.error, color=DANGER_INK, size="2", line_height="1.6"),
            role="alert",
            spacing="2",
            align="start",
            width="100%",
            padding="0.7rem 0.85rem",
            background=DANGER_SOFT,
            border_radius=RADIUS_SM,
        ),
    )


def _header() -> rx.Component:
    return rx.hstack(
        brand.mark(size="2.4rem", surface=CARD),
        rx.vstack(
            brand.wordmark(),
            brand.tagline(),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.button(
            rx.icon("scroll_text", size=15),
            rx.text("Registro", size="2", weight="bold", display=["none", "none", "block"]),
            on_click=State.toggle_trace,
            aria_label="Mostrar u ocultar el registro",
            aria_expanded=State.show_trace,
            aria_controls=trace_panel.RAIL_ID,
            size="2",
            cursor="pointer",
            flex_shrink="0",
            color=BRASS_DEEP,
            background=BRASS_SOFT,
            border=f"1px solid {EDGE}",
            _hover={"border_color": BRASS},
        ),
        rx.button(
            rx.icon("rotate_ccw", size=15),
            rx.text(
                "Empezar de nuevo",
                size="2",
                weight="bold",
                display=["none", "none", "block"],
            ),
            on_click=State.clear,
            # The words hide below md, which would leave two unnamed icon buttons side by
            # side on exactly the viewport a phone uses.
            aria_label="Empezar de nuevo",
            size="2",
            cursor="pointer",
            flex_shrink="0",
            color=GRAPHITE,
            background=CARD,
            border=f"1px solid {EDGE}",
            _hover={"border_color": BRASS, "color": INK},
        ),
        role="banner",
        width="100%",
        height=HEADER_H,
        align="center",
        spacing="3",
        padding=["0 1rem", "0 1rem", "0 1.4rem"],
        background=CARD,
        border_bottom=f"1px solid {EDGE}",
        position="sticky",
        top="0",
        z_index="20",
    )


def _skip_link() -> rx.Component:
    """First tab stop. Without it a keyboard user walks the header and, once a kit is up,
    every card link before reaching the composer."""
    return rx.link(
        "Ir a la conversación",
        href=f"#{MAIN_ID}",
        color=INK,
        position="absolute",
        left="-9999px",
        top="0",
        padding="0.5rem 0.75rem",
        background=CARD,
        border=f"1px solid {BRASS}",
        border_radius=RADIUS,
        _focus={"left": "0.75rem", "top": "0.75rem", "z_index": "50"},
    )


def _column() -> rx.Component:
    return rx.vstack(
        _error(),
        chat.transcript(),
        advice.questions(),
        advice.verdict(),
        product.kit(),
        advice.notes(),
        advice.evidence(),
        # Sticky, not last in the column: with a kit on screen the column runs several
        # thousand pixels, and the answer to "¿de qué tamaño es tu caja?" sits below all of
        # it. The fade is the paper's own colour, so the last card slides under it.
        rx.box(
            chat.composer(),
            position="sticky",
            bottom="0",
            z_index="10",
            width="100%",
            padding_top="1rem",
            padding_bottom="0.7rem",
            background=f"linear-gradient(180deg, transparent 0%, {PAPER} 42%)",
        ),
        spacing="4",
        width="100%",
        max_width=CONTENT_W,
        padding=["1rem", "1rem", "1.5rem 1.4rem 1.2rem"],
        align="start",
    )


def _shell() -> rx.Component:
    return rx.box(
        _skip_link(),
        _header(),
        rx.flex(
            rx.flex(
                _column(),
                id=MAIN_ID,
                role="main",
                flex="1",
                # Without this the column refuses to shrink and the rail is pushed off the
                # viewport instead of sharing it.
                min_width="0",
                width="100%",
                justify="center",
            ),
            trace_panel.panel(top=HEADER_H),
            direction=rx.breakpoints(initial="column", lg="row"),
            width="100%",
            align="start",
            gap="0",
        ),
        width="100%",
        min_height="100vh",
        background=f"linear-gradient(180deg, {PAPER} 0, {PAPER_DEEP} 340px)",
        background_attachment="fixed",
    )


def index() -> rx.Component:
    return rx.cond(State.unlocked, _shell(), gate.screen())


app = rx.App(
    theme=rx.theme(
        appearance=APPEARANCE,
        accent_color=RADIX_ACCENT,
        gray_color=RADIX_GRAY,
        radius=RADIX_RADIUS,
        scaling=RADIX_SCALING,
        # Radix sets font-family on `.radix-themes` itself, which outranks a body-level
        # App(style=...). Setting it here is what actually applies Barlow.
        font_family=FONT,
    ),
    stylesheets=[FONT_HREF],
    style={"background": PAPER, "color": INK, "font_family": FONT},
)
app.add_page(
    index,
    route="/",
    title=TITLE,
    description=DESCRIPTION,
    on_load=State.on_page_load,
)
