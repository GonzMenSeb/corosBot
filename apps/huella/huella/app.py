"""Huella's entrypoint: one route, the gate inside it, and the OAuth callback beside it.

`rxconfig.py`'s `app_module_import` points here, and Reflex requires the module-level `app`.

**Two serving surfaces, and only one of them is a page.** The athlete's whole session is
the single Reflex route below. Strava's redirect is not: it is a plain HTTP GET carrying a
`code`, and it is answered by a Starlette route on the `api_transformer` — `huella/oauth.py`
owns it, this file only wires it in. The wiring is the load-bearing part and it is measured:
`App.__call__` mounts Reflex's own router (the event channel, `/ping`, and in prod the
compiled frontend's catch-all) into our transformer as one `Mount("")`, and Starlette matches
in list order, so a route registered here outranks the static mount that would otherwise hand
Strava a 404. `scripts/spike_api_transformer.py` proved it end to end under granian on
30 Jul 2026; `tests/test_contracts.py` pins both halves against Reflex's source.

**The routes go on at construction, not after.** Both orders work — the mount happens inside
`App.__call__`, which granian invokes per worker boot — but "before `rx.App(...)`" is the
order that stays correct if Reflex ever moves the mount into the constructor, and it is the
one shape that never has to be re-argued. The failure it avoids has no symptom in any log:
a 404 on a route that plainly exists.

**The transformer's own `lifespan=` never runs**, so there is none. Reflex mounts the
transformer inside a Starlette it owns and a mounted ASGI app is never sent a lifespan scope
— the spike's marker file was never written. The sweeper below goes through
`app.register_lifespan_task`, which is the hook that does fire.

**The gate is a branch, not a route.** A `/gate` page would be a URL that renders the shell
without the password check ever running: `on_load` is per page and `unlocked` is what the
shell branches on, so a second route is a second door.

Everything on screen comes off `State` — prices already formatted, requirement and check
names already in Spanish, the uncertainty verdict already a set of values. Nothing here reads
the model's prose except the chat bubbles, which are the one place it belongs. The surfaces
now live in `huella/ui/*`; what is left here is the wiring, the conversation itself and the
gate. `rx.theme` is handed the Radix scales the theme picked, because `rxconfig.py` cannot
read that file — `get_config()` imports it with `sys.path` cut to its own directory.

**`assets/huella.css` is listed in `stylesheets=`, and that is what makes the `hu-` classes
real.** Reflex serves `assets/` at the web root. A component naming a class with no rule
behind it is a silent no-op — the thinking pip simply stops moving and nothing warns you —
so the stylesheet and the class names in `ui/*` are one contract. The same root is what
serves `/strava/…`, the vendored attribution marks.
"""

from __future__ import annotations

import asyncio
import contextlib

import reflex as rx
from starlette.applications import Starlette

from huella import oauth, privacy
from huella.state import State
from huella.ui import advice, brand, connect, trace_panel, training
from huella.ui.theme import (
    AMBER_INK,
    APPEARANCE,
    CONTENT_W,
    DASH,
    EDGE,
    FLAG_INK,
    FONT,
    FONT_HREF,
    GRID,
    INK,
    INK_2,
    RADIUS,
    RADIUS_LG,
    RADIX_ACCENT,
    RADIX_GRAY,
    RADIX_RADIUS,
    RADIX_SCALING,
    READOUT,
    SHADOW_MD,
    SUB,
    TRACE,
    TRACE_DEEP,
    TRACK_DISPLAY,
)

STYLESHEET = "/huella.css"

NAME = brand.NAME
TAGLINE = brand.TAGLINE
TITLE = f"{NAME} — {TAGLINE.lower()}"
DESCRIPTION = (
    "Conecta tu Strava y Huella deriva de tus actividades reales qué del catálogo de COROS "
    "Colombia te sirve — diciendo siempre en qué se apoya y qué tan poco."
)

MAIN_ID = "huella-conversacion"
# The rail's id belongs to the rail. The header's aria-controls points at it, and two
# spellings of one id is a control that announces it opens something that does not exist.
HEADER_H = "4.5rem"

