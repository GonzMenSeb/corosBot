"""Brújula's entrypoint: one route, and the gate inside it.

`rxconfig.py`'s `app_module_import` points here, and Reflex requires the module-level `app`.

**The gate is a branch, not a route.** A `/gate` page would be a URL that renders the shell
without the password check ever running: `on_load` is per page and `unlocked` is what the
shell branches on, so a second route is a second door. There is one page, and `index()` is
`rx.cond(State.unlocked, shell, gate)`.

Everything on screen comes off `State` — prices already formatted, requirement and check
names already in Spanish, product fields already rebuilt from the catalogue. Nothing here
reads the model's prose except the chat bubbles, which are the one place it belongs.

Colour comes from the Radix scale the theme selects (`var(--gray-11)`, `var(--red-11)`, …),
never from a literal here. Brújula's measured tokens, mark and components land in
`brujula/ui/`, and this file composes them.
"""

from __future__ import annotations

import reflex as rx

from brujula.state import ChatMessage, CheckRow, ProductCard, State, TraceRow

TITLE = "Brújula — asesor de equipos COROS"
DESCRIPTION = (
    "Cuéntale para qué entrenas. Brújula lee el catálogo de COROS Colombia, verifica cada "
    "dato contra él y te dice qué comprar — o que no compres nada."
)
TAGLINE = "Asesor de equipos COROS"

EXAMPLES = (
    (
        "footprints",
        "Trail largo",
        "Corro trail, unas 60 km al mes, y quiero un reloj que aguante una salida de 12 horas",
    ),
    ("watch", "Correa de repuesto", "¿Qué correa le sirve a mi APEX 2 Pro?"),
    ("bike", "Potencia en la bici", "Quiero medir potencia en la bici, con 1.500.000 de presupuesto"),
)

CARD = {
    "padding": "1rem",
    "background": "var(--gray-1)",
    "border": "1px solid var(--gray-5)",
    "border_radius": "12px",
}


def _eyebrow(text: str, tone: str = "gray") -> rx.Component:
    return rx.text(
        text,
        size="1",
        weight="bold",
        letter_spacing="0.09em",
        text_transform="uppercase",
        color=f"var(--{tone}-11)",
    )


