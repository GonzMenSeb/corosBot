"""The audit rail: every step of the turn, in the vocabulary the turn was written in.

Huella is already dark, so unlike Brújula's rail this is **not** a second register — there
is no inversion left to make. It is the instrument's own palette (`INK` under the column,
`INK_2` for a well inside it, `GRID` for the hairlines) and the separation from the
conversation is done by structure instead: a column of its own, a rule down its edge, and
rows that are records rather than prose. None of the sheets' vocabulary reaches this file.
`SHEET*` and `GRAPHITE*` are measured on white — `READOUT on SHEET` is 1.12:1 — so a light
token here is a build failure, not a review note.

**`State.blocking` is rendered here and nowhere else in the interface.** Those strings are
the evidence bundle's own English — "stock did not run" — an engineering artifact written
for whoever reads a PR, and `loop.py` already tells the athlete the same thing in Spanish in
the transcript. This is the surface that register belongs on: the event names beside them
are English too and the payload lines under them are raw. The caption says as much, so a
Spanish reader is not left wondering whether the app changed languages on them.

**Every level gets a glyph, not only a hue.** `theme.LEVEL_COLOR` tells the three levels
apart by colour, which is one signal; a reader who cannot separate cyan from grey is then
looking at a rail of identical rows, and so is anyone reading a monochrome screenshot in an
issue. The glyph is the second signal and the left rule is the third.

The rules are resolved as whole `border` shorthands, one per branch. An f-string over a Var
in a *prop* stringifies the Var itself into a sentinel Reflex re-parses as text, so
`_level_rule` returns the entire declaration for each level rather than interpolating a
colour into one.

`blocking()` sits inside the scrolling `log` deliberately. What could not be confirmed
belongs to the turn that failed to confirm it and scrolls with it; hoisted above the scroll
area it would pin one turn's English over a rail that has already moved on.
"""

from __future__ import annotations

import reflex as rx

from huella.state import State, TraceRow
from huella.ui.theme import (
    FLAG_INK,
    GRID,
    INK,
    INK_2,
    LEVEL_BG,
    LEVEL_COLOR,
    MONO,
    RADIUS_PILL,
    RADIUS_SM,
    RAIL_W,
    READOUT,
    SUB,
    TRACE,
    TRACK_EYEBROW,
    TRACK_READOUT,
)

RAIL_ID = "huella-registro"
RAIL_LABEL = "Registro de auditoría — cada paso del turno, cada comprobación y cada verdicto"
LOG_LABEL = "Eventos del turno, en orden"

# Keyed on `coros_core.trace.Level`, the same three keys theme.LEVEL_COLOR carries.
_LEVEL_ICON: dict[str, str] = {
    "info": "minus",
    "guardrail": "shield_check",
    "error": "octagon_alert",
}

_EMPTY = (
    "Todavía no hay eventos. Acá queda cada paso del turno: lo que leí de tu entrenamiento, "
    "cada comprobación y lo que no pude verificar."
)
_BLOCKING_NOTE = "Tal como lo reportó el verificador, en inglés y en sus propias palabras."


def _eyebrow(label: str | rx.Var, color: str | rx.Var) -> rx.Component:
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


def _level_color(item: TraceRow) -> rx.Var:
    return rx.match(item.level, *LEVEL_COLOR.items(), READOUT)


def _level_bg(item: TraceRow) -> rx.Var:
    return rx.match(item.level, *LEVEL_BG.items(), "transparent")


def _level_rule(item: TraceRow) -> rx.Var:
    return rx.match(
        item.level,
        ("guardrail", f"2px solid {TRACE}"),
        ("error", f"2px solid {FLAG_INK}"),
        # Transparent rather than absent: a missing rule would shift the rows that lack one.
        "2px solid transparent",
    )


def _level_icon(item: TraceRow) -> rx.Var:
    return rx.match(item.level, *_LEVEL_ICON.items(), _LEVEL_ICON["info"])


