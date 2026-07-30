"""The transcript: two registers, and the one place a model's prose is allowed on screen.

The person speaks on cream and Brújula answers on white — `theme.SURFACES` assigns those
two surfaces exactly that way, and the pairing is why neither speaker needs a coloured
stripe or an avatar to be told apart at three metres. Only the type colours the theme
measured against each of those two surfaces are used on them: the person's words are
`BRASS_DEEP on BRASS_SOFT 8.98:1`, Brújula's are `INK on CARD 16.98:1`.

**Nothing here is markdown.** `prompts.py` asks for paragraphs and forbids lists, prices
and totals — those are `product.py`'s cards — so `white_space="pre-wrap"` renders every
template and every generated reply correctly. Reflex's `rx.markdown` pulls in `rehype-raw`,
so routing the person's own message through it would put a typed `<img onerror=…>` into the
DOM; routing only Brújula's half through it would be two renderers for one transcript.

**The waiting row has no spinner.** The mark's presence dot is the app's only ambient
animation and it already reports both things worth reporting — that a turn is running, and
whether COROS is throttling us — so a second moving element would be a second answer to
the same question. The surface under it changes with the throttle for the same reason.

The opening heading is an `h2`. It unmounts the moment the first message lands, and as an
`h1` it would take the document's only top-level heading with it; `app.py`'s header owns
the `h1` for the whole session.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import ChatMessage, State
from brujula.ui import brand
from brujula.ui.theme import (
    BRASS,
    BRASS_DEEP,
    BRASS_SOFT,
    CARD,
    EASE,
    EDGE,
    FOCUS_RING,
    FONT_DISPLAY,
    GRAPHITE,
    GRAPHITE_DEEP,
    INK,
    ON_DARK,
    QUIET,
    RADIUS,
    RADIUS_LG,
    RADIUS_SM,
    SHADOW_MD,
    SHADOW_SM,
    SHADOW_XS,
    TRACK_DISPLAY,
    TRACK_EYEBROW,
    WARN_INK,
    WARN_SOFT,
)

# Three openings somebody can click instead of typing. The third one dead-ends on purpose:
# COROS Colombia sells nothing that measures cycling power, and hearing that in one turn is
# the single most useful thing Brújula does. An example that quietly recommends the wrong
# product would be worse than no example at all.
EXAMPLES: tuple[tuple[str, str, str], ...] = (
    (
        "footprints",
        "Trail largo",
        "Corro trail, unas 60 km al mes, y quiero un reloj que aguante una salida de 12 horas",
    ),
    ("watch", "Correa de repuesto", "¿Qué correa le sirve a mi APEX 2 Pro?"),
    (
        "bike",
        "Potencia en la bici",
        "Quiero medir potencia en la bici, con 1.500.000 de presupuesto",
    ),
)

_OPENING_BLURB = (
    "Cuéntame qué haces y con qué. Leo el catálogo real de COROS Colombia, verifico cada "
    "dato contra él y te digo qué comprar — o que no compres nada."
)


def _speaker(label: str, color: str) -> rx.Component:
    return rx.text(
        label,
        color=color,
        font_size="0.62rem",
        font_weight="700",
        letter_spacing=TRACK_EYEBROW,
        line_height="1.2",
        text_transform="uppercase",
    )


def _person(message: ChatMessage) -> rx.Component:
    return rx.vstack(
        _speaker("Tú", BRASS),
        rx.text(message.content, color=BRASS_DEEP, size="3", line_height="1.7", white_space="pre-wrap"),
        spacing="2",
        align="start",
        align_self="flex-end",
        max_width="40rem",
        padding="0.7rem 0.95rem",
        background=BRASS_SOFT,
        border=f"1px solid {EDGE}",
        # Square where it meets the person: the block points at its own side of the column.
        border_radius=f"{RADIUS} {RADIUS_SM} {RADIUS} {RADIUS}",
        box_shadow=SHADOW_XS,
    )


def _brujula(message: ChatMessage) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            brand.mark(size="1.75rem", dot=False, surface=CARD),
            _speaker("Brújula", QUIET),
            spacing="2",
            align="center",
        ),
        rx.text(message.content, color=INK, size="3", line_height="1.75", white_space="pre-wrap"),
        spacing="3",
        align="start",
        align_self="flex-start",
        width="100%",
        max_width="46rem",
        padding="0.85rem 1.05rem 0.95rem",
        background=CARD,
        border=f"1px solid {EDGE}",
        border_radius=f"{RADIUS_SM} {RADIUS} {RADIUS} {RADIUS}",
        box_shadow=SHADOW_SM,
    )


def bubble(message: ChatMessage) -> rx.Component:
    return rx.cond(message.role == "user", _person(message), _brujula(message))


def _waiting(surface: str, ink: str) -> rx.Component:
    return rx.hstack(
        brand.mark(size="1.75rem", surface=surface),
        rx.text(State.status, color=ink, size="2", line_height="1.6"),
        # A turn runs for the better part of a minute and `status` changes several times
        # inside it. Without a live region that whole minute is silent.
        role="status",
        aria_live="polite",
        spacing="3",
        align="center",
        align_self="flex-start",
        max_width="46rem",
        padding="0.6rem 0.9rem",
        background=surface,
        border=f"1px solid {EDGE}",
        border_radius=RADIUS,
    )


def thinking() -> rx.Component:
    return rx.cond(
        State.is_thinking,
        # The throttle outranks the turn, the way it does in the caption and on the dot:
        # being on a partial read of the catalogue is the more important thing to be saying.
        rx.cond(State.throttled, _waiting(WARN_SOFT, WARN_INK), _waiting(CARD, QUIET)),
    )


def composer() -> rx.Component:
    return rx.form(
        rx.hstack(
            rx.input(
                name="message",
                placeholder="Cuéntame para qué entrenas…",
                # A placeholder is not a label: it disappears the moment you type, and a
                # screen reader announces the field as unnamed.
                aria_label="Cuéntame para qué entrenas",
                size="3",
                width="100%",
                disabled=State.is_thinking,
                color=INK,
                font_size="0.95rem",
                # The dock draws the field. Radix's own inset ring inside it reads as a
                # second border, and its background hides the dock's surface.
                background="transparent",
                box_shadow="none",
            ),
            rx.button(
                rx.icon("send-horizontal", size=17),
                # The word hides below md, which would leave an unnamed icon button on
                # exactly the viewport a phone uses.
                rx.text("Enviar", size="2", weight="bold", display=["none", "none", "block"]),
                aria_label="Enviar",
                type="submit",
                size="3",
                disabled=State.is_thinking,
                cursor="pointer",
                flex_shrink="0",
                color=ON_DARK,
                background=GRAPHITE,
                _hover={"background": GRAPHITE_DEEP},
            ),
            spacing="2",
            width="100%",
            align="center",
        ),
        on_submit=State.send_message,
        reset_on_submit=True,
        width="100%",
        padding="0.4rem 0.4rem 0.4rem 0.55rem",
        background=CARD,
        border=f"1px solid {EDGE}",
        border_radius=RADIUS_LG,
        box_shadow=SHADOW_SM,
        _focus_within={"border_color": BRASS, "box_shadow": FOCUS_RING},
    )


def _example(icon: str, label: str, prompt: str) -> rx.Component:
    return rx.button(
        rx.icon(icon, size=17, color=BRASS, flex_shrink="0"),
        rx.vstack(
            rx.text(label, color=INK, size="2", weight="bold"),
            rx.text(prompt, color=QUIET, size="1", line_height="1.5", text_align="left"),
            spacing="1",
            align="start",
        ),
        on_click=State.send_example(prompt),
        disabled=State.is_thinking,
        # A button, not a clickable box: Enter and Space have to work.
        cursor="pointer",
        width="100%",
        height="auto",
        justify_content="start",
        align_items="start",
        gap="0.7rem",
        padding="0.8rem 0.9rem",
        white_space="normal",
        text_align="left",
        background=CARD,
        border=f"1px solid {EDGE}",
        border_radius=RADIUS,
        box_shadow=SHADOW_XS,
        transition=f"box-shadow 180ms {EASE}, transform 180ms {EASE}, border-color 180ms {EASE}",
        # Only the edge and the depth move. Swapping the surface on hover would put INK and
        # QUIET onto a colour the theme never measured them against.
        _hover={"border_color": BRASS, "box_shadow": SHADOW_MD, "transform": "translateY(-1px)"},
    )


def opening() -> rx.Component:
    return rx.vstack(
        rx.heading(
            "¿Para qué entrenas?",
            as_="h2",
            color=INK,
            font_family=FONT_DISPLAY,
            font_size=["1.9rem", "2.25rem", "2.6rem"],
            font_weight="700",
            letter_spacing=TRACK_DISPLAY,
            line_height="1.05",
        ),
        rx.text(_OPENING_BLURB, color=QUIET, size="3", line_height="1.7", max_width="42rem"),
        rx.vstack(
            *(_example(icon, label, prompt) for icon, label, prompt in EXAMPLES),
            spacing="2",
            width="100%",
            max_width="34rem",
        ),
        spacing="4",
        align="start",
        width="100%",
        padding_top="1.25rem",
    )


def transcript() -> rx.Component:
    return rx.cond(
        State.messages.length() > 0,
        rx.vstack(
            rx.vstack(
                rx.foreach(State.messages, bubble),
                # An ordered running transcript, not an announcement: `log` must not
                # interrupt whatever the waiting row's status region is in the middle of
                # saying. The waiting row stays OUTSIDE it — a live region nested in a live
                # region is one event announced twice.
                role="log",
                spacing="3",
                width="100%",
                align="stretch",
            ),
            thinking(),
            spacing="3",
            width="100%",
            align="stretch",
        ),
        opening(),
    )
