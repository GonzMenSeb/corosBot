"""What the training demonstrates, and how sure we are.

Huella's whole premise on one screen: the D2 uncertainty layer, the parsed figures with the
provenance of each one, and the counts the statement rests on.

**The statement is rendered verbatim.** `UncertaintyVerdict`'s own validator regenerates it
from the flags and three counts and refuses the object when the two disagree, so it is the
one sentence here that provably carries nothing a model wrote. Reformatting it would be
re-deriving something already proven.

**The confidence badge is three literal chips selected by `rx.match`, not one chip with two
matched colour props.** `theme.CONFIDENCE_COLOR` and `theme.CONFIDENCE_BG` are keyed the same
way, but resolved independently they are two tables that can drift onto a pair nobody
measured. Keyed once, per branch, the ink and its well are paired structurally — Brújula's
`advice._KINDS` is the same argument. The Python indexing is over the branch's own literal
key; the runtime decision is `rx.match`'s, and no colour here is picked by matching a string.

**A band is not a measurement, and the same mono treatment renders both.** That is deliberate:
a derived band and a stated one are equally load-bearing for the advice, so the provenance
label is the entire distinction — words plus a colour, never a hue alone. It never shrinks and
never wraps inside itself; on a narrow viewport the row wraps and the label drops to its own
line intact, because a band whose provenance was truncated away reads as a figure somebody
measured.

**`RequirementRow.rationale` is `privacy.py`'s regenerated template**, rebuilt by `seal()`
from the requirement's own two counts and compared — which is the only reason free text cannot
ride out in that field. It is safe to show, and for a derived band it is the provenance.

**The read window is on screen because the statement leans on it.** `sample_size`,
`window_days`, `stale_days`, `manual_dropped` and `truncated` come off `privacy.Sync`, and the
statement is a pure function of the flags and three of them. `stale_days` is -1 when nothing
was read at all — `stale_display` is `""` there — and "ninguna" is what that says, not a
zero pretending to be a recent day.

**The mode sentence outranks the uncertainty panel's own gate.** `State.mode_label` moves with
the advice on screen and `State.uncertainty_statement` moves with the verdict; a turn that
produced advice with no verdict sets one and not the other, so the panel opens on either. The
interview branch is the one lie this app exists not to tell, and it is set in the amber
register rather than in the same quiet accent as the reassuring half.

The confidence three-way is a chip fill and not a rule. `theme.SURFACES` names AMBER_WELL and
SUCCESS_WELL the window chips, and `CONFIDENCE_COLOR` is a TYPE_ON set with no edge measured
for it; the panel's left rule stays TRACE, which is an EDGE_ON colour against the page behind
it.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from huella.state import RequirementRow, State
from huella.ui.theme import (
    AMBER_INK,
    AMBER_WELL,
    CONFIDENCE_BG,
    CONFIDENCE_COLOR,
    GRID,
    INK,
    INK_2,
    MONO,
    RADIUS_LG,
    RADIUS_PILL,
    RADIUS_SM,
    READOUT,
    SUB,
    TRACE,
    TRACK_EYEBROW,
    TRACK_READOUT,
)

_UNCERTAINTY_LABEL = "En qué me apoyo, y qué tan lejos llega"
_REQUIREMENTS_LABEL = "Lo que entendí de tu entrenamiento, y de dónde salió cada dato"
_WINDOW_LABEL = "La ventana que leí: cuántas actividades, cuántos días y qué tan reciente"

_DERIVED = "de tu historial"
_STATED = "lo dijiste tú"
_NO_ACTIVITY = "ninguna"


def _eyebrow(label: str) -> rx.Component:
    return rx.text(
        label,
        color=SUB,
        font_size="0.62rem",
        font_weight="700",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )


def _panel(*children: rx.Component, label: str, **props: Any) -> rx.Component:
    return rx.vstack(
        *children,
        role="group",
        aria_label=label,
        width="100%",
        align="start",
        spacing="2",
        padding="0.9rem 1rem",
        background=INK,
        border=f"1px solid {GRID}",
        border_radius=RADIUS_LG,
        **props,
    )


def _well(*children: rx.Component) -> rx.Component:
    return rx.vstack(
        *children,
        width="100%",
        align="start",
        spacing="1",
        padding="0.55rem 0.7rem",
        background=INK_2,
        border_radius=RADIUS_SM,
    )


def _chip(label: rx.Var | str) -> rx.Component:
    return rx.text(
        label,
        size="1",
        color=AMBER_INK,
        background=AMBER_WELL,
        padding="0.15rem 0.55rem",
        border_radius=RADIUS_PILL,
        white_space="nowrap",
    )


def _badge(confidence: str) -> rx.Component:
    return rx.text(
        State.confidence_label,
        size="1",
        weight="bold",
        color=CONFIDENCE_COLOR[confidence],
        background=CONFIDENCE_BG[confidence],
        padding="0.15rem 0.6rem",
        border_radius=RADIUS_PILL,
        white_space="nowrap",
        flex_shrink="0",
    )


def _confidence() -> rx.Component:
    return rx.match(
        State.confidence,
        *((name, _badge(name)) for name in CONFIDENCE_COLOR),
        _badge("none"),
    )


def _cold_start() -> rx.Component:
    """A connected account with two activities is not a signal, and the panel says so.

    Neutral ink rather than a second semantic hue: `cold_start` is exactly the case the badge
    above it is already colouring — the verdict refuses `confidence != "none"` without a
    `DemonstratedTraining` — so this is the explanation, not a second verdict.
    """
    return rx.cond(
        State.cold_start,
        _well(
            _eyebrow("Arranque en frío"),
            rx.text(State.cold_start_reason, size="2", color=READOUT, line_height="1.6"),
        ),
    )


def _mode_line(icon: str, color: str, background: str) -> rx.Component:
    return rx.hstack(
        rx.icon(icon, size=14, color=color, flex_shrink="0"),
        rx.text(State.mode_label, size="2", color=color, line_height="1.6"),
        spacing="2",
        align="start",
        width="100%",
        padding="0.5rem 0.7rem",
        background=background,
        border_radius=RADIUS_SM,
    )


def _mode() -> rx.Component:
    return rx.cond(
        State.mode_label != "",
        rx.cond(
            State.is_interview,
            _mode_line("message_circle", AMBER_INK, AMBER_WELL),
            _mode_line("activity", TRACE, INK_2),
        ),
    )


def uncertainty() -> rx.Component:
    return rx.cond(
        (State.uncertainty_statement != "") | (State.mode_label != ""),
        _panel(
            rx.hstack(
                _eyebrow("En qué me apoyo"),
                rx.spacer(),
                _confidence(),
                width="100%",
                align="center",
                spacing="2",
            ),
            rx.cond(
                State.uncertainty_statement != "",
                rx.text(
                    State.uncertainty_statement,
                    size="2",
                    color=READOUT,
                    line_height="1.65",
                ),
            ),
            rx.cond(
                State.uncertainty_flags.length() > 0,
                rx.flex(
                    rx.foreach(State.uncertainty_flags, _chip),
                    wrap="wrap",
                    gap="0.4rem",
                    width="100%",
                ),
            ),
            _cold_start(),
            _mode(),
            label=_UNCERTAINTY_LABEL,
            border_left=f"3px solid {TRACE}",
        ),
    )


def _provenance(label: str, color: str, icon: str) -> rx.Component:
    return rx.hstack(
        rx.icon(icon, size=12, color=color, flex_shrink="0"),
        rx.text(label, size="1", color=color, white_space="nowrap"),
        spacing="1",
        align="center",
        flex_shrink="0",
    )


def _requirement(row: RequirementRow) -> rx.Component:
    return _well(
        rx.hstack(
            rx.text(
                row.label,
                size="2",
                color=SUB,
                width=["7rem", "9rem", "11rem"],
                flex_shrink="0",
            ),
            rx.text(
                row.value,
                size="2",
                color=READOUT,
                font_family=MONO,
                letter_spacing=TRACK_READOUT,
                class_name="hu-num",
                min_width="0",
                word_break="break-word",
            ),
            rx.spacer(),
            rx.cond(
                row.derived,
                _provenance(_DERIVED, TRACE, "activity"),
                _provenance(_STATED, AMBER_INK, "message_circle"),
            ),
            width="100%",
            align="center",
            spacing="2",
            wrap="wrap",
        ),
        rx.cond(
            row.rationale != "",
            rx.text(row.rationale, size="1", color=SUB, line_height="1.6"),
        ),
    )


def requirements() -> rx.Component:
    return rx.cond(
        State.has_requirements,
        _panel(
            _eyebrow("Lo que entendí de tu entrenamiento"),
            rx.vstack(
                rx.foreach(State.requirements, _requirement),
                spacing="2",
                width="100%",
                align="start",
            ),
            label=_REQUIREMENTS_LABEL,
        ),
    )


def _figure(label: str, value: rx.Var | str, color: str | rx.Var) -> rx.Component:
    return rx.vstack(
        rx.text(
            value,
            size="4",
            color=color,
            font_family=MONO,
            letter_spacing=TRACK_READOUT,
            line_height="1.2",
            class_name="hu-num",
        ),
        rx.text(label, size="1", color=SUB, line_height="1.3"),
        spacing="1",
        align="start",
        min_width="7rem",
        padding="0.5rem 0.7rem",
        background=INK_2,
        border_radius=RADIUS_SM,
    )


def window() -> rx.Component:
    """The counts the uncertainty statement is computed from, as a readout row.

    Gated on the connection and deliberately not on `sample_size > 0`: zero of everything on a
    connected account is the case this row exists for. A read that Strava refused, or one that
    found no activities, is exactly why the confidence is "none", and hiding the row would
    leave that statement resting on figures nobody can see.
    """
    return rx.cond(
        State.connected,
        _panel(
            _eyebrow("La ventana que leí"),
            rx.flex(
                _figure("actividades leídas", State.sample_size, READOUT),
                _figure("días de ventana", State.window_days, READOUT),
                _figure(
                    "última actividad",
                    rx.cond(State.stale_display != "", State.stale_display, _NO_ACTIVITY),
                    rx.cond(State.stale_display != "", READOUT, SUB),
                ),
                _figure(
                    "descartadas a mano",
                    State.manual_dropped,
                    rx.cond(State.manual_dropped > 0, AMBER_INK, READOUT),
                ),
                _figure(
                    "lectura",
                    rx.cond(State.truncated, "cortada", "completa"),
                    rx.cond(State.truncated, AMBER_INK, READOUT),
                ),
                wrap="wrap",
                gap="0.5rem",
                width="100%",
            ),
            label=_WINDOW_LABEL,
        ),
    )
