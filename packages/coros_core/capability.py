"""The capability map — which tool, if any, can serve a request.

`capable_tools()` is a pure lookup. When it comes back empty, `check_capability()` turns
that into a typed `DeadEnd`, because the failure this module exists to prevent is the one
KB `docs/lifeseek/SPEC.md` §4.2 calls the most dangerous silent failure there is: an
empty result that reads as "there is nothing for you" when the truth is "nothing here
could have looked". `CapabilityVerdict` enforces it structurally — no tools and no reason
is not a constructible object.

Three distinctions are load-bearing:

  * **Capability is not availability.** COROS Colombia sells a bike cadence sensor and it
    is out of stock; `cycling_cadence` is therefore *capable*, and `check_stock` is what
    reports the shelf. A dead end here would tell a person COROS does not make a product
    it makes and will restock. Cycling *power* is the opposite: no product in the
    45-product feed measures it, so no search can end anywhere but empty.
  * **A dead end is not a negative answer.** `DeadEnd.outcome` is restricted to the two
    escalating outcomes, so `UNAVAILABLE` — "we looked, there is nothing" — cannot be
    dressed as a capability verdict. Retrying a dead end is pointless; retrying an
    `UNAVAILABLE` is not.
  * **A person is not a missing tool.** Checkout is withheld deliberately (`WITHHELD`),
    so `place_order` is `NEEDS_HUMAN`: an answer exists and a human click is what
    authorises it. `NO_CAPABILITY` there would be a lie about the store.

A need nobody declared is a dead end, never a wildcard. That is why `need` is a plain
normalised `str` and not the `Need` literal: the string arrives from a model, and a
typo has to fail closed rather than raise out of the gate that was supposed to catch it.
No alias table translates Spanish synonyms — "potenciómetro" is ambiguous between the two
power needs COROS answers differently, and picking one would be a guess.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from coros_core.outcomes import ToolOutcome, ToolResult
from coros_core.trace import emit

Surface = Literal["brujula", "huella"]


class ToolId(str, Enum):
    """Every tool either app may hand the model. The apps' schemas are written against
    these ids, so a tool that is not here cannot be offered and cannot be asked for."""

    LIST_COLLECTIONS = "list_collections"
    GET_COLLECTION_PRODUCTS = "get_collection_products"
    SEARCH_PRODUCTS = "search_products"
    LOOKUP_DEVICE_COMPAT = "lookup_device_compat"
    GET_TRAINING_SUMMARY = "get_training_summary"


# Named so the omission is auditable rather than a silence someone re-adds by accident.
# UCP really exposes both, and `ucp.call_ucp()` can still reach them from a click handler;
# what they are not is a tool the model can choose.
WITHHELD: frozenset[str] = frozenset({"create_cart", "create_checkout"})

SURFACES: dict[ToolId, tuple[Surface, ...]] = {
    ToolId.LIST_COLLECTIONS: ("brujula", "huella"),
    ToolId.GET_COLLECTION_PRODUCTS: ("brujula", "huella"),
    ToolId.SEARCH_PRODUCTS: ("brujula", "huella"),
    ToolId.LOOKUP_DEVICE_COMPAT: ("brujula", "huella"),
    ToolId.GET_TRAINING_SUMMARY: ("huella",),
}

Need = Literal[
    "product_recommendation",
    "catalog_browse",
    "product_lookup",
    "strap_compatibility",
    "heart_rate",
    "cycling_cadence",
    "cycling_speed",
    "training_history",
    "cycling_power",
    "running_power",
    "ant_plus",
    "place_order",
]

DeadEndReason = Literal[
    "no_product", "vendor_says_no", "human_gated", "other_surface", "undeclared"
]


class DeadEnd(BaseModel):
    """Why nothing can serve this, in the words the person gets and the words the audit
    rail gets. No `alternative`/`instead`/`closest` field: a capability COROS does not
    have is never answered with a product that does something else, and the cheapest way
    to make that impossible is to give the substitution nowhere to sit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    need: str
    outcome: ToolOutcome
    reason: DeadEndReason
    statement: str
    tradeoff: str = ""
    evidence: str = ""

    @model_validator(mode="after")
    def a_dead_end_is_never_a_negative_answer(self):
        if not self.outcome.needs_escalation:
            raise ValueError(
                f"{self.need} cannot be a dead end with outcome {self.outcome.name}. "
                "UNAVAILABLE means we looked and there was nothing — a different answer, "
                "reported by check_stock. Only NO_CAPABILITY and NEEDS_HUMAN escalate."
            )
        return self