# The sweeper's period. `privacy.sweep()` already runs on every session touch, so this is
# only for a process nobody is using: hosted for weeks, it would otherwise sit on an idle
# athlete's refresh token long past `SESSION_TTL_SECONDS` because nothing came in to
# trigger the sweep.
SWEEP_INTERVAL_SECONDS = 300

EXAMPLES = (
    "¿Qué reloj me sirve para lo que estoy entrenando?",
    "Necesito una correa nueva para mi APEX 4",
    "Corro trail dos veces por semana, ¿me falta algo?",
)


# ── the OAuth callback, registered before Reflex mounts anything ──────────────

api = Starlette(routes=list(oauth.ROUTES))


# ── the instrument ────────────────────────────────────────────────────────────


def _transcript() -> rx.Component:
    return rx.vstack(
        rx.cond(
            State.messages.length() == 0,
            rx.vstack(
                rx.heading(
                    "Cuéntame qué entrenas — o conecta Strava y lo leo.",
                    as_="h2",
                    size="5",
                    color=READOUT,
                    letter_spacing=TRACK_DISPLAY,
                ),
                rx.hstack(
                    rx.foreach(
                        list(EXAMPLES),
                        lambda text: rx.button(
                            text,
                            on_click=lambda: State.send_example(text),
                            size="2",
                            cursor="pointer",
                            color=TRACE,
                            background=INK,
                            border=f"1px solid {GRID}",
                        ),
                    ),
                    spacing="2",
                    wrap="wrap",
                ),
                spacing="3",
                align="start",
                width="100%",
            ),
        ),
        rx.foreach(
            State.messages,
            lambda message: rx.box(
                rx.text(
                    message.content,
                    size="3",
                    color=rx.cond(message.role == "user", INK, READOUT),
                    white_space="pre-wrap",
                    line_height="1.7",
                ),
                width="100%",
                padding="0.75rem 0.95rem",
                border_radius=RADIUS,
                background=rx.cond(message.role == "user", TRACE, INK),
                border=rx.cond(message.role == "user", f"1px solid {TRACE_DEEP}", f"1px solid {GRID}"),
            ),
        ),
        rx.cond(
            State.is_thinking,
            rx.hstack(
                rx.spinner(size="1"),
                rx.text(State.status, size="2", color=rx.cond(State.throttled, AMBER_INK, SUB)),
                spacing="2",
                align="center",
                role="status",
            ),
        ),
        spacing="3",
        width="100%",
        align="start",
    )


def _composer() -> rx.Component:
    return rx.form(
        rx.hstack(
            rx.input(
                name="message",
                placeholder="Cuéntame qué entrenas…",
                aria_label="Tu mensaje",
                size="3",
                flex="1",
                background=INK,
                color=READOUT,
                border=f"1px solid {EDGE}",
            ),
            rx.button(
                rx.icon("send", size=16),
                type="submit",
                aria_label="Enviar",
                size="3",
                cursor="pointer",
                disabled=State.is_thinking,
                color=INK,
                background=TRACE,
            ),
            width="100%",
            spacing="2",
            align="center",
        ),
        on_submit=State.send_message,
        reset_on_submit=True,
        width="100%",
    )


def _header() -> rx.Component:
    return rx.hstack(
        brand.mark(size="2.1rem", surface=INK),
        rx.vstack(
            brand.wordmark(size="1.35rem", as_="h1"),
            brand.credit(),
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
            color=TRACE,
            background=INK_2,
            border=f"1px solid {GRID}",
        ),
        rx.button(
            rx.icon("rotate_ccw", size=15),
            rx.text(
                "Empezar de nuevo", size="2", weight="bold", display=["none", "none", "block"]
            ),
            on_click=State.clear,
            # The words hide below md, which would otherwise leave two unnamed icon buttons
            # side by side on exactly the viewport a phone uses.
            aria_label="Empezar de nuevo",
            size="2",
            cursor="pointer",
            flex_shrink="0",
            color=SUB,
            background=INK_2,
            border=f"1px solid {GRID}",
        ),
        role="banner",
        width="100%",
        height=HEADER_H,
        align="center",
        spacing="3",
        padding=["0 1rem", "0 1rem", "0 1.4rem"],
        background=INK,
        border_bottom=f"1px solid {GRID}",
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
        color=READOUT,
        position="absolute",
        left="-9999px",
        top="0",
        padding="0.5rem 0.75rem",
        background=INK,
        border=f"1px solid {TRACE}",
        border_radius=RADIUS,
        _focus={"left": "0.75rem", "top": "0.75rem", "z_index": "50"},
    )