def _bullets(title: str, values: rx.Var, tone: str = "gray") -> rx.Component:
    return rx.cond(
        values.length() > 0,
        rx.vstack(
            _eyebrow(title, tone),
            rx.foreach(
                values,
                lambda value: rx.hstack(
                    rx.box(
                        width="4px",
                        height="4px",
                        margin_top="0.55rem",
                        border_radius="50%",
                        background=f"var(--{tone}-9)",
                        flex_shrink="0",
                    ),
                    rx.text(value, size="2", line_height="1.6"),
                    spacing="2",
                    align="start",
                    width="100%",
                ),
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
    )


# ── the conversation ──────────────────────────────────────────────────────────


def _bubble(message: ChatMessage) -> rx.Component:
    mine = message.role == "user"
    return rx.box(
        rx.text(message.content, size="3", line_height="1.7", white_space="pre-wrap"),
        max_width="46rem",
        padding="0.7rem 0.95rem",
        border_radius="12px",
        align_self=rx.cond(mine, "flex-end", "flex-start"),
        background=rx.cond(mine, "var(--accent-3)", "var(--gray-2)"),
        border=rx.cond(mine, "1px solid var(--accent-5)", "1px solid var(--gray-5)"),
    )


def _thinking() -> rx.Component:
    return rx.cond(
        State.is_thinking,
        rx.hstack(
            rx.spinner(size="2"),
            rx.text(
                State.status,
                size="2",
                color=rx.cond(State.throttled, "var(--amber-11)", "var(--gray-11)"),
            ),
            spacing="2",
            align="center",
            padding_left="0.2rem",
        ),
    )


def _opening() -> rx.Component:
    return rx.vstack(
        rx.heading("¿Para qué entrenas?", size="6", weight="bold"),
        rx.text(
            "Cuéntame qué haces y con qué. Leo el catálogo de COROS Colombia, verifico cada "
            "dato contra él y te digo qué comprar — o que no compres nada.",
            size="3",
            color="var(--gray-11)",
            line_height="1.7",
            max_width="46rem",
        ),
        rx.vstack(
            *(
                rx.button(
                    rx.icon(icon, size=18, flex_shrink="0"),
                    rx.vstack(
                        rx.text(label, size="2", weight="bold"),
                        rx.text(prompt, size="1", color="var(--gray-11)", text_align="left"),
                        spacing="1",
                        align="start",
                    ),
                    on_click=State.send_example(prompt),
                    disabled=State.is_thinking,
                    variant="outline",
                    size="3",
                    cursor="pointer",
                    width="100%",
                    height="auto",
                    justify_content="start",
                    padding="0.8rem 0.9rem",
                    white_space="normal",
                )
                for icon, label, prompt in EXAMPLES
            ),
            spacing="2",
            width="100%",
            max_width="34rem",
        ),
        spacing="4",
        align="start",
        width="100%",
        padding_top="1.5rem",
    )


def _conversation() -> rx.Component:
    return rx.cond(
        State.messages.length() > 0,
        rx.vstack(
            rx.foreach(State.messages, _bubble),
            _thinking(),
            spacing="3",
            width="100%",
            align="stretch",
        ),
        _opening(),
    )


def _composer() -> rx.Component:
    return rx.form(
        rx.hstack(
            rx.input(
                name="message",
                placeholder="Cuéntame para qué entrenas…",
                aria_label="Cuéntame para qué entrenas",
                size="3",
                width="100%",
                disabled=State.is_thinking,
                background="transparent",
                box_shadow="none",
            ),
            rx.button(
                rx.icon("send-horizontal", size=17),
                # The word hides below md, which leaves an unnamed icon button on exactly
                # the viewport a phone uses.
                rx.text("Enviar", size="2", weight="bold", display=["none", "none", "block"]),
                aria_label="Enviar",
                type="submit",
                size="3",
                disabled=State.is_thinking,
                cursor="pointer",
                flex_shrink="0",
            ),
            spacing="2",
            width="100%",
            align="center",
        ),
        on_submit=State.send_message,
        reset_on_submit=True,
        width="100%",
        padding="0.4rem 0.4rem 0.4rem 0.55rem",
        background="var(--gray-1)",
        border="1px solid var(--gray-6)",
        border_radius="12px",
        _focus_within={"border_color": "var(--accent-8)"},
    )


# ── what the turn produced ────────────────────────────────────────────────────


def _error() -> rx.Component:
    return rx.cond(
        State.error != "",
        rx.callout(State.error, icon="triangle-alert", color_scheme="red", size="1", width="100%"),
    )


def _questions() -> rx.Component:
    return rx.cond(
        State.has_questions,
        rx.box(
            _bullets("Para poder responderte", State.questions, "accent"),
            width="100%",
            **CARD,
        ),
    )


def _refusal() -> rx.Component:
    """"No compres nada" and "no se vende en Colombia" are answers, not failures to answer,
    so they get a surface of their own instead of reading as a paragraph."""
    return rx.cond(
        State.is_refusal,
        rx.vstack(
            _eyebrow("Sin recomendación", "red"),
            _bullets("COROS Colombia no vende", State.unavailable, "red"),
            _bullets("Con estas salvedades", State.caveats, "gray"),
            spacing="3",
            align="start",
            width="100%",
            padding="1rem",
            background="var(--red-2)",
            border="1px solid var(--red-6)",
            border_radius="12px",
        ),
    )


def _card(card: ProductCard) -> rx.Component:
    return rx.vstack(
        rx.cond(
            card.image_url != "",
            rx.image(
                src=card.image_url,
                alt=card.title,
                width="100%",
                height="9rem",
                object_fit="contain",
            ),
        ),
        rx.link(
            card.title,
            href=card.product_url,
            is_external=True,
            size="3",
            weight="bold",
            line_height="1.4",
        ),
        rx.text(card.price_display, size="4", weight="bold"),
        rx.cond(
            card.rationale != "",
            rx.text(card.rationale, size="2", color="var(--gray-11)", line_height="1.6"),
        ),
        rx.hstack(
            rx.foreach(card.satisfies, lambda key: rx.badge(key, size="1", variant="soft")),
            spacing="1",
            wrap="wrap",
        ),
        spacing="2",
        align="start",
        width="100%",
        **CARD,
    )


def _kit() -> rx.Component:
    return rx.cond(
        State.has_cards,
        rx.vstack(
            rx.hstack(
                _eyebrow("Lo que te recomiendo"),
                rx.spacer(),
                rx.badge(State.total_display, size="2", variant="soft"),
                width="100%",
                align="center",
            ),
            rx.grid(
                rx.foreach(State.cards, _card),
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing="3",
                width="100%",
            ),
            _bullets("Con estas salvedades", State.caveats, "gray"),
            spacing="3",
            width="100%",
            align="start",
        ),
    )


def _check(check: CheckRow) -> rx.Component:
    return rx.hstack(
        rx.icon(
            rx.cond(check.outcome == "pass", "check", rx.cond(check.outcome == "fail", "x", "minus")),
            size=14,
            color=rx.cond(
                check.outcome == "pass",
                "var(--green-11)",
                rx.cond(check.outcome == "fail", "var(--red-11)", "var(--gray-9)"),
            ),
            flex_shrink="0",
        ),
        rx.text(check.label, size="2"),
        rx.spacer(),
        rx.text(check.confidence, size="1", color="var(--gray-10)"),
        spacing="2",
        align="center",
        width="100%",
    )


def _evidence() -> rx.Component:
    return rx.cond(
        State.has_evidence,
        rx.vstack(
            rx.hstack(
                rx.icon(
                    rx.cond(State.evidence_accepted, "shield-check", "shield-alert"),
                    size=15,
                    color=rx.cond(State.blocked, "var(--red-11)", "var(--green-11)"),
                    flex_shrink="0",
                ),
                _eyebrow("Verificación"),
                rx.spacer(),
                rx.badge(State.checks_summary, size="1", variant="soft"),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.vstack(rx.foreach(State.checks, _check), spacing="1", width="100%"),
            _bullets("No pude confirmar", State.blocking, "red"),
            spacing="3",
            align="start",
            width="100%",
            **CARD,
        ),
    )


# ── the audit rail ────────────────────────────────────────────────────────────


def _trace_row(row: TraceRow) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.code(row.event, size="1", variant="ghost", color="var(--gray-3)"),
            rx.spacer(),
            rx.text(row.level, size="1", color="var(--gray-8)"),
            spacing="2",
            align="center",
            width="100%",
        ),
        rx.cond(
            row.summary != "",
            rx.text(
                row.summary,
                size="1",
                color="var(--gray-7)",
                line_height="1.55",
                word_break="break-word",
            ),
        ),
        spacing="1",
        align="start",
        width="100%",
        padding_bottom="0.5rem",
        border_bottom="1px solid var(--gray-11)",
    )


def _trace_panel() -> rx.Component:
    return rx.cond(
        State.show_trace,
        rx.vstack(
            rx.hstack(
                rx.icon("scroll-text", size=15, color="var(--gray-4)", flex_shrink="0"),
                rx.text(
                    "Registro",
                    size="1",
                    weight="bold",
                    letter_spacing="0.09em",
                    text_transform="uppercase",
                    color="var(--gray-4)",
                ),
                rx.spacer(),
                rx.button(
                    rx.icon("panel-right-close", size=15),
                    on_click=State.toggle_trace,
                    aria_label="Cerrar el registro",
                    variant="ghost",
                    size="1",
                    cursor="pointer",
                    color="var(--gray-6)",
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.cond(
                State.trace.length() > 0,
                rx.scroll_area(
                    rx.vstack(rx.foreach(State.trace, _trace_row), spacing="2", width="100%"),
                    type="auto",
                    scrollbars="vertical",
                    height="100%",
                ),
                rx.text(
                    "Cada paso del turno queda acá: lo que le pregunté al catálogo, cada "
                    "comprobación y lo que no pude verificar.",
                    size="1",
                    color="var(--gray-8)",
                    line_height="1.65",
                ),
            ),
            spacing="3",
            align="start",
            flex_shrink="0",
            width=rx.breakpoints(initial="100%", lg="24rem"),
            height=rx.breakpoints(initial="20rem", lg="100vh"),
            position=rx.breakpoints(initial="static", lg="sticky"),
            top="0",
            padding="1rem",
            background="var(--gray-12)",
        ),
    )


# ── the two branches ──────────────────────────────────────────────────────────


def _mark(size: int) -> rx.Component:
    return rx.icon("compass", size=size, color="var(--accent-11)", flex_shrink="0")


def _header() -> rx.Component:
    return rx.hstack(
        _mark(26),
        rx.vstack(
            rx.heading("Brújula", as_="h1", size="5", weight="bold"),
            rx.text(TAGLINE, size="1", color="var(--gray-11)"),
            spacing="0",
            align="start",
        ),
        rx.spacer(),
        rx.button(
            rx.icon("scroll-text", size=15),
            rx.text("Registro", size="2", display=["none", "none", "block"]),
            on_click=State.toggle_trace,
            aria_label="Mostrar u ocultar el registro",
            variant="soft",
            size="2",
            cursor="pointer",
        ),
        rx.button(
            rx.icon("rotate-ccw", size=15),
            rx.text("Empezar de nuevo", size="2", display=["none", "none", "block"]),
            on_click=State.clear,
            aria_label="Empezar de nuevo",
            variant="outline",
            size="2",
            cursor="pointer",
        ),
        role="banner",
        width="100%",
        align="center",
        spacing="3",
        padding=["0.7rem 1rem", "0.7rem 1rem", "0.85rem 1.4rem"],
        background="var(--gray-1)",
        border_bottom="1px solid var(--gray-5)",
        position="sticky",
        top="0",
        z_index="20",
    )


def _skip_link() -> rx.Component:
    """First tab stop. Without it a keyboard user walks the header and, once a kit is up,
    every card link before reaching the composer."""
    return rx.link(
        "Ir a la conversación",
        href="#brujula-main",
        position="absolute",
        left="-9999px",
        top="0",
        padding="0.5rem 0.75rem",
        background="var(--gray-1)",
        border="1px solid var(--accent-8)",
        border_radius="8px",
        _focus={"left": "0.75rem", "top": "0.75rem", "z_index": "50"},
    )


def _column() -> rx.Component:
    return rx.vstack(
        _error(),
        _conversation(),
        _questions(),
        _refusal(),
        _kit(),
        _evidence(),
        # Sticky, not last-in-column: with a kit on screen the column runs several thousand
        # pixels, and the reply to "¿de qué tamaño es tu caja?" sits below all of it.
        rx.box(
            _composer(),
            position="sticky",
            bottom="0",
            z_index="10",
            width="100%",
            padding_top="0.9rem",
            padding_bottom="0.6rem",
            background="linear-gradient(180deg, transparent 0%, var(--gray-2) 45%)",
        ),
        spacing="4",
        width="100%",
        max_width="58rem",
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
                id="brujula-main",
                role="main",
                flex="1",
                min_width="0",
                width="100%",
                justify="center",
            ),
            _trace_panel(),
            direction=rx.breakpoints(initial="column", lg="row"),
            width="100%",
            align="start",
            gap="0",
        ),
        width="100%",
        min_height="100vh",
        background="linear-gradient(180deg, var(--gray-1) 0, var(--gray-2) 220px)",
        background_attachment="fixed",
    )


def _gate_field() -> rx.Component:
    return rx.hstack(
        rx.icon("lock-keyhole", size=17, color="var(--gray-9)", flex_shrink="0"),
        rx.input(
            name="password",
            type=rx.cond(State.gate_reveal, "text", "password"),
            placeholder="Contraseña",
            aria_label="Contraseña",
            auto_focus=True,
            disabled=State.gate_busy,
            size="3",
            width="100%",
            background="transparent",
            # Radix draws the field's own inset ring, which reads as a second border inside
            # the dock. The dock's _focus_within is the focus signal.
            box_shadow="none",
        ),
        rx.button(
            rx.icon(rx.cond(State.gate_reveal, "eye-off", "eye"), size=16),
            on_click=State.toggle_reveal,
            # A reveal toggle inside a form submits it unless it opts out.
            type="button",
            aria_label="Mostrar u ocultar la contraseña",
            variant="ghost",
            size="2",
            cursor="pointer",
            flex_shrink="0",
        ),
        spacing="2",
        width="100%",
        align="center",
        padding="0.4rem 0.5rem 0.4rem 0.75rem",
        background="var(--gray-1)",
        border=rx.cond(
            State.gate_error != "", "1px solid var(--red-8)", "1px solid var(--gray-6)"
        ),
        border_radius="12px",
        _focus_within={"border_color": "var(--accent-8)"},
    )


def _gate() -> rx.Component:
    return rx.center(
        rx.form(
            rx.vstack(
                _mark(40),
                rx.vstack(
                    rx.heading("Brújula", as_="h1", size="7", weight="bold"),
                    _eyebrow(TAGLINE),
                    spacing="2",
                    align="center",
                ),
                rx.text(
                    "Brújula consulta el catálogo real de COROS Colombia, así que corre "
                    "detrás de una contraseña. Escríbela para entrar.",
                    size="2",
                    color="var(--gray-11)",
                    text_align="center",
                    line_height="1.65",
                    max_width="38ch",
                ),
                rx.vstack(
                    _gate_field(),
                    rx.cond(
                        State.gate_error != "",
                        rx.hstack(
                            rx.icon("circle-alert", size=14, color="var(--red-11)", flex_shrink="0"),
                            rx.text(State.gate_error, size="2", color="var(--red-11)", weight="medium"),
                            spacing="2",
                            align="center",
                            width="100%",
                        ),
                    ),
                    rx.button(
                        rx.cond(
                            State.gate_busy,
                            rx.hstack(
                                rx.spinner(size="2"),
                                rx.text("Comprobando…", size="2", weight="bold"),
                                spacing="2",
                                align="center",
                            ),
                            rx.hstack(
                                rx.text("Entrar", size="2", weight="bold"),
                                rx.icon("arrow-right", size=16),
                                spacing="2",
                                align="center",
                            ),
                        ),
                        type="submit",
                        size="3",
                        disabled=State.gate_busy,
                        cursor="pointer",
                        width="100%",
                    ),
                    spacing="3",
                    width="100%",
                ),
                spacing="5",
                align="center",
                width="100%",
            ),
            on_submit=State.unlock,
            reset_on_submit=True,
            width="100%",
            max_width="26rem",
            padding=["1.75rem 1.35rem", "2.25rem 2rem", "2.5rem 2.25rem"],
            background="var(--gray-1)",
            border="1px solid var(--gray-5)",
            border_radius="16px",
        ),
        width="100%",
        min_height="100vh",
        padding="1.25rem",
        background="linear-gradient(168deg, var(--gray-2) 0%, var(--accent-2) 100%)",
        background_attachment="fixed",
    )


def index() -> rx.Component:
    return rx.cond(State.unlocked, _shell(), _gate())


app = rx.App(
    theme=rx.theme(
        appearance="light",
        # Warm paper over the COROS monochrome, and red left free for the honest refusal.
        # The measured tokens land in brujula/ui/theme.py.
        accent_color="bronze",
        gray_color="sand",
        radius="small",
        scaling="100%",
    ),
    style={"background": "var(--gray-2)"},
)
app.add_page(
    index,
    route="/",
    title=TITLE,
    description=DESCRIPTION,
    on_load=State.on_page_load,
)
