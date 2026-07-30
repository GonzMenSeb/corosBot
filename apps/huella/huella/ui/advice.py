"""The answer: what to buy, what not to, what is still missing, and what was verified.

**A refusal is an object, not a paragraph.** Four of `models.AdviceKind`'s five values
recommend nothing, and each one is a different sentence — "no compres nada", "eso aquí no
está", "no pude confirmarlo", "esto no lo hago yo". `Advice` already refuses to carry
products on any of them, so the only way a person tells them apart on screen is if the
screen says so. `_KINDS` gives each its own icon, eyebrow and headline; the match cases are
built from `get_args(AdviceKind)` rather than from a list typed here, so a kind added to the
type gets a case whether or not anybody wrote it a row, and falls through to `_UNNAMED`
when they did not.

**No refusal spends the flag colour, and that is the one rule in this file worth stating
twice.** Red in Huella means "do not lean on what is on screen". A refusal is the opposite
of that: "no compres nada" and "no se vende aquí" are answers arrived at correctly and
worth leaning on, so they are the neutral register; "no pude confirmarlo" and "hasta aquí
llego yo" are the middle answer, so they are amber. The only red in this module arrives
through `theme.OUTCOME_COLOR`, on a check that ran and failed — which *is* that answer —
and this file imports no `FLAG*` token of its own.

**A colour a panel uses lives in that panel's own row.** `ink` is measured on `well` and
`glyph` on `seal`; a table of colours resolved independently is a table two entries of which
can drift onto a pair the theme never measured. `AMBER_INK` and `READOUT` carry words and
draw nothing, which is why no refusal has a coloured left rule: `theme.EDGE_ON` declares
`EDGE`, `TRACE`, `FLAG` and `SUCCESS` on the instrument and no other token may draw an edge
there. The tint and the medallion are what separate the four.

**Everything on a card except one line came off COROS's feed.** `price_display` arrives
already formatted — `money.minor_to_display` is a Python function and cannot run on a Var,
which is also what keeps the storefront's major units and UCP's minor units from ever
meeting on screen. `image_url` is `""` and never None, so a product COROS ships no photo of
is still a product somebody can buy, and it gets a real fallback rather than an empty frame.
`rationale` is the exception: it is Huella's own sentence, and the quote rule down its left
edge is what says so without a caption.

**`State.blocking` and `CheckRow.detail` are not rendered here.** Both are the evidence
bundle's own English — an engineering artifact read in a PR — and the audit rail is the
register that vocabulary lives in. `confidence` is translated in this file rather than in
`state.py` because "high" is a word on a screen, not a value in a payload, and the per-check
confidence is not the training window's: that one is `State.confidence_label`.
"""

from __future__ import annotations

from typing import NamedTuple, get_args

import reflex as rx

from coros_core.evidence import Confidence
from coros_core.models import AdviceKind

from huella.state import CheckRow, ProductCard, State
from huella.ui.theme import (
    AMBER_INK,
    AMBER_WELL,
    EASE,
    EDGE,
    FONT_DISPLAY,
    GRID,
    INK,
    INK_2,
    MONO,
    OUTCOME_COLOR,
    RADIUS,
    RADIUS_LG,
    RADIUS_PILL,
    RADIUS_SM,
    READOUT,
    SUB,
    SUCCESS_INK,
    TRACE,
    TRACK_DISPLAY,
    TRACK_EYEBROW,
    TRACK_READOUT,
)


class _Refusal(NamedTuple):
    """One refusal and every colour its panel draws, kept together so the pairs stay the
    measured ones: `ink` on `well`, `glyph` on `seal`."""

    icon: str
    eyebrow: str
    headline: str
    ink: str
    glyph: str
    well: str
    seal: str


_KINDS: dict[str, _Refusal] = {
    "buy_nothing": _Refusal(
        icon="ban",
        eyebrow="La respuesta es no comprar",
        headline="No compres nada.",
        ink=READOUT,
        glyph=TRACE,
        well=INK_2,
        seal=INK,
    ),
    "not_sold_locally": _Refusal(
        icon="map_pin_off",
        eyebrow="No se vende en Colombia",
        headline="Eso aquí no está.",
        ink=READOUT,
        glyph=TRACE,
        well=INK_2,
        seal=INK,
    ),
    "insufficient_evidence": _Refusal(
        icon="shield_off",
        eyebrow="Sin verificar",
        headline="No pude confirmarlo.",
        ink=AMBER_INK,
        glyph=AMBER_INK,
        well=AMBER_WELL,
        seal=INK,
    ),
    "needs_human": _Refusal(
        icon="hand",
        eyebrow="Fuera de mi alcance",
        headline="Hasta aquí llego yo.",
        ink=AMBER_INK,
        glyph=AMBER_INK,
        well=AMBER_WELL,
        seal=INK,
    ),
}