class CapabilityVerdict(BaseModel):
    """Tools xor a dead end. The validator is the whole guarantee: an empty tool list with
    no reason attached is not a value this system can produce."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    need: str
    surface: Surface
    tools: tuple[ToolId, ...] = ()
    dead_end: DeadEnd | None = None

    @model_validator(mode="after")
    def an_empty_map_carries_its_reason(self):
        if not self.tools and self.dead_end is None:
            raise ValueError(
                f"no tool serves {self.need!r} on {self.surface} and no reason says why. "
                "An empty capability result has to be a NO_CAPABILITY somebody can render."
            )
        if self.tools and self.dead_end is not None:
            raise ValueError(f"{self.need!r} is both served and a dead end — pick one")
        return self

    @property
    def is_dead_end(self) -> bool:
        return self.dead_end is not None


def _normalise(raw: str) -> str:
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return re.sub(r"[\s_-]+", "_", folded.strip().lower()).strip("_")


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    need: str
    surface: Surface

    @field_validator("need")
    @classmethod
    def _fold(cls, v: str) -> str:
        return _normalise(v)


MAP: dict[Need, tuple[ToolId, ...]] = {
    "product_recommendation": (
        ToolId.SEARCH_PRODUCTS,
        ToolId.LIST_COLLECTIONS,
        ToolId.GET_COLLECTION_PRODUCTS,
        ToolId.LOOKUP_DEVICE_COMPAT,
    ),
    "catalog_browse": (ToolId.LIST_COLLECTIONS, ToolId.GET_COLLECTION_PRODUCTS),
    "product_lookup": (ToolId.SEARCH_PRODUCTS,),
    "strap_compatibility": (ToolId.LOOKUP_DEVICE_COMPAT, ToolId.SEARCH_PRODUCTS),
    "heart_rate": (ToolId.SEARCH_PRODUCTS,),
    "cycling_cadence": (ToolId.SEARCH_PRODUCTS,),
    "cycling_speed": (ToolId.SEARCH_PRODUCTS,),
    "training_history": (ToolId.GET_TRAINING_SUMMARY,),
}

DEAD_ENDS: dict[Need, DeadEnd] = {
    "cycling_power": DeadEnd(
        need="cycling_power",
        outcome=ToolOutcome.NO_CAPABILITY,
        reason="no_product",
        statement="COROS no fabrica un medidor de potencia, y este catálogo no vende uno.",
        tradeoff=(
            "Buscar no cambia eso: no hay nada que buscar. Lo que falta es el medidor, y "
            "aquí no se consigue."
        ),
        evidence=(
            "los 45 productos del feed de coros.com.co, revisados el 30 jul 2026: ninguno "
            "mide potencia"
        ),
    ),
    "running_power": DeadEnd(
        need="running_power",
        outcome=ToolOutcome.NO_CAPABILITY,
        reason="vendor_says_no",
        statement="COROS no mide potencia de carrera, y este catálogo no vende con qué medirla.",
        tradeoff="Lo dice COROS en su propia ficha, no es una limitación de esta búsqueda.",
        evidence=(
            'ficha del COROS POD 2: "¿El POD 2 mide la potencia de funcionamiento? No, el '
            'POD 2 usa Effort Pace"'
        ),
    ),
    "ant_plus": DeadEnd(
        need="ant_plus",
        outcome=ToolOutcome.NO_CAPABILITY,
        reason="vendor_says_no",
        statement="Los sensores que vende COROS Colombia no hablan ANT+, solo Bluetooth.",
        tradeoff="Si tu ciclocomputador exige ANT+, ninguno de estos sensores le sirve.",
        evidence=(
            'fichas de los sensores de cadencia y velocidad: "No compatible con '
            'dispositivos ANT+"'
        ),
    ),
    "place_order": DeadEnd(
        need="place_order",
        outcome=ToolOutcome.NEEDS_HUMAN,
        reason="human_gated",
        statement="El pedido lo haces tú. Yo llego hasta el enlace del carrito.",
        tradeoff="Nada aquí puede comprar en tu nombre, ni siquiera si se lo pides.",
        evidence="create_cart y create_checkout no están en la lista de herramientas",
    ),
}


def is_declared(need: str) -> bool:
    return need in MAP or need in DEAD_ENDS


def capable_tools(request: CapabilityRequest) -> tuple[ToolId, ...]:
    """The map, and nothing else — pure, no trace, no clock. Empty means no tool on this
    surface can serve the request; `check_capability` is what says why."""
    offered = MAP.get(request.need, ())  # type: ignore[arg-type]
    return tuple(t for t in offered if request.surface in SURFACES[t])


def check_capability(request: CapabilityRequest) -> CapabilityVerdict:
    tools = capable_tools(request)
    dead_end = None if tools else _dead_end(request)
    verdict = CapabilityVerdict(
        need=request.need, surface=request.surface, tools=tools, dead_end=dead_end
    )
    emit(
        "guardrail.capability",
        {
            # A declared need is our own vocabulary; an undeclared one is text a person
            # typed, and evidence bundles paste trace payloads back into a model's
            # context. So the length goes in and the words never do.
            "need": request.need if is_declared(request.need) else "",
            "declared": is_declared(request.need),
            "need_len": len(request.need),
            "surface": request.surface,
            "tools": [t.value for t in tools],
            "outcome": (dead_end.outcome if dead_end else ToolOutcome.OK).value,
            "reason": dead_end.reason if dead_end else "",
        },
        "guardrail",
    )
    return verdict


def _dead_end(request: CapabilityRequest) -> DeadEnd:
    declared = DEAD_ENDS.get(request.need)  # type: ignore[arg-type]
    if declared is not None:
        return declared
    if request.need in MAP:
        elsewhere = {s for t in MAP[request.need] for s in SURFACES[t]}  # type: ignore[index]
        other = sorted(elsewhere - {request.surface})
        return DeadEnd(
            need=request.need,
            outcome=ToolOutcome.NO_CAPABILITY,
            reason="other_surface",
            statement="Esto no lo puede hacer esta aplicación.",
            tradeoff=f"La herramienta existe, pero solo en: {', '.join(other) or 'ninguna'}.",
            evidence=f"{request.need} no está expuesto en {request.surface}",
        )
    return DeadEnd(
        need=request.need,
        outcome=ToolOutcome.NO_CAPABILITY,
        reason="undeclared",
        statement="No sé responder eso, y no voy a improvisar una búsqueda para taparlo.",
        tradeoff="Reformúlalo y te digo si hay algo en el catálogo que aplique.",
        evidence="la necesidad pedida no está en el mapa de capacidades",
    )


def as_result(verdict: CapabilityVerdict, tool: str = "capability") -> ToolResult:
    """The gate at the edge of the agent loop. A caller that ignores `is_dead_end` still
    cannot produce an empty list: it gets a typed escalating outcome with a detail."""
    dead_end = verdict.dead_end
    if dead_end is not None:
        said = (part for part in (dead_end.statement, dead_end.tradeoff) if part)
        return ToolResult(tool=tool, outcome=dead_end.outcome, detail=" ".join(said))
    return ToolResult(tool=tool, outcome=ToolOutcome.OK, data=[t.value for t in verdict.tools])
