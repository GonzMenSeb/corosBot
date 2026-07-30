"""What the turn produced when it is not a list of products.

Four of `models.AdviceKind`'s five values are answers that recommend nothing, and each one
is a different sentence: "no compres nada", "eso no se vende aquí", "no pude leer el
catálogo", "esto no lo hago yo". `Advice` already refuses to carry products on any of them,
so the only way a person can tell them apart on screen is if the screen says so — which is
why each gets its own panel here instead of a paragraph in the transcript and a shrug.

**The panels are four literal trees, selected by `rx.match`, not one tree with four
`rx.match`ed colours.** A panel's ink is measured against that panel's own surface and
against nothing else, and a table of colours resolved independently is a table two entries of
which can silently drift onto a pair the theme never measured. Written this way the pairing
is structural: `_panel()` takes both halves at once.

**`State.blocking` is not rendered here.** Its strings are the evidence bundle's own English
— "stock did not run" — an engineering artifact for a reviewer, and `loop.py` already says
the same thing to the person in Spanish in the transcript. It belongs on the audit rail, in
the register the rest of that vocabulary lives in, and `trace_panel.py` is where it lands.

`Check.detail` is left unrendered for the same reason, and `confidence` is translated here
rather than in `state.py` because "high" is a word on a screen, not a value in a payload.
"""

from __future__ import annotations

from typing import NamedTuple

import reflex as rx

from brujula.state import CheckRow, State
from brujula.ui.theme import (
    BRASS,
    BRASS_DEEP,
    BRASS_SOFT,
    CARD,
    DANGER,
    DANGER_INK,
    DANGER_SOFT,
    EDGE,
    FONT_DISPLAY,
    INK,
    OUTCOME_COLOR,
    QUIET,
    RADIUS,
    RADIUS_PILL,
    RADIUS_SM,
    RULE,
    SHADOW_SM,
    SHADOW_XS,
    SUCCESS,
    TRACK_DISPLAY,
    TRACK_EYEBROW,
    WARN_INK,
    WARN_SOFT,
)


class _Verdict(NamedTuple):
    """One refusal, and the four values that have to agree with each other.

    `ink` is measured on `soft` and `glyph` on CARD, which is the medallion's own surface.
    """

    glyph: str
    icon: str
    eyebrow: str
    headline: str
    ink: str
    soft: str


# Keyed on `models.AdviceKind` minus "recommend". A kind with no row here falls through to
# `_UNNAMED`, which says less but never says the wrong thing; the suite asserts every kind
# the type allows has a row, so the fallback stays unreachable.
_KINDS: dict[str, _Verdict] = {
    "buy_nothing": _Verdict(
        glyph=DANGER,
        icon="ban",
        eyebrow="La respuesta es no comprar",
        headline="No compres nada.",
        ink=DANGER_INK,
        soft=DANGER_SOFT,
    ),
    "not_sold_locally": _Verdict(
        glyph=DANGER,
        icon="map_pin_off",
        eyebrow="No se vende en Colombia",
        headline="Eso aquí no está.",
        ink=DANGER_INK,
        soft=DANGER_SOFT,
    ),
    "insufficient_evidence": _Verdict(
        glyph=WARN_INK,
        icon="wifi_off",
        eyebrow="Catálogo sin leer",
        headline="No pude mirar el catálogo.",
        ink=WARN_INK,
        soft=WARN_SOFT,
    ),
    "needs_human": _Verdict(
        glyph=BRASS_DEEP,
        icon="hand",
        eyebrow="Fuera de mi alcance",
        headline="Hasta aquí llego yo.",
        ink=BRASS_DEEP,
        soft=BRASS_SOFT,
    ),
}

_UNNAMED = _Verdict(
    glyph=DANGER,
    icon="circle_alert",
    eyebrow="Sin recomendación",
    headline="No tengo qué recomendarte.",
    ink=DANGER_INK,
    soft=DANGER_SOFT,
)

# `evidence.Confidence`, in the language the rest of the screen is in.
_CONFIDENCE_ES: dict[str, str] = {
    "high": "confianza alta",
    "medium": "confianza media",
    "none": "sin confianza",
}