_UNNAMED = _Refusal(
    icon="circle_alert",
    eyebrow="Sin recomendación",
    headline="No tengo qué recomendarte.",
    ink=READOUT,
    glyph=TRACE,
    well=INK_2,
    seal=INK,
)

_REFUSAL_KINDS: tuple[str, ...] = tuple(k for k in get_args(AdviceKind) if k != "recommend")

# The bundle's per-check confidence, which is a different question from how thick the
# training window was — `State.confidence_label` answers that one.
_CONFIDENCE_ES: dict[str, str] = {
    "high": "confianza alta",
    "medium": "confianza media",
    "none": "sin confianza",
}
_CONFIDENCE_UNKNOWN = "confianza sin clasificar"

_OUTCOME_ICON: dict[str, str] = {"pass": "check", "fail": "x", "not_run": "minus"}
_OUTCOME_ES: dict[str, str] = {
    "pass": "pasó",
    "fail": "falló",
    "not_run": "no se ejecutó",
}

_BLOCKED = (
    "No pude confirmar todo, así que no te muestro una recomendación. El detalle exacto "
    "está en el registro."
)

_NO_PHOTO = "Sin foto en el catálogo"


def _eyebrow(label: str | rx.Var, color: str = SUB) -> rx.Component:
    return rx.text(
        label,
        color=color,
        font_size="0.62rem",
        font_weight="700",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )


def _figure(value: str | int | rx.Var, size: str) -> rx.Component:
    """A figure in the mono. Nothing asks for tabular numerals: a monospace is tabular by
    construction, which is the whole reason the theme sets figures in one."""
    return rx.text(
        value,
        color=READOUT,
        size=size,
        font_family=MONO,
        font_weight="500",
        letter_spacing=TRACK_READOUT,
        line_height="1.1",
    )


