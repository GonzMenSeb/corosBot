"""The cards, and the one line on them that Brújula wrote.

Every factual thing a card shows — the title, the price, the photo, the link, the
requirements it satisfies — is a field of `state.ProductCard`, which `state._card()` builds
from a feed-backed `AdviceItem`. A specification the catalogue does not carry cannot be
rendered here, because there is no field to render it from. That is the whole reason the
card takes a typed row instead of a paragraph.

`rationale` is the exception and the only one: it is Brújula's own sentence about why this
product and not another. It gets the quote rule down its left edge — the one element on the
card wearing one — so prose is visibly prose and the rest is visibly the catalogue's. The
footer says the same thing in words, once per card, because a card is the surface a person
screenshots and sends to somebody else.

`price_display` arrives already formatted. `money.minor_to_display` is a Python function and
cannot be called on a Var inside a component, so a price that has not been through it cannot
reach this file at all — which is also what keeps the storefront's major units and UCP's
minor units from ever meeting on screen.

`image_url` is `""` and never None when COROS ships no photo: an in-stock product without one
is still buyable, and `rx.cond` needs a value to test.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import ProductCard, State
from brujula.ui.theme import (
    BRASS,
    BRASS_DEEP,
    BRASS_SOFT,
    CARD,
    EASE,
    EDGE,
    FONT_DISPLAY,
    INK,
    PAPER_DEEP,
    QUIET,
    RADIUS,
    RADIUS_PILL,
    RADIUS_SM,
    RULE,
    SHADOW_MD,
    SHADOW_XS,
    SUB,
    TRACK_EYEBROW,
    TRACK_TIGHT,
)

PROVENANCE = "Título, precio y foto del catálogo de COROS Colombia"


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


def _figure(value: str | rx.Var, size: str, color: str = INK) -> rx.Component:
    """A number set in the editorial face, with the digits locked to one width.

    Prices are read against each other down a column of cards; proportional figures make
    two identical prices look like different lengths.
    """
    return rx.text(
        value,
        color=color,
        font_family=FONT_DISPLAY,
        font_size=size,
        font_weight="700",
        font_variant_numeric="tabular-nums",
        letter_spacing=TRACK_TIGHT,
        line_height="1.05",
    )


def _photo(card: ProductCard) -> rx.Component:
    return rx.box(
        rx.cond(
            card.image_url != "",
            rx.image(
                src=card.image_url,
                alt=card.title,
                loading="lazy",
                width="100%",
                height="100%",
                object_fit="contain",
            ),
            rx.center(
                rx.vstack(
                    # SUB is COROS's own sub-grey and is under AA on every surface here, so
                    # it is only ever an inert glyph. The words beside it carry the answer.
                    rx.icon("image_off", size=20, color=SUB),
                    _eyebrow("Sin foto en el catálogo"),
                    spacing="2",
                    align="center",
                ),
                width="100%",
                height="100%",
            ),
        ),
        height="9.5rem",
        width="100%",
        flex_shrink="0",
        padding="0.6rem",
        background=PAPER_DEEP,
        border_radius=RADIUS_SM,
        overflow="hidden",
    )


def _satisfies(card: ProductCard) -> rx.Component:
    return rx.cond(
        card.satisfies.length() > 0,
        rx.vstack(
            _eyebrow("Cumple"),
            rx.flex(
                rx.foreach(
                    card.satisfies,
                    lambda key: rx.text(
                        key,
                        color=BRASS_DEEP,
                        size="1",
                        weight="medium",
                        line_height="1.2",
                        padding="0.2rem 0.5rem",
                        background=BRASS_SOFT,
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
                color=INK,
                size="3",
                weight="bold",
                line_height="1.35",
                # Two lines reserved either way, so titles of different lengths still line
                # their prices up across a row of cards.
                min_height="2.7em",
                class_name="bj-clamp-2",
            ),
            href=item.product_url,
            is_external=True,
            width="100%",
            _hover={"color": BRASS},
        ),
        rx.box(
            _figure(item.price_display, "1.5rem"),
            width="100%",
            padding_top="0.6rem",
            border_top=f"1px solid {RULE}",
        ),
        _satisfies(item),
        rx.cond(
            item.rationale != "",
            rx.text(
                item.rationale,
                color=QUIET,
                size="2",
                line_height="1.6",
                padding_left="0.7rem",
                # The one quote rule on the card. Everything without it came off the feed.
                border_left=f"2px solid {BRASS}",
            ),
        ),
        rx.vstack(
            rx.hstack(
                rx.icon("crosshair", size=12, color=QUIET, flex_shrink="0"),
                rx.text(PROVENANCE, color=QUIET, size="1", line_height="1.45"),
                spacing="2",
                align="start",
                width="100%",
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
                color=BRASS,
                _hover={"text_decoration": "underline"},
            ),
            spacing="2",
            align="start",
            width="100%",
            # Pinned to the bottom whatever the title and the rationale did above it.
            margin_top="auto",
            padding_top="0.7rem",
            border_top=f"1px solid {RULE}",
        ),
        spacing="3",
        align="start",
        width="100%",
        height="100%",
        padding="0.75rem 0.9rem 0.9rem",
        background=CARD,
        border=f"1px solid {EDGE}",
        border_radius=RADIUS,
        box_shadow=SHADOW_XS,
        transition=f"box-shadow 180ms {EASE}, border-color 180ms {EASE}",
        _hover={"border_color": BRASS, "box_shadow": SHADOW_MD},
    )


def summary() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            _eyebrow("Lo que te recomiendo"),
            rx.hstack(
                _figure(State.item_count, "1.15rem"),
                rx.text(
                    rx.cond(State.item_count == 1, "producto", "productos"),
                    color=QUIET,
                    size="2",
                ),
                spacing="2",
                align="baseline",
            ),
            spacing="2",
            align="start",
        ),
        rx.spacer(),
        rx.vstack(
            _eyebrow("Total"),
            _figure(State.total_display, "1.45rem"),
            spacing="2",
            align="end",
        ),
        width="100%",
        align="end",
        padding_bottom="0.7rem",
        border_bottom=f"1px solid {RULE}",
    )


def kit() -> rx.Component:
    return rx.cond(
        State.has_cards,
        rx.vstack(
            summary(),
            rx.grid(
                rx.foreach(State.cards, card),
                # Two columns and no more. `lg` is where the audit rail moves in beside
                # this column, and a third card there would be asked to share what is left
                # of a 1024px viewport.
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing="3",
                width="100%",
            ),
            spacing="4",
            align="start",
            width="100%",
        ),
    )
