"""The four tools Brújula may hand the model, and the dispatcher behind them.

`create_cart` and `create_checkout` ARE DELIBERATELY ABSENT and must stay absent. No
`ToolId` spells either one, so there is no token sequence the model can emit that
reaches a cart — human-in-the-loop is a fact about the tool list, not a request in a
prompt. `capability.WITHHELD` names the omission so it is auditable, and
`ucp.call_ucp()` still reaches both from a click handler.

Three design calls here diverge from DecaBot's `agent/tools.py`, and all three follow
from one measured fact: **COROS Colombia's entire catalogue is 45 products in ONE
storefront request.**

  * **Retrieval reads a per-turn snapshot; it does not navigate.** Decathlon needed
    `list_collections` → `get_collection_products` because retrieval there was
    navigation across thousands of SKUs. Here every tool answers from the one feed read
    the turn already paid for. A second storefront request per group would spend the
    harshest limiter in the system — measured at ~4 requests before an IP-level lockout
    that outlasts the conversation — to re-fetch products we are holding.
  * **A search that matches nothing is CONCLUSIVE.** Because the snapshot is the whole
    catalogue, `ToolOutcome.UNAVAILABLE` here means "we read all 43 and none matched",
    which is real evidence. That is the property `check_buy_nothing(
    retrieval_conclusive=True)` rests on, and it is why `search_products` does not call
    UCP's `search_catalog`: a UCP hit that is not in the snapshot cannot pass
    `check_provenance(candidates, catalog)` anyway, so a semantic path can only ever
    re-rank products we already have — at the cost of putting the cart surface inside
    the model's reach.
  * **The groups come from `devices.py`, not from `product_type`.** The field is empty
    on 24 of 45 products (PACE 4 included) and says `Relojes GPS` for a bike computer.
    Tags are worse: `APEX Pro` on a charger is a compatibility claim, and compatibility
    has exactly one authority. So `relojes` and `correas` are the two registry tables
    and `accesorios` is everything the registry does not name — a partition, so nothing
    is double-counted and nothing is unreachable.

Everything handed to the model is whitelisted and truncated by `_slim()`. Sanitised
`description` is NOT forwarded: it is the injection surface and an invitation to read a
spec out of marketing copy, and the specs that matter (strap widths, compatibility)
come from the registry instead.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from google.genai import types
from pydantic import BaseModel, ConfigDict, model_validator

from coros_core import devices, gemini
from coros_core.capability import SURFACES, ToolId
from coros_core.models import CatalogProduct
from coros_core.outcomes import ToolOutcome, ToolResult
from coros_core.trace import emit

MAX_PRODUCTS = 12
MAX_VARIANTS = 8
TITLE_CHARS = 140
MAX_QUERY_TOKENS = 6


class Snapshot(BaseModel):
    """The turn's one storefront read, or the reason there isn't one.

    The validators are the whole point: an OK snapshot carrying no products, or a
    failed one carrying no reason, are the two shapes that let a 429 reach a person as
    "COROS has nothing like that"."""

    model_config = ConfigDict(frozen=True)

    products: tuple[CatalogProduct, ...] = ()
    outcome: ToolOutcome = ToolOutcome.OK
    detail: str = ""

    @model_validator(mode="after")
    def a_snapshot_is_a_catalogue_or_a_reason(self):
        if self.outcome.is_ok and not self.products:
            raise ValueError(
                "an OK snapshot with no products would report the whole catalogue as empty. "
                "COROS Colombia has 45; catalog.normalize() already fails closed on an "
                "empty feed, so pass the failure through instead."
            )
        if not self.outcome.is_ok and not self.detail:
            raise ValueError(
                f"a {self.outcome.name} snapshot has to say why — every tool answer this turn "
                "quotes it, and the person reads it."
            )
        return self

    @property
    def visible(self) -> tuple[CatalogProduct, ...]:
        return tuple(p for p in self.products if not p.hidden)


# Bound per turn so two Reflex sessions cannot see each other's catalogue, and so a
# turn that never read the feed cannot silently answer from the previous one's.
_snapshot: ContextVar[Snapshot | None] = ContextVar("brujula_snapshot", default=None)


def bind_snapshot(snapshot: Snapshot | None) -> None:
    _snapshot.set(snapshot)


def snapshot() -> Snapshot | None:
    return _snapshot.get()


# ── the catalogue's own shape, from the registry ───────────────────────────────


def _is_device(product: CatalogProduct) -> bool:
    return devices.device_for_product(product) is not None


def _is_strap(product: CatalogProduct) -> bool:
    return devices.strap_fit_for_product(product) is not None


@dataclass(frozen=True)
class Group:
    handle: str
    title: str
    source: str
    note: str
    holds: Callable[[CatalogProduct], bool]


GROUPS: tuple[Group, ...] = (
    Group(
        handle="relojes",
        title="Relojes GPS y ciclocomputadores",
        source="devices.DEVICES",
        note=(
            "Los dispositivos que COROS Colombia sí vende. Uno de ellos, el DURA, es un "
            "ciclocomputador y no lleva correa."
        ),
        holds=_is_device,
    ),
    Group(
        handle="correas",
        title="Correas",
        source="devices.STRAPS",
        note=(
            "Incluye correas de relojes que aquí no se venden. Qué correa le sirve a qué "
            "reloj lo responde lookup_device_compat, nunca el ancho ni el título."
        ),
        holds=_is_strap,
    ),
    Group(
        handle="accesorios",
        title="Accesorios y sensores",
        source="resto",
        note=(
            "Todo lo que el registro de dispositivos no nombra: sensores, monitores de "
            "frecuencia cardiaca, cargadores, hidratación."
        ),
        holds=lambda product: not _is_device(product) and not _is_strap(product),
    ),
)

BY_HANDLE: dict[str, Group] = {g.handle: g for g in GROUPS}


def group_of(product: CatalogProduct) -> Group:
    return next(g for g in GROUPS if g.holds(product))


# ── what the model is allowed to see ──────────────────────────────────────────


def _slim(product: CatalogProduct) -> dict[str, Any]:
    """Whitelist and truncate. Adding a key here is a change to every prompt that
    renders one, and `description` is not a candidate — see the module docstring."""
    device = devices.device_for_product(product)
    return {
        "product_id": product.product_id,
        "product_handle": product.handle[:TITLE_CHARS],
        "title": product.title[:TITLE_CHARS],
        "product_url": product.product_url,
        "image_url": product.image_url or "",
        "price_minor": product.min_price_minor,
        "in_stock": product.in_stock,
        "group": group_of(product).handle,
        "device": device.slug if device is not None else "",
        "option_names": list(product.option_names),
        "variants": [
            {
                "variant_id": v.variant_id,
                "label": v.label,
                "price_minor": v.price_minor,
                "available": v.available,
            }
            for v in product.variants[:MAX_VARIANTS]
        ],
    }


def _slim_all(products: Sequence[CatalogProduct]) -> list[dict[str, Any]]:
    return [_slim(p) for p in products]


def as_candidates(products: Sequence[CatalogProduct]) -> list[dict[str, Any]]:
    """The selection stage sees exactly what the retrieval tools handed back — same
    whitelist, one place. A second shape there is a second thing to keep in step with
    `_slim`, and the divergence would show up as a field the model can cite and nothing
    can verify."""
    return _slim_all(products)


def as_response(result: ToolResult) -> dict[str, Any]:
    """The function-response part the loop hands back to the model.

    `outcome` always travels. A refusal delivered as an absent key is a refusal the
    model reads as an empty answer, which is the one failure `outcomes.py` exists to
    prevent."""
    response: dict[str, Any] = {"outcome": result.outcome.value}
    if result.detail:
        response["detail"] = result.detail
    if result.data is not None:
        response["data"] = result.data
    return response


def _unread(tool: str) -> ToolResult:
    snap = snapshot()
    if snap is None:
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.UPSTREAM_ERROR,
            detail=(
                "no se leyó el catálogo en este turno, así que no hay nada que consultar. "
                "No es que el catálogo esté vacío."
            ),
        )
    if not snap.outcome.is_ok:
        return ToolResult(tool=tool, outcome=snap.outcome, detail=snap.detail)
    return None  # type: ignore[return-value]


# ── the tools ─────────────────────────────────────────────────────────────────


async def list_collections(**_: Any) -> ToolResult:
    """No `query` parameter, unlike DecaBot: three groups do not need searching, and a
    query is one more thing for the model to get wrong."""
    tool = ToolId.LIST_COLLECTIONS.value
    if (refused := _unread(tool)) is not None:
        return refused
    visible = snapshot().visible  # type: ignore[union-attr]

    listed = [
        {
            "handle": g.handle,
            "title": g.title,
            "count": sum(1 for p in visible if g.holds(p)),
            "in_stock": sum(1 for p in visible if g.holds(p) and p.in_stock),
            "source": g.source,
            "note": g.note,
        }
        for g in GROUPS
    ]
    emit("tool.list_collections", {"groups": {c["handle"]: c["count"] for c in listed}})
    return ToolResult(
        tool=tool,
        outcome=ToolOutcome.OK,
        data={
            "collections": listed,
            "total": len(visible),
            "note": (
                "Este es el catálogo completo de COROS Colombia, no una página de él. "
                "Los conteos son hechos."
            ),
        },
    )


async def get_collection_products(
    handle: str = "", limit: int = MAX_PRODUCTS, **_: Any
) -> ToolResult:
    tool = ToolId.GET_COLLECTION_PRODUCTS.value
    if (refused := _unread(tool)) is not None:
        return refused

    wanted = (handle or "").strip().lower()
    group = BY_HANDLE.get(wanted)
    if group is None:
        # A rejected handle is a normal tool ANSWER, not an exception: the model sees the
        # live names and retries, and nothing unvalidated is ever looked up.
        emit("guardrail.handle_rejected", {"handle_len": len(wanted)}, "guardrail")
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.NOT_ELIGIBLE,
            detail=(
                f"'{wanted}' no es un grupo de este catálogo. Los que existen son: "
                f"{', '.join(BY_HANDLE)}."
            ),
        )

    visible = snapshot().visible  # type: ignore[union-attr]
    found = [p for p in visible if group.holds(p)]
    if not found:
        emit("guardrail.empty_collection", {"handle": group.handle}, "guardrail")
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.UNAVAILABLE,
            detail=(
                f"el grupo '{group.handle}' existe y hoy no tiene productos. Se consultó y "
                "no hay: no busques un sustituto de otro grupo."
            ),
        )

    capped = max(1, int(limit or MAX_PRODUCTS))
    shown = found[:capped]
    emit(
        "tool.get_collection_products",
        {"handle": group.handle, "matched": len(found), "shown": len(shown)},
    )
    return ToolResult(
        tool=tool,
        outcome=ToolOutcome.OK,
        data={
            "handle": group.handle,
            "title": group.title,
            "count": len(found),
            "shown": len(shown),
            "note": group.note,
            "products": _slim_all(shown),
        },
    )


_WORD = re.compile(r"[a-z0-9]+")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _tokens(text: str) -> list[str]:
    return _WORD.findall(_fold(text))[:MAX_QUERY_TOKENS]


def _haystack(product: CatalogProduct) -> str:
    """Title, option names and variant labels — the text a shopper sees on the page.

    Not tags: `APEX Pro` on a charging cable is a compatibility claim, and matching on
    it would open a second, uncurated compatibility path. Not the handle either: three
    live handles contradict their own product."""
    device = devices.device_for_product(product)
    parts = [product.title, *product.option_names, *(v.label for v in product.variants)]
    if device is not None:
        parts.append(device.name)
    return _fold(" ".join(parts))


async def search_products(query: str = "", limit: int = MAX_PRODUCTS, **_: Any) -> ToolResult:
    """Every token has to match. An OR match over 43 products returns most of them for
    any query, which reads like a recommendation and is a shuffled catalogue."""
    tool = ToolId.SEARCH_PRODUCTS.value
    if (refused := _unread(tool)) is not None:
        return refused

    terms = _tokens(query)
    if not terms:
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.NOT_ELIGIBLE,
            detail=(
                "una búsqueda vacía devolvería el catálogo entero como si fuera una "
                "recomendación. Usa list_collections para ver qué hay."
            ),
        )

    visible = snapshot().visible  # type: ignore[union-attr]
    found = [p for p in visible if all(t in _haystack(p) for t in terms)]
    emit(
        "tool.search_products",
        # Token COUNT, never the words: an evidence bundle pastes trace payloads back
        # into a model's context, and the query is text a person typed.
        {"terms": len(terms), "searched": len(visible), "matched": len(found)},
    )

    if not found:
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.UNAVAILABLE,
            detail=(
                f"se revisaron los {len(visible)} productos del catálogo y ninguno coincide. "
                "Esto es todo el catálogo, así que no existe: no reformules la búsqueda "
                "esperando otro resultado."
            ),
        )

    capped = max(1, int(limit or MAX_PRODUCTS))
    shown = found[:capped]
    return ToolResult(
        tool=tool,
        outcome=ToolOutcome.OK,
        data={
            "searched": len(visible),
            "matched": len(found),
            "shown": len(shown),
            "products": _slim_all(shown),
        },
    )


def _device_names() -> str:
    return ", ".join(d.name for d in devices.DEVICES)


async def lookup_device_compat(
    device: str = "", case_mm: int | None = None, **_: Any
) -> ToolResult:
    """The only authority on which strap fits what. Equal width is not compatibility —
    COROS sells a 24 mm strap "solo compatible con el APEX 4 46mm" and, separately,
    24 mm NOMAD straps — so nothing here is derived from a number."""
    tool = ToolId.LOOKUP_DEVICE_COMPAT.value
    if (refused := _unread(tool)) is not None:
        return refused

    resolved = devices.resolve_with_case(device)
    if resolved is None:
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.NOT_ELIGIBLE,
            detail=(
                f"'{(device or '').strip()[:60]}' no es un dispositivo COROS que este "
                f"registro conozca. Los que conoce: {_device_names()}."
            ),
        )
    found, named_case = resolved
    case = case_mm if case_mm is not None else named_case

    try:
        strap_mm = devices.strap_width(found, case)
        straps = devices.straps_for(snapshot().visible, found.slug, case_mm=case)  # type: ignore[union-attr]
    except devices.CaseUnspecified as exc:
        # A question, not a pick. An empty tuple here would read as "COROS sells no
        # APEX 4 straps", which is false, and picking a case for someone is a guess.
        emit("guardrail.case_unspecified", {"device": found.slug}, "guardrail")
        return ToolResult(tool=tool, outcome=ToolOutcome.NOT_ELIGIBLE, detail=str(exc))
    except devices.UnknownCase as exc:
        return ToolResult(tool=tool, outcome=ToolOutcome.NOT_ELIGIBLE, detail=str(exc))

    emit(
        "guardrail.device_compat",
        {
            "device": found.slug,
            "case_mm": case,
            "sold_locally": found.sold_locally,
            "strap_mm": strap_mm,
            "straps": len(straps),
        },
        "guardrail",
    )
    return ToolResult(
        tool=tool,
        outcome=ToolOutcome.OK,
        data={
            "device": found.slug,
            "name": found.name,
            "sold_locally": found.sold_locally,
            "case_mm": case,
            "cases_mm": [c.case_mm for c in found.cases if c.case_mm is not None],
            "strap_mm": strap_mm,
            "note": found.note,
            "straps": _slim_all(straps),
        },
    )


# ── what the model is told about them ─────────────────────────────────────────

DECLARATIONS: list[types.FunctionDeclaration] = [
    types.FunctionDeclaration(
        name=ToolId.LIST_COLLECTIONS.value,
        description=(
            "Los tres grupos en los que se divide el catálogo completo de COROS Colombia, "
            "con cuántos productos tiene cada uno y cuántos están disponibles. Es el "
            "catálogo entero, no una página: los conteos son hechos, no estimaciones. "
            "Empieza por aquí cuando no sepas qué vende esta tienda."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name=ToolId.GET_COLLECTION_PRODUCTS.value,
        description=(
            "Los productos de UN grupo: 'relojes', 'correas' o 'accesorios'. El handle "
            "tiene que ser uno de esos tres; cualquier otro devuelve not_eligible con la "
            "lista de los que existen. Una lista vacía significa que ese grupo hoy no "
            "tiene nada, no que haya que sustituirlo con algo de otro grupo."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "handle": types.Schema(
                    type=types.Type.STRING,
                    description="'relojes', 'correas' o 'accesorios'.",
                ),
                "limit": types.Schema(
                    type=types.Type.INTEGER,
                    description="Cuántos productos devolver. Por defecto 12.",
                ),
            },
            required=["handle"],
        ),
    ),
    types.FunctionDeclaration(
        name=ToolId.SEARCH_PRODUCTS.value,
        description=(
            "Búsqueda literal por palabras sobre los 43 productos visibles del catálogo: "
            "compara tus palabras contra el título, los nombres de opción y las etiquetas "
            "de variante, y TODAS tienen que coincidir. No es búsqueda semántica, así que "
            "una frase conversacional no encuentra nada. Como se revisa todo el catálogo, "
            "cero resultados (unavailable) significa que ese producto no existe aquí — no "
            "que haya que reformular."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "query": types.Schema(
                    type=types.Type.STRING,
                    description=(
                        "Una a tres palabras que aparezcan en el producto: 'correa nylon', "
                        "'sensor cadencia', 'pace 4'. Sin adjetivos ni condiciones."
                    ),
                ),
                "limit": types.Schema(
                    type=types.Type.INTEGER,
                    description="Cuántos productos devolver. Por defecto 12.",
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name=ToolId.LOOKUP_DEVICE_COMPAT.value,
        description=(
            "La ÚNICA fuente sobre correas y compatibilidad: qué ancho declara COROS para "
            "un dispositivo, qué correas de este catálogo le sirven, y si el dispositivo se "
            "vende en Colombia. Un ancho igual no es compatibilidad y un título no es una "
            "ficha técnica; nunca deduzcas ninguna de las dos. El APEX 4 viene en 42 mm y "
            "46 mm y llevan correas distintas, así que sin 'case_mm' esta herramienta "
            "devuelve la pregunta en vez de elegir por la persona."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "device": types.Schema(
                    type=types.Type.STRING,
                    description="El modelo como lo nombró la persona: 'APEX 4', 'PACE 3', 'NOMAD'.",
                ),
                "case_mm": types.Schema(
                    type=types.Type.INTEGER,
                    description="El tamaño de caja en mm, solo para el APEX 4: 42 o 46.",
                ),
            },
            required=["device"],
        ),
    ),
]

# `tools=` takes Tool objects: a bare FunctionDeclaration raises AttributeError inside
# the SDK, before any HTTP call. `gemini.as_tools()` is the one place that knows it.
TOOLS: list[Any] = gemini.as_tools(DECLARATIONS)

DISPATCH: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    ToolId.LIST_COLLECTIONS.value: list_collections,
    ToolId.GET_COLLECTION_PRODUCTS.value: get_collection_products,
    ToolId.SEARCH_PRODUCTS.value: search_products,
    ToolId.LOOKUP_DEVICE_COMPAT.value: lookup_device_compat,
}

assert set(DISPATCH) == {t.value for t, s in SURFACES.items() if "brujula" in s}, (
    "capability.SURFACES and this dispatch table disagree about Brújula's tool surface. "
    "AGENTS.md's maintenance contract moves capability.py, tools.py and prompts.py together."
)