_OUTCOME_ICON: dict[str, str] = {"pass": "check", "fail": "x", "not_run": "minus"}

_BLOCKED = (
    "No pude confirmar todo, así que no te muestro una recomendación. El detalle exacto "
    "está en el registro."
)


def _eyebrow(label: str | rx.Var, color: str = QUIET) -> rx.Component:
    return rx.text(
        label,
        color=color,
        font_size="0.62rem",
        font_weight="700",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )


def _lines(values: rx.Var, color: str) -> rx.Component:
    """A list whose marker is an index tick rather than a bullet.

    The tick is `RULE`, which is under every contrast floor there is and is meant to be:
    it is furniture aligning the rows, and the words beside it are the answer.
    """
    return rx.vstack(
        rx.foreach(
            values,
            lambda value: rx.hstack(
                rx.box(
                    width="0.55rem",
                    height="2px",
                    margin_top="0.62rem",
                    background=RULE,
                    flex_shrink="0",
                ),
                rx.text(value, color=color, size="2", line_height="1.65"),
                spacing="2",
                align="start",
                width="100%",
            ),
        ),
        spacing="2",
        align="start",
        width="100%",
    )


def questions() -> rx.Component:
    """The interview. Pending work, so it is a marked line on the page and not a verdict
    panel — the two must not read as the same kind of object."""
    return rx.cond(
        State.has_questions,
        rx.vstack(
            _eyebrow("Para poder responderte", BRASS),
            _lines(State.questions, INK),
            spacing="3",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=CARD,
            border=f"1px solid {EDGE}",
            border_left=f"3px solid {BRASS}",
            border_radius=RADIUS,
            box_shadow=SHADOW_XS,
        ),
    )


def _medallion(spec: _Verdict) -> rx.Component:
    """A white seal on the tinted panel, because the pure red cannot carry a glyph on its
    own tint: `DANGER on DANGER_SOFT` is under the non-text floor, `DANGER on CARD` is not."""
    return rx.center(
        rx.icon(spec.icon, size=19, color=spec.glyph),
        width="2.4rem",
        height="2.4rem",
        flex_shrink="0",
        background=CARD,
        border_radius=RADIUS_SM,
        box_shadow=SHADOW_XS,
    )