def _lines(values: rx.Var, color: str) -> rx.Component:
    """A list marked by a hairline tick rather than a bullet. The tick is GRID — the
    instrument's own furniture, under every floor on purpose — and the words carry it."""
    return rx.vstack(
        rx.foreach(
            values,
            lambda value: rx.hstack(
                rx.box(
                    width="0.55rem",
                    height="2px",
                    margin_top="0.62rem",
                    background=GRID,
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


# ── the recommendation ────────────────────────────────────────────────────────


def _photo(item: ProductCard) -> rx.Component:
    return rx.box(
        rx.cond(
            item.image_url != "",
            rx.image(
                src=item.image_url,
                alt=item.title,
                loading="lazy",
                width="100%",
                height="100%",
                object_fit="contain",
            ),
            rx.center(
                rx.vstack(
                    rx.icon("image_off", size=20, color=SUB),
                    _eyebrow(_NO_PHOTO),
                    spacing="2",
                    align="center",
                ),
                width="100%",
                height="100%",
            ),
        ),
        height="9rem",
        width="100%",
        flex_shrink="0",
        padding="0.6rem",
        background=INK,
        border_radius=RADIUS_SM,
        overflow="hidden",
    )


def _satisfies(item: ProductCard) -> rx.Component:
    return rx.cond(
        item.satisfies.length() > 0,
        rx.vstack(
            _eyebrow("Cumple"),
            rx.flex(
                rx.foreach(
                    item.satisfies,
                    lambda key: rx.text(
                        key,
                        color=TRACE,
                        size="1",
                        weight="medium",
                        line_height="1.2",
                        padding="0.2rem 0.5rem",
                        background=INK,
                        border_radius=RADIUS_PILL,
                    ),
                ),
                wrap="wrap",
                gap="0.3rem",
                width="100%",
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
    )


def card(item: ProductCard) -> rx.Component:
    return rx.vstack(
        _photo(item),
        rx.link(
            rx.text(
                item.title,
                color=READOUT,
                size="3",
                weight="bold",
                line_height="1.35",
                # Two lines reserved either way, so titles of different lengths still line
                # their prices up across a row of cards.
                min_height="2.7em",
                class_name="hu-clamp-2",
            ),
            href=item.product_url,
            is_external=True,
            width="100%",
            _hover={"color": TRACE},
        ),
        rx.box(
            _figure(item.price_display, "5"),
            width="100%",
            padding_top="0.6rem",
            border_top=f"1px solid {GRID}",
        ),
        _satisfies(item),
        rx.cond(
            item.rationale != "",
            rx.text(
                item.rationale,
                color=SUB,
                size="2",
                line_height="1.6",
                padding_left="0.7rem",
                # The one quote rule on the card. Everything without it came off the feed.
                border_left=f"2px solid {TRACE}",
            ),
        ),
        rx.link(
            rx.hstack(
                rx.text("Ver en COROS", size="2", weight="bold"),
                rx.icon("arrow_up_right", size=15),
                spacing="1",
                align="center",
            ),
            href=item.product_url,
            is_external=True,
            color=TRACE,
            width="100%",
            # Pinned to the bottom whatever the title and the rationale did above it.
            margin_top="auto",
            padding_top="0.7rem",
            border_top=f"1px solid {GRID}",
            _hover={"text_decoration": "underline"},
        ),
        spacing="3",
        align="start",
        width="100%",
        height="100%",
        padding="0.7rem 0.8rem 0.8rem",
        background=INK_2,
        border=f"1px solid {GRID}",
        border_radius=RADIUS,
        transition=f"border-color 180ms {EASE}",
        _hover={"border_color": TRACE},
    )


def _summary() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            _eyebrow("Lo que te sirve"),
            rx.hstack(
                _figure(State.item_count, "4"),
                rx.text(
                    rx.cond(State.item_count == 1, "producto", "productos"),
                    color=SUB,
                    size="2",
                ),
                spacing="2",
                align="baseline",
            ),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.vstack(
            _eyebrow("Total"),
            _figure(State.total_display, "5"),
            spacing="1",
            align="end",
        ),
        width="100%",
        align="end",
        padding_bottom="0.7rem",
        border_bottom=f"1px solid {GRID}",
    )


def kit() -> rx.Component:
    return rx.cond(
        State.has_cards,
        rx.vstack(
            _summary(),
            rx.grid(
                rx.foreach(State.cards, card),
                # Two columns and no more: lg is where the audit rail moves in beside this
                # column, and a third card there would share what is left of 1024px.
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing="3",
                width="100%",
            ),
            # A class, never a style prop, so the stylesheet can answer
            # prefers-reduced-motion for it.
            class_name="hu-kit",
            spacing="4",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=INK,
            border=f"1px solid {GRID}",
            border_radius=RADIUS_LG,
        ),
    )


def notes() -> rx.Component:
    """Caveats beside a recommendation. A refusal's caveats are inside its own panel, so
    this is conditioned on there being cards for them to qualify."""
    return rx.cond(
        State.has_cards & (State.caveats.length() > 0),
        rx.vstack(
            _eyebrow("Con estas salvedades"),
            _lines(State.caveats, SUB),
            spacing="3",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=INK,
            border=f"1px solid {GRID}",
            border_radius=RADIUS_LG,
        ),
    )


# ── the refusals ──────────────────────────────────────────────────────────────


def _medallion(spec: _Refusal) -> rx.Component:
    return rx.center(
        rx.icon(spec.icon, size=19, color=spec.glyph),
        width="2.4rem",
        height="2.4rem",
        flex_shrink="0",
        background=spec.seal,
        border_radius=RADIUS_SM,
    )


def _named_devices(spec: _Refusal) -> rx.Component:
    """What COROS Colombia does not sell, by name, as objects rather than as a sentence.

    The names are resolved from `devices.py` in `state.py` — never a slug, and never a
    model's spelling of either, which is why no device name is written in this file.
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
                        rx.text(
                            name,
                            color=spec.ink,
                            size="2",
                            weight="bold",
                            line_height="1.2",
                        ),
                        spacing="2",
                        align="center",
                        padding="0.3rem 0.6rem",
                        background=spec.seal,
                        border_radius=RADIUS_PILL,
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


def _refusal(spec: _Refusal) -> rx.Component:
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
                    font_size=["1.25rem", "1.35rem", "1.45rem"],
                    font_weight="700",
                    letter_spacing=TRACK_DISPLAY,
                    line_height="1.15",
                ),
                spacing="1",
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
                _lines(State.caveats, SUB),
                spacing="2",
                align="start",
                width="100%",
                padding="0.7rem 0.8rem",
                background=spec.seal,
                border_radius=RADIUS_SM,
            ),
        ),
        spacing="4",
        align="start",
        width="100%",
        padding=["1rem", "1rem 1.1rem", "1.05rem 1.25rem"],
        # A step above the panels around it — on the instrument elevation is a lighter
        # surface — and EDGE rather than GRID, because this one is a boundary somebody has
        # to see. A refusal filled like the questions panel is one more panel.
        background=spec.well,
        border=f"1px solid {EDGE}",
        border_radius=RADIUS_LG,
    )


def verdict() -> rx.Component:
    return rx.cond(
        State.is_refusal,
        rx.match(
            State.advice_kind,
            *((kind, _refusal(_KINDS.get(kind, _UNNAMED))) for kind in _REFUSAL_KINDS),
            _refusal(_UNNAMED),
        ),
    )


# ── the interview and the checklist ───────────────────────────────────────────


def questions() -> rx.Component:
    """Pending work, so it is a marked list and not a verdict panel — the two must not read
    as the same kind of object."""
    return rx.cond(
        State.has_questions,
        rx.vstack(
            _eyebrow("Lo que me falta saber", TRACE),
            _lines(State.questions, READOUT),
            spacing="3",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=INK,
            border=f"1px solid {GRID}",
            border_left=f"3px solid {TRACE}",
            border_radius=RADIUS_LG,
        ),
    )


def _glyph(outcome: str) -> rx.Component:
    return rx.hstack(
        rx.icon(
            _OUTCOME_ICON.get(outcome, _OUTCOME_ICON["not_run"]),
            size=14,
            color=OUTCOME_COLOR[outcome],
            flex_shrink="0",
        ),
        # The glyph is the whole answer for a sighted reader and silence for everybody
        # else, and "no se ejecutó" is not "falló".
        rx.text(_OUTCOME_ES.get(outcome, _OUTCOME_ES["not_run"]), class_name="hu-sr-only"),
        spacing="0",
        align="center",
        flex_shrink="0",
        margin_top="0.15rem",
    )


def _outcome(check: CheckRow) -> rx.Component:
    return rx.match(
        check.outcome,
        *((outcome, _glyph(outcome)) for outcome in OUTCOME_COLOR),
        _glyph("not_run"),
    )


def _confidence(check: CheckRow) -> rx.Component:
    return rx.text(
        rx.match(
            check.confidence,
            *(
                (value, _CONFIDENCE_ES.get(value, _CONFIDENCE_UNKNOWN))
                for value in get_args(Confidence)
            ),
            _CONFIDENCE_UNKNOWN,
        ),
        color=SUB,
        size="1",
        white_space="nowrap",
        flex_shrink="0",
    )


def _check(check: CheckRow) -> rx.Component:
    return rx.hstack(
        _outcome(check),
        rx.text(check.label, color=READOUT, size="2", line_height="1.5"),
        rx.spacer(),
        _confidence(check),
        spacing="2",
        align="start",
        width="100%",
    )


def evidence() -> rx.Component:
    """What was verified, in the person's language. A blocked bundle is amber and not red:
    "no pude confirmarlo" is an honest answer about a check nobody could run, not a check
    that ran and failed — and red in this app is reserved for the second. The rows stay on
    the panel rather than inside the blocked well, because a failed check's glyph is `FLAG`
    and `theme.EDGE_ON` measures it on the instrument's surfaces only."""
    return rx.cond(
        State.has_evidence,
        rx.vstack(
            rx.hstack(
                rx.cond(
                    State.blocked,
                    rx.icon("shield_alert", size=16, color=AMBER_INK, flex_shrink="0"),
                    rx.icon("shield_check", size=16, color=SUCCESS_INK, flex_shrink="0"),
                ),
                _eyebrow("Lo que verifiqué"),
                rx.spacer(),
                rx.text(
                    State.checks_summary,
                    color=rx.cond(State.blocked, AMBER_INK, SUCCESS_INK),
                    size="1",
                    font_family=MONO,
                    letter_spacing=TRACK_READOUT,
                    padding="0.15rem 0.55rem",
                    background=INK_2,
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
                    rx.icon("circle_alert", size=14, color=AMBER_INK, flex_shrink="0"),
                    # No live region: `loop.py` already said this to the person in the
                    # transcript, and announcing one turn twice is worse than once.
                    rx.text(_BLOCKED, color=AMBER_INK, size="2", line_height="1.55"),
                    spacing="2",
                    align="start",
                    width="100%",
                    padding="0.6rem 0.7rem",
                    background=AMBER_WELL,
                    border_radius=RADIUS_SM,
                ),
            ),
            spacing="3",
            align="start",
            width="100%",
            padding="0.9rem 1rem",
            background=INK,
            border=f"1px solid {GRID}",
            border_radius=RADIUS_LG,
        ),
    )