def row(item: TraceRow) -> rx.Component:
    color = _level_color(item)
    return rx.hstack(
        rx.text(
            item.seq,
            class_name="hu-num",
            color=SUB,
            font_family=MONO,
            font_size="0.62rem",
            letter_spacing=TRACK_READOUT,
            line_height="1.5",
            opacity="0.7",
            text_align="right",
            width="1.6rem",
            flex_shrink="0",
        ),
        rx.vstack(
            rx.hstack(
                rx.icon(_level_icon(item), size=12, color=color, flex_shrink="0"),
                rx.text(
                    item.event,
                    color=color,
                    font_family=MONO,
                    font_size="0.68rem",
                    font_weight="500",
                    letter_spacing=TRACK_READOUT,
                    line_height="1.4",
                    # The rail is 26rem and event names run long. Without this the name
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
                    color=SUB,
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
    engineering artifact, and the note under them says so rather than leaving a Spanish
    reader to wonder.
    """
    return rx.cond(
        State.blocking.length() > 0,
        rx.vstack(
            rx.hstack(
                rx.icon("shield_alert", size=13, color=FLAG_INK, flex_shrink="0"),
                _eyebrow("No pude confirmar", FLAG_INK),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.vstack(
                rx.foreach(
                    State.blocking,
                    lambda reason: rx.text(
                        reason,
                        color=READOUT,
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
                color=SUB,
                font_family=MONO,
                font_size="0.6rem",
                line_height="1.5",
            ),
            spacing="2",
            align="start",
            width="100%",
            margin_bottom="0.6rem",
            padding="0.6rem 0.7rem",
            background=INK_2,
            border_radius=RADIUS_SM,
        ),
    )


def _empty() -> rx.Component:
    return rx.hstack(
        rx.icon("scroll_text", size=14, color=SUB, flex_shrink="0"),
        rx.text(_EMPTY, color=SUB, font_size="0.72rem", line_height="1.65"),
        spacing="2",
        align="start",
        width="100%",
    )


def _log() -> rx.Component:
    return rx.box(
        blocking(),
        rx.cond(
            State.trace.length() > 0,
            rx.vstack(rx.foreach(State.trace, row), spacing="2", width="100%"),
            _empty(),
        ),
        role="log",
        aria_label=LOG_LABEL,
        # The rail outruns the viewport within a turn, and a region nobody can focus is a
        # region a keyboard cannot scroll.
        tab_index=0,
        # A plain overflow box rather than rx.scroll_area: the scrollbar it inherits is a
        # light one, and `hu-scroll` is what repaints it for the instrument.
        class_name="hu-scroll",
        width="100%",
        flex="1 1 auto",
        min_height="0",
        overflow_y="auto",
        padding="0.6rem",
    )


def _header() -> rx.Component:
    return rx.hstack(
        rx.icon("scroll_text", size=14, color=SUB, flex_shrink="0"),
        _eyebrow("Registro", SUB),
        rx.text(
            State.trace.length(),
            class_name="hu-num",
            color=TRACE,
            font_family=MONO,
            font_size="0.62rem",
            letter_spacing=TRACK_READOUT,
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
            color=SUB,
            # 44px is the floor for a touch target, and this is the control somebody on a
            # phone reaches for first.
            min_width="44px",
            min_height="44px",
            _hover={"background": INK_2, "color": READOUT},
        ),
        spacing="2",
        align="center",
        width="100%",
        flex_shrink="0",
        padding="0.35rem 0.5rem 0.35rem 0.75rem",
        background=INK,
        border_bottom=f"1px solid {GRID}",
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
        margin=["0 1rem 1rem", "0 1rem 1rem", "0 1rem 1rem", "1.5rem 1.4rem 0 0"],
        min_height="44px",
        padding="0 0.9rem",
        color=TRACE,
        background=INK_2,
        border_radius=RADIUS_PILL,
        _hover={"background": INK},
    )


def panel(top: str = "0") -> rx.Component:
    """The rail, or the pill that brings it back.

    `top` is the height of `app.py`'s sticky header: the rail sticks below it and is capped
    at exactly that much less than the viewport. Two numbers that disagree put the last rows
    below the fold with no way to scroll to them.
    """
    return rx.cond(
        State.show_trace,
        rx.vstack(
            _header(),
            _log(),
            id=RAIL_ID,
            role="complementary",
            aria_label=RAIL_LABEL,
            class_name="hu-rail",
            spacing="0",
            align="stretch",
            flex_shrink="0",
            width=["100%", "100%", "100%", RAIL_W],
            max_height=f"calc(100vh - {top})",
            overflow="hidden",
            position=["static", "static", "static", "sticky"],
            top=top,
            background=INK,
            # Stacked under the conversation until the shell turns into two columns, so the
            # boundary the rail draws changes side with it.
            border_top=[f"1px solid {GRID}"] * 3 + ["none"],
            border_left=["none"] * 3 + [f"1px solid {GRID}"],
        ),
        _collapsed(),
    )