def _named_devices(spec: _Verdict) -> rx.Component:
    """What COROS Colombia does not sell, by name, as objects rather than as a sentence.

    The names are device names resolved from `devices.py` in `state.py` — never the slug,
    and never a model's spelling of either.
    """
    return rx.cond(
        State.unavailable.length() > 0,
        rx.vstack(
            _eyebrow("COROS Colombia no vende", spec.ink),
            rx.flex(
                rx.foreach(
                    State.unavailable,
                    lambda name: rx.hstack(
                        rx.icon("circle_slash", size=13, color=spec.glyph, flex_shrink="0"),
                        rx.text(name, color=spec.ink, size="2", weight="bold", line_height="1.2"),
                        spacing="2",
                        align="center",
                        padding="0.3rem 0.6rem",
                        background=CARD,
                        border_radius=RADIUS_PILL,
                        box_shadow=SHADOW_XS,
                    ),
                ),
                wrap="wrap",
                gap="0.4rem",
                width="100%",
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
    )


def _panel(spec: _Verdict) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            _medallion(spec),
            rx.vstack(
                _eyebrow(spec.eyebrow, spec.ink),
                rx.heading(
                    spec.headline,
                    as_="h2",
                    color=spec.ink,
                    font_family=FONT_DISPLAY,
                    font_size=["1.35rem", "1.5rem", "1.6rem"],
                    font_weight="700",
                    letter_spacing=TRACK_DISPLAY,
                    line_height="1.15",
                ),
                spacing="2",
                align="start",
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        _named_devices(spec),
        rx.cond(
            State.caveats.length() > 0,
            rx.vstack(
                _eyebrow("Con estas salvedades"),
                _lines(State.caveats, QUIET),
                spacing="2",
                align="start",
                width="100%",
                padding="0.75rem 0.85rem",
                # A white well inside the tint: the caveats are neutral notes, and the only
                # ink the theme measured on the tint is the panel's own.
                background=CARD,
                border_radius=RADIUS_SM,
            ),
        ),
        spacing="4",
        align="start",
        width="100%",
        padding=["1rem", "1.1rem 1.2rem", "1.15rem 1.35rem"],
        background=spec.soft,
        border=f"1px solid {EDGE}",
        border_radius=RADIUS,
        box_shadow=SHADOW_SM,
    )


def verdict() -> rx.Component:
    return rx.cond(
        State.is_refusal,
        rx.match(
            State.advice_kind,
            *((kind, _panel(spec)) for kind, spec in _KINDS.items()),
            _panel(_UNNAMED),
        ),
    )


def notes() -> rx.Component:
    """Caveats beside a recommendation. A refusal's caveats are inside its own panel, so
    this is conditioned on there being cards for them to qualify."""
    return rx.cond(
        State.has_cards & (State.caveats.length() > 0),
        rx.vstack(
            _eyebrow("Con estas salvedades"),
            _lines(State.caveats, QUIET),
            spacing="3",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=CARD,
            border=f"1px solid {EDGE}",
            border_radius=RADIUS,
            box_shadow=SHADOW_XS,
        ),
    )


def _outcome_icon(check: CheckRow) -> rx.Component:
    return rx.match(
        check.outcome,
        *(
            (outcome, rx.icon(_OUTCOME_ICON[outcome], size=14, color=OUTCOME_COLOR[outcome]))
            for outcome in _OUTCOME_ICON
        ),
        rx.icon("minus", size=14, color=OUTCOME_COLOR["not_run"]),
    )


def _check(check: CheckRow) -> rx.Component:
    return rx.hstack(
        _outcome_icon(check),
        rx.text(check.label, color=INK, size="2", line_height="1.5"),
        rx.spacer(),
        rx.text(
            rx.match(
                check.confidence,
                *_CONFIDENCE_ES.items(),
                _CONFIDENCE_ES["none"],
            ),
            color=QUIET,
            size="1",
            white_space="nowrap",
            flex_shrink="0",
        ),
        spacing="2",
        align="start",
        width="100%",
    )


def evidence() -> rx.Component:
    """The checklist, in the person's language: what was verified, and how sure that is.

    A `not_run` row is not a `fail` row — a check nobody ran is not a check that failed —
    so it gets the muted colour and a dash, which is the distinction `theme.OUTCOME_COLOR`
    is drawn to preserve.
    """
    return rx.cond(
        State.has_evidence,
        rx.vstack(
            rx.hstack(
                rx.cond(
                    State.blocked,
                    rx.icon("shield_alert", size=16, color=DANGER, flex_shrink="0"),
                    rx.icon("shield_check", size=16, color=SUCCESS, flex_shrink="0"),
                ),
                _eyebrow("Verificación"),
                rx.spacer(),
                rx.text(
                    State.checks_summary,
                    color=BRASS_DEEP,
                    size="1",
                    weight="bold",
                    font_variant_numeric="tabular-nums",
                    padding="0.15rem 0.5rem",
                    background=BRASS_SOFT,
                    border_radius=RADIUS_PILL,
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.vstack(rx.foreach(State.checks, _check), spacing="2", width="100%"),
            rx.cond(
                State.blocked,
                rx.hstack(
                    rx.icon("circle_alert", size=14, color=DANGER_INK, flex_shrink="0"),
                    rx.text(_BLOCKED, color=DANGER_INK, size="2", line_height="1.55"),
                    spacing="2",
                    align="start",
                    width="100%",
                    padding="0.6rem 0.7rem",
                    background=DANGER_SOFT,
                    border_radius=RADIUS_SM,
                ),
            ),
            spacing="3",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=CARD,
            border=f"1px solid {EDGE}",
            border_radius=RADIUS,
            box_shadow=SHADOW_XS,
        ),
    )
