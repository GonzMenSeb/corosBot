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
are composed inline for now and move into `huella/ui/*` as that lane lands; `ui/theme.py` is
already the only source of a colour in this file, so nothing here has to change when they do.
`rx.theme` is handed the Radix scales the theme picked, because `rxconfig.py` cannot read that
file — `get_config()` imports it with `sys.path` cut to its own directory.
"""

from __future__ import annotations

import asyncio
import contextlib

import reflex as rx
from starlette.applications import Starlette

from huella import oauth, privacy
from huella.state import State
from huella.ui.theme import (
    AMBER_INK,
    APPEARANCE,
    CONTENT_W,
    DASH,
    EDGE,
    FLAG_INK,
    FLAG_WELL,
    FONT,
    FONT_HREF,
    GRID,
    INK,
    INK_2,
    MONO,
    ON_FILL,
    RADIUS,
    RADIUS_LG,
    RADIUS_PILL,
    RADIUS_SM,
    RADIX_ACCENT,
    RADIX_GRAY,
    RADIX_RADIUS,
    RADIX_SCALING,
    RAIL_W,
    READOUT,
    SHADOW_MD,
    SHEET,
    SHEET_EDGE,
    SHEET_QUIET,
    SHEET_TRACE,
    STRAVA,
    SUB,
    SUCCESS_INK,
    TRACE,
    TRACE_DEEP,
    TRACK_DISPLAY,
    TRACK_EYEBROW,
    TRACK_READOUT,
)

NAME = "Huella"
TAGLINE = "Lo que tu entrenamiento ya demostró"
TITLE = f"{NAME} — {TAGLINE.lower()}"
DESCRIPTION = (
    "Conecta tu Strava y Huella deriva de tus actividades reales qué del catálogo de COROS "
    "Colombia te sirve — diciendo siempre en qué se apoya y qué tan poco."
)

MAIN_ID = "huella-conversacion"
RAIL_ID = "huella-registro"
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


def _eyebrow(text: str) -> rx.Component:
    return rx.text(
        text,
        size="1",
        weight="bold",
        color=SUB,
        letter_spacing=TRACK_EYEBROW,
        text_transform="uppercase",
    )


def _panel(*children: rx.Component, **props) -> rx.Component:
    return rx.vstack(
        *children,
        width="100%",
        align="start",
        spacing="2",
        padding="0.9rem 1rem",
        background=INK,
        border=f"1px solid {GRID}",
        border_radius=RADIUS_LG,
        **props,
    )


def _chip(label: rx.Var | str) -> rx.Component:
    return rx.text(
        label,
        size="1",
        color=AMBER_INK,
        padding="0.15rem 0.55rem",
        border_radius=RADIUS_PILL,
        background=INK_2,
    )


def _notice() -> rx.Component:
    return rx.fragment(
        rx.cond(
            State.strava_error != "",
            rx.hstack(
                rx.icon("triangle_alert", size=16, color=FLAG_INK, flex_shrink="0"),
                rx.text(State.strava_error, color=FLAG_INK, size="2", line_height="1.6"),
                role="alert",
                spacing="2",
                align="start",
                width="100%",
                padding="0.7rem 0.85rem",
                background=FLAG_WELL,
                border_radius=RADIUS_SM,
            ),
        ),
        rx.cond(
            State.strava_notice != "",
            rx.hstack(
                rx.icon("circle_check", size=16, color=SUCCESS_INK, flex_shrink="0"),
                rx.text(State.strava_notice, color=SUCCESS_INK, size="2", line_height="1.6"),
                role="status",
                spacing="2",
                align="start",
                width="100%",
                padding="0.7rem 0.85rem",
                background=INK_2,
                border_radius=RADIUS_SM,
            ),
        ),
        rx.cond(
            State.error != "",
            rx.hstack(
                rx.icon("triangle_alert", size=16, color=FLAG_INK, flex_shrink="0"),
                rx.text(State.error, color=FLAG_INK, size="2", line_height="1.6"),
                role="alert",
                spacing="2",
                align="start",
                width="100%",
                padding="0.7rem 0.85rem",
                background=FLAG_WELL,
                border_radius=RADIUS_SM,
            ),
        ),
    )


def _strava() -> rx.Component:
    """A declared LIGHT surface, and that is a theme rule rather than a taste: Strava's
    `#FC4C02` may only appear unmodified on a sheet, where it is legible without being
    darkened. The official "Connect with Strava" asset belongs here and is not shipped yet —
    until it is, the orange is the panel's own edge and never a recoloured mark."""
    return rx.vstack(
        _eyebrow("Strava"),
        rx.cond(
            State.connected,
            rx.vstack(
                rx.text(
                    "Cuenta conectada. Leo tus actividades, nunca tus datos personales.",
                    size="2",
                    color=INK,
                    line_height="1.6",
                ),
                rx.hstack(
                    rx.text("huella del par", size="1", color=SHEET_QUIET),
                    rx.text(
                        State.token_fingerprint,
                        size="1",
                        color=SHEET_QUIET,
                        font_family=MONO,
                        letter_spacing=TRACK_READOUT,
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.hstack(
                    rx.button(
                        "Desconectar",
                        on_click=State.disconnect_strava,
                        size="2",
                        cursor="pointer",
                        color=ON_FILL,
                        background=SHEET_TRACE,
                    ),
                    rx.button(
                        "Borrar todo lo mío",
                        on_click=State.forget_me,
                        size="2",
                        cursor="pointer",
                        color=SHEET_TRACE,
                        background=SHEET,
                        border=f"1px solid {SHEET_EDGE}",
                    ),
                    spacing="2",
                ),
                spacing="2",
                align="start",
                width="100%",
            ),
            rx.vstack(
                rx.text(
                    "Sin Strava conectado. Huella funciona igual — te pregunta en vez de "
                    "deducir, y lo dice.",
                    size="2",
                    color=INK,
                    line_height="1.6",
                ),
                rx.cond(
                    State.strava_configured,
                    rx.button(
                        "Conectar con Strava",
                        on_click=State.connect_strava,
                        size="2",
                        cursor="pointer",
                        color=ON_FILL,
                        background=SHEET_TRACE,
                    ),
                    rx.text(
                        "Esta instancia no tiene credenciales de Strava configuradas.",
                        size="1",
                        color=SHEET_QUIET,
                    ),
                ),
                spacing="2",
                align="start",
                width="100%",
            ),
        ),
        width="100%",
        align="start",
        spacing="2",
        padding="0.9rem 1rem",
        background=SHEET,
        border_left=f"3px solid {STRAVA}",
        border_radius=RADIUS,
    )


def _uncertainty() -> rx.Component:
    """D2 on screen. The statement is `UncertaintyVerdict.statement`, regenerated and
    compared by the verdict's own validator, so nothing a model wrote reaches this box."""
    return rx.cond(
        State.uncertainty_statement != "",
        _panel(
            rx.hstack(
                _eyebrow("En qué me apoyo"),
                rx.spacer(),
                rx.text(State.confidence_label, size="1", color=SUB),
                width="100%",
                align="center",
            ),
            rx.text(State.uncertainty_statement, size="2", color=READOUT, line_height="1.65"),
            rx.cond(
                State.uncertainty_flags.length() > 0,
                rx.hstack(
                    rx.foreach(State.uncertainty_flags, _chip),
                    spacing="2",
                    wrap="wrap",
                ),
            ),
            rx.cond(
                State.cold_start,
                rx.text(State.cold_start_reason, size="1", color=AMBER_INK, line_height="1.6"),
            ),
            rx.cond(
                State.mode_label != "",
                rx.text(State.mode_label, size="1", color=SUB, line_height="1.6"),
            ),
            border_left=f"3px solid {TRACE}",
        ),
    )


def _requirements() -> rx.Component:
    return rx.cond(
        State.has_requirements,
        _panel(
            _eyebrow("Lo que entendí de tu entrenamiento"),
            rx.foreach(
                State.requirements,
                lambda row: rx.hstack(
                    rx.text(row.label, size="2", color=SUB, width="11rem", flex_shrink="0"),
                    rx.text(
                        row.value,
                        size="2",
                        color=READOUT,
                        font_family=MONO,
                        letter_spacing=TRACK_READOUT,
                    ),
                    rx.spacer(),
                    # The provenance, not a tone. `derived` comes off the Requirement.
                    rx.text(
                        rx.cond(row.derived, "de tu historial", "lo dijiste tú"),
                        size="1",
                        color=rx.cond(row.derived, TRACE, AMBER_INK),
                    ),
                    width="100%",
                    align="center",
                    spacing="2",
                ),
            ),
        ),
    )


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


def _questions() -> rx.Component:
    return rx.cond(
        State.has_questions,
        _panel(
            _eyebrow("Lo que me falta saber"),
            rx.foreach(
                State.questions,
                lambda question: rx.text(question, size="2", color=READOUT, line_height="1.65"),
            ),
        ),
    )


def _kit() -> rx.Component:
    return rx.cond(
        State.has_cards,
        _panel(
            rx.hstack(
                _eyebrow("Lo que te sirve"),
                rx.spacer(),
                rx.text(
                    State.total_display,
                    size="2",
                    color=READOUT,
                    font_family=MONO,
                    letter_spacing=TRACK_READOUT,
                ),
                width="100%",
                align="center",
            ),
            rx.foreach(
                State.cards,
                lambda card: rx.vstack(
                    rx.link(
                        card.title,
                        href=card.product_url,
                        is_external=True,
                        size="3",
                        color=TRACE,
                    ),
                    rx.text(
                        card.price_display,
                        size="2",
                        color=READOUT,
                        font_family=MONO,
                        letter_spacing=TRACK_READOUT,
                    ),
                    rx.text(card.rationale, size="2", color=SUB, line_height="1.6"),
                    width="100%",
                    align="start",
                    spacing="1",
                    padding="0.6rem 0.7rem",
                    background=INK_2,
                    border_radius=RADIUS_SM,
                ),
            ),
        ),
    )


def _verdict() -> rx.Component:
    return rx.cond(
        State.is_refusal,
        _panel(
            _eyebrow("La respuesta honesta"),
            rx.foreach(
                State.unavailable,
                lambda name: rx.text(
                    name, size="2", color=FLAG_INK, line_height="1.6"
                ),
            ),
            rx.foreach(
                State.caveats,
                lambda caveat: rx.text(caveat, size="2", color=SUB, line_height="1.6"),
            ),
            border_left=f"3px solid {FLAG_INK}",
        ),
    )


def _evidence() -> rx.Component:
    return rx.cond(
        State.has_evidence,
        _panel(
            rx.hstack(
                _eyebrow("Lo que verifiqué"),
                rx.spacer(),
                rx.text(
                    State.checks_summary,
                    size="1",
                    color=rx.cond(State.blocked, FLAG_INK, SUCCESS_INK),
                    font_family=MONO,
                ),
                width="100%",
                align="center",
            ),
            rx.foreach(
                State.checks,
                lambda check: rx.hstack(
                    rx.text(
                        check.outcome,
                        size="1",
                        color=rx.cond(check.outcome == "pass", SUCCESS_INK, AMBER_INK),
                        font_family=MONO,
                        width="5rem",
                        flex_shrink="0",
                    ),
                    rx.text(check.label, size="2", color=READOUT),
                    width="100%",
                    align="center",
                    spacing="2",
                ),
            ),
        ),
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


def _rail() -> rx.Component:
    return rx.cond(
        State.show_trace,
        rx.box(
            _eyebrow("Registro"),
            rx.foreach(
                State.trace,
                lambda row: rx.vstack(
                    rx.hstack(
                        rx.text(
                            row.event,
                            size="1",
                            color=rx.cond(row.level == "error", FLAG_INK, TRACE),
                            font_family=MONO,
                        ),
                        rx.spacer(),
                        rx.text(row.seq, size="1", color=SUB, font_family=MONO),
                        width="100%",
                        align="center",
                    ),
                    rx.text(row.summary, size="1", color=SUB, white_space="pre-wrap"),
                    width="100%",
                    align="start",
                    spacing="1",
                    padding="0.4rem 0.5rem",
                    border_bottom=f"1px solid {GRID}",
                ),
            ),
            id=RAIL_ID,
            role="log",
            width=["100%", "100%", RAIL_W],
            flex_shrink="0",
            max_height=f"calc(100vh - {HEADER_H})",
            overflow_y="auto",
            padding="1rem 0.9rem",
            background=INK,
            border_left=f"1px solid {GRID}",
            position=["static", "static", "sticky"],
            top=HEADER_H,
        ),
    )


def _header() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.heading(
                NAME, as_="h1", size="5", color=READOUT, letter_spacing=TRACK_DISPLAY
            ),
            rx.text(TAGLINE, size="1", color=SUB),
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
            aria_controls=RAIL_ID,
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
    return rx.vstack(
        _notice(),
        _strava(),
        _uncertainty(),
        _requirements(),
        _transcript(),
        _questions(),
        _verdict(),
        _kit(),
        _evidence(),
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
            _rail(),
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
            rx.heading(NAME, as_="h1", size="7", color=READOUT, letter_spacing=TRACK_DISPLAY),
            rx.text(TAGLINE, size="2", color=SUB),
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
    stylesheets=[FONT_HREF],
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
