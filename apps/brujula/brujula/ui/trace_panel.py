"""The audit rail: Brújula's instrument, in a register nothing else on the page shares.

The rail inverts to the paper's negative — a warm ink slab, not a neutral black — and
**none of the light tokens transfer.** The two that come closest are already invisible
there: `BRASS on RAIL_BG 3.10:1`, `DANGER_INK on RAIL_BG 3.00:1`. So this module imports the
`RAIL_*` set and nothing else that carries a colour, and the suite enforces that by reading
the import list: a light-surface type colour appearing in this file is a build failure, not a
review note.

Everything here is set in the mono face, in English event names, with raw payload lines. That
is deliberate and it is the reason `State.blocking` lands here rather than in the checklist:
the bundle's reasons are its own English — "stock did not run" — and `loop.py` already tells
the person the same thing in Spanish in the transcript. This is the surface where the
engineering vocabulary is at home.

**A verdict is an object on the rail; a routine step is a line on it.** An `info` row gets no
glyph, no label, no fill and a transparent rule; a `guardrail` or `error` row gets all four.
The shape is what separates them at three metres — the hue only confirms it, and colouring
both would make every row the same object in a different tint.

A `border_left` has to be resolved as a whole shorthand, not as a colour interpolated into
one: an f-string over a Var stringifies the Var itself, so `_level_rule` returns the entire
declaration per branch.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import State, TraceRow
from brujula.ui.theme import (
    EASE,
    LEVEL_BG,
    LEVEL_COLOR,
    MONO,
    RADIUS,
    RADIUS_PILL,
    RADIUS_SM,
    RAIL_BG,
    RAIL_BG_2,
    RAIL_BRASS,
    RAIL_DANGER,
    RAIL_INK,
    RAIL_LINE,
    RAIL_MUTED,
    RAIL_W,
    TRACK_EYEBROW,
)

RAIL_ID = "brujula-registro"
RAIL_LABEL = "Registro de auditoría — cada paso, cada consulta al catálogo y cada verdicto"

_EMPTY = (
    "Cada paso del turno queda acá: lo que le pregunté al catálogo, cada comprobación y lo "
    "que no pude verificar."
)
_BLOCKING_NOTE = "Tal como lo reportó el verificador, en sus propias palabras."


def _eyebrow(label: str | rx.Var, color: str) -> rx.Component:
    return rx.text(
        label,
        color=color,
        font_family=MONO,
        font_size="0.6rem",
        font_weight="500",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )


def _level_color(row: TraceRow):
    return rx.match(row.level, *LEVEL_COLOR.items(), RAIL_INK)


def _level_bg(row: TraceRow):
    return rx.match(row.level, *LEVEL_BG.items(), "transparent")


def _level_rule(row: TraceRow):
    return rx.match(
        row.level,
        ("guardrail", f"2px solid {RAIL_BRASS}"),
        ("error", f"2px solid {RAIL_DANGER}"),
        # Transparent rather than absent: the rule must not shift the rows that lack one.
        "2px solid transparent",
    )


def _level_icon(row: TraceRow):
    return rx.match(row.level, ("error", "octagon_alert"), "shield_check")


def row(item: TraceRow) -> rx.Component:
    color = _level_color(item)
    return rx.hstack(
        rx.text(
            item.seq,
            color=RAIL_MUTED,
            font_family=MONO,
            font_size="0.62rem",
            font_variant_numeric="tabular-nums",
            line_height="1.5",
            opacity="0.7",
            text_align="right",
            width="1.5rem",
            flex_shrink="0",
        ),
        rx.vstack(
            rx.hstack(
                rx.cond(
                    item.level != "info",
                    rx.icon(_level_icon(item), size=12, color=color, flex_shrink="0"),
                ),
                rx.text(
                    item.event,
                    color=color,
                    font_family=MONO,
                    font_size="0.68rem",
                    font_weight="500",
                    line_height="1.4",
                    # The rail is 24rem and event names run long. Without this the name
                    # breaks mid-word instead of dropping to its own line.
                    word_break="break-word",
                    min_width="0",
                ),
                rx.spacer(),
                rx.cond(item.level != "info", _eyebrow(item.level.upper(), color)),
                spacing="2",
                align="center",
                wrap="wrap",
                width="100%",
            ),
            rx.cond(
                item.summary != "",
                rx.text(
                    item.summary,
                    color=RAIL_MUTED,
                    font_family=MONO,
                    font_size="0.64rem",
                    line_height="1.55",
                    white_space="pre-wrap",
                    word_break="break-word",
                ),
            ),
            spacing="1",
            align="start",
            min_width="0",
            width="100%",
        ),
        spacing="2",
        align="start",
        width="100%",
        padding="0.45rem 0.55rem",
        background=_level_bg(item),
        border_left=_level_rule(item),
        border_radius=RADIUS_SM,
    )


def blocking() -> rx.Component:
    """What the evidence bundle could not confirm, verbatim.

    This is the one place those strings belong. They are English because the bundle is an
    engineering artifact, and the caption says so rather than leaving a Spanish reader to
    wonder whether the app switched languages on them.
    """
    return rx.cond(
        State.blocking.length() > 0,
        rx.vstack(
            rx.hstack(
                rx.icon("shield_alert", size=13, color=RAIL_DANGER, flex_shrink="0"),
                _eyebrow("No pude confirmar", RAIL_DANGER),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.vstack(
                rx.foreach(
                    State.blocking,
                    lambda reason: rx.text(
                        reason,
                        color=RAIL_INK,
                        font_family=MONO,
                        font_size="0.64rem",
                        line_height="1.55",
                        word_break="break-word",
                    ),
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            rx.text(
                _BLOCKING_NOTE,
                color=RAIL_MUTED,
                font_family=MONO,
                font_size="0.6rem",
                line_height="1.5",
            ),
            spacing="2",
            align="start",
            width="100%",
            margin_bottom="0.6rem",
            padding="0.6rem 0.7rem",
            background=RAIL_BG_2,
            border_radius=RADIUS_SM,
        ),
    )


def body() -> rx.Component:
    return rx.box(
        blocking(),
        rx.cond(
            State.trace.length() > 0,
            rx.vstack(rx.foreach(State.trace, row), spacing="2", width="100%"),
            rx.text(
                _EMPTY,
                color=RAIL_MUTED,
                font_size="0.72rem",
                line_height="1.65",
            ),
        ),
        # A plain overflow box rather than rx.scroll_area: the scrollbar it inherits is a
        # light one, and `bj-rail bj-scroll` is what repaints it for this surface.
        class_name="bj-scroll",
        width="100%",
        flex="1",
        overflow_y="auto",
        padding="0.6rem",
    )


def header() -> rx.Component:
    return rx.hstack(
        rx.icon("scroll_text", size=14, color=RAIL_MUTED, flex_shrink="0"),
        _eyebrow("Registro", RAIL_MUTED),
        rx.text(
            State.trace.length(),
            color=RAIL_BRASS,
            font_family=MONO,
            font_size="0.62rem",
            font_variant_numeric="tabular-nums",
            line_height="1.2",
            padding="0.1rem 0.4rem",
            background=LEVEL_BG["guardrail"],
            border_radius=RADIUS_PILL,
        ),
        rx.spacer(),
        rx.button(
            rx.icon("panel_right_close", size=16),
            on_click=State.toggle_trace,
            aria_label="Cerrar el registro",
            aria_expanded="true",
            aria_controls=RAIL_ID,
            variant="ghost",
            size="2",
            cursor="pointer",
            color=RAIL_MUTED,
            # 44px is the floor for a touch target, and this is the control somebody on a
            # phone reaches for first.
            min_width="44px",
            min_height="44px",
            _hover={"background": RAIL_BG_2, "color": RAIL_INK},
        ),
        spacing="2",
        align="center",
        width="100%",
        flex_shrink="0",
        padding="0.35rem 0.5rem 0.35rem 0.75rem",
        background=RAIL_BG,
        border_bottom=f"1px solid {RAIL_LINE}",
    )


def _collapsed() -> rx.Component:
    return rx.button(
        rx.icon("panel_right_open", size=16),
        rx.text("Registro", size="2", weight="bold"),
        on_click=State.toggle_trace,
        aria_label="Mostrar el registro",
        aria_expanded="false",
        aria_controls=RAIL_ID,
        cursor="pointer",
        flex_shrink="0",
        gap="0.5rem",
        margin=["0 1rem 1rem", "0 1rem 1rem", "1.5rem 1.4rem 0 0"],
        min_height="44px",
        padding="0 0.9rem",
        color=RAIL_INK,
        background=RAIL_BG,
        border_radius=RADIUS_PILL,
        transition=f"background 180ms {EASE}",
        _hover={"background": RAIL_BG_2},
    )


def panel(top: str = "0") -> rx.Component:
    """The rail, or the pill that brings it back.

    `top` is the height of `app.py`'s sticky header: the rail sticks below it and is exactly
    that much shorter than the viewport, or it scrolls its own last rows out of reach.
    """
    return rx.cond(
        State.show_trace,
        rx.vstack(
            header(),
            body(),
            id=RAIL_ID,
            role="complementary",
            aria_label=RAIL_LABEL,
            spacing="0",
            align="stretch",
            flex_shrink="0",
            width=["100%", "100%", "100%", RAIL_W],
            height=["22rem", "22rem", "22rem", f"calc(100vh - {top})"],
            position=["static", "static", "static", "sticky"],
            top=top,
            overflow="hidden",
            background=RAIL_BG,
            # Square once it is flush against the viewport's own edge.
            border_radius=[RADIUS, RADIUS, RADIUS, "0"],
            margin=["0 1rem 1rem", "0 1rem 1rem", "0"],
            class_name="bj-rail",
        ),
        _collapsed(),
    )