def _column() -> rx.Component:
    # `connect.panel()` already carries the "Powered by Strava" mark, so nothing here places
    # `connect.attribution()` a second time: two of the vendor's marks on one screen is the
    # prominence their terms are about.
    return rx.vstack(
        connect.notices(),
        connect.panel(),
        training.uncertainty(),
        training.window(),
        training.requirements(),
        _transcript(),
        advice.questions(),
        advice.verdict(),
        advice.kit(),
        advice.notes(),
        advice.evidence(),
        # Sticky, not last in the column: with a kit on screen the column runs several
        # thousand pixels, and the next question sits below all of it.
        rx.box(
            _composer(),
            position="sticky",
            bottom="0",
            z_index="10",
            width="100%",
            padding_top="1rem",
            padding_bottom="0.7rem",
            background=f"linear-gradient(180deg, transparent 0%, {DASH} 42%)",
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
        background=DASH,
    )


def _gate() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.hstack(
                brand.mark(size="2.6rem", surface=INK),
                rx.vstack(
                    brand.wordmark(size="1.8rem", as_="h1"),
                    brand.tagline(),
                    spacing="1",
                    align="start",
                ),
                spacing="3",
                align="center",
                width="100%",
            ),
            rx.form(
                rx.hstack(
                    rx.input(
                        name="password",
                        type=rx.cond(State.gate_reveal, "text", "password"),
                        placeholder="Contraseña",
                        aria_label="Contraseña",
                        size="3",
                        flex="1",
                        background=INK,
                        color=READOUT,
                        border=f"1px solid {EDGE}",
                    ),
                    rx.button(
                        rx.icon("eye", size=16),
                        type="button",
                        on_click=State.toggle_reveal,
                        aria_label="Mostrar u ocultar la contraseña",
                        size="3",
                        cursor="pointer",
                        color=SUB,
                        background=INK_2,
                    ),
                    rx.button(
                        "Entrar",
                        type="submit",
                        size="3",
                        cursor="pointer",
                        disabled=State.gate_busy,
                        color=INK,
                        background=TRACE,
                    ),
                    width="100%",
                    spacing="2",
                    align="center",
                ),
                on_submit=State.unlock,
                width="100%",
            ),
            rx.cond(
                State.gate_error != "",
                rx.text(State.gate_error, size="2", color=FLAG_INK, role="alert"),
            ),
            spacing="3",
            align="start",
            width="100%",
            max_width="26rem",
            padding="1.6rem",
            background=INK,
            border=f"1px solid {GRID}",
            border_radius=RADIUS_LG,
            box_shadow=SHADOW_MD,
        ),
        width="100%",
        min_height="100vh",
        background=DASH,
    )


def index() -> rx.Component:
    return rx.cond(State.unlocked, _shell(), _gate())


async def sweeper() -> None:
    """The only startup work Huella has, and it runs through the hook that actually fires.

    `privacy.sweep()` is driven by traffic — it runs when a session is touched — so a
    process nobody is using holds an idle athlete's refresh token indefinitely. This is what
    drops it on time. `oauth.sweep()` does the same for handles nobody redeemed.
    """
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            privacy.sweep()
            oauth.sweep()


app = rx.App(
    api_transformer=api,
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
    stylesheets=[FONT_HREF, STYLESHEET],
    style={"background": DASH, "color": READOUT, "font_family": FONT},
)
app.add_page(
    index,
    route="/",
    title=TITLE,
    description=DESCRIPTION,
    on_load=State.on_page_load,
)
app.register_lifespan_task(sweeper)
