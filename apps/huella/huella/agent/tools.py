"""The five tools Huella may hand the model, the athlete's own preference state, and the
uncertainty verdict that governs what any of it is allowed to claim.

`create_cart` and `create_checkout` ARE DELIBERATELY ABSENT and must stay absent, for
the same reason they are absent from Brújula: no `ToolId` spells either one, so there is
no token sequence the model can emit that reaches a cart. `capability.WITHHELD` names
the omission so it is auditable.

**The four catalogue tools are Brújula's, re-implemented rather than imported.** The
Huella image copies `packages/coros_core` and `apps/huella/huella` and nothing else
(`apps/huella/Dockerfile`), so `import brujula.agent.tools` works in the test suite and
fails in the container — the worst possible place to discover it. The shared half that
*can* live in one place already does: the groups are derived from `devices.DEVICES` and
`devices.STRAPS`, prices go through `money.py`, and every verdict is `guardrails.py`'s.
What is duplicated is `_slim`'s whitelist and the four declarations, and
`AGENTS.md`'s maintenance contract already lists both apps' `tools.py` on the same row.

Three things here have no counterpart in Brújula.

  * **`get_training_summary` answers from a per-turn `TrainingView`, never from a
    session store.** Nothing in this module knows a session key, holds a credential or
    can reach `huella.privacy`'s store: the loop reads the history through the gate and
    binds the derived result, exactly as it binds the catalogue snapshot. The requirements
    inside a view are re-sealed by `privacy.seal()` on the way in — the gate already
    sealed them, and an agent that verifies its own inputs through an independent call is
    the difference between a boundary and a promise.
  * **`UncertaintyVerdict` is a type, not a caveat.** D2 says advice leaning on a thin or
    stale window has to be flagged; a sentence in a prompt cannot guarantee that, so
    `DemonstratedTraining` simply cannot be constructed for a window under `MIN_SAMPLE`
    activities — `sample_size` is `Field(ge=MIN_SAMPLE)` — and `UncertaintyVerdict`
    refuses to hold one alongside a flag that says the window was never read. Same shape
    as `LocalAvailabilityVerdict.is_available: Literal[True]`, and for the same reason.
  * **`Preferences` is the athlete's structured, editable, deletable state** (KB §5.1.5).
    A correction is a typed `Requirement` with `source="user"`, stored before the turn
    generates anything and applied over the derived band at serving time. It is never a
    sentence appended to a prompt, and dropping one is `drop(key)` rather than hoping a
    later prompt contradicts an earlier one.

Everything handed to the model is whitelisted and truncated by `_slim()`. Sanitised
`description` is NOT forwarded: it is the injection surface and an invitation to read a
spec out of marketing copy.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, model_validator

from coros_core import devices, gemini
from coros_core.capability import SURFACES, ToolId
from coros_core.models import CatalogProduct, Requirement, RequirementKey
from coros_core.outcomes import ToolOutcome, ToolResult
from coros_core.trace import emit

from huella import privacy

MAX_PRODUCTS = 12
MAX_VARIANTS = 8
TITLE_CHARS = 140
MAX_QUERY_TOKENS = 6
RATIONALE_CHARS = 200

# Under eight demonstrated activities a band is arithmetic on a rumour: three weeks of
# training at two sessions a week is the least that distinguishes a habit from a fortnight.
MIN_SAMPLE = 8

# Three weeks with nothing recorded. The window still describes real training — it just
# describes how somebody trained, which is a different sentence and gets said as one.
MAX_STALE_DAYS = 21


class Snapshot(BaseModel):
    """The turn's one storefront read, or the reason there isn't one.

    The validators are the whole point: an OK snapshot carrying no products, or a failed
    one carrying no reason, are the two shapes that let a 429 reach a person as "COROS
    has nothing like that"."""

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


# Bound per turn so two Reflex sessions cannot see each other's catalogue or each other's
# training, and so a turn that read neither cannot answer from the previous one's.
_snapshot: ContextVar[Snapshot | None] = ContextVar("huella_snapshot", default=None)


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
    """Whitelist and truncate. Adding a key here is a change to every prompt that renders
    one — in BOTH apps — and `description` is not a candidate."""
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
    whitelist, one place."""
    return _slim_all(products)


def as_response(result: ToolResult) -> dict[str, Any]:
    """The function-response part the loop hands back to the model.

    `outcome` always travels. A refusal delivered as an absent key is a refusal the model
    reads as an empty answer, which is the one failure `outcomes.py` exists to prevent."""
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


# ── the catalogue tools ───────────────────────────────────────────────────────


async def list_collections(**_: Any) -> ToolResult:
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
                "Este es el catálogo completo de COROS Colombia, no una página de él. Los "
                "conteos son hechos."
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
                f"el grupo '{group.handle}' existe y hoy no tiene productos. Se consultó y no "
                "hay: no busques un sustituto de otro grupo."
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
    """Title, option names and variant labels — the text a shopper sees on the page. Not
    tags: `APEX Pro` on a charging cable is a compatibility claim. Not the handle either:
    three live handles contradict their own product."""
    device = devices.device_for_product(product)
    parts = [product.title, *product.option_names, *(v.label for v in product.variants)]
    if device is not None:
        parts.append(device.name)
    return _fold(" ".join(parts))


async def search_products(query: str = "", limit: int = MAX_PRODUCTS, **_: Any) -> ToolResult:
    """Every token has to match. An OR match over 43 products returns most of them for any
    query, which reads like a recommendation and is a shuffled catalogue."""
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
        # Token COUNT, never the words: an evidence bundle pastes trace payloads back into
        # a model's context, and the query is text a person typed.
        "tool.search_products",
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


async def lookup_device_compat(device: str = "", case_mm: int | None = None, **_: Any) -> ToolResult:
    """The only authority on which strap fits what. Equal width is not compatibility —
    COROS sells a 24 mm strap "solo compatible con el APEX 4 46mm" and, separately, 24 mm
    NOMAD straps — so nothing here is derived from a number."""
    tool = ToolId.LOOKUP_DEVICE_COMPAT.value
    if (refused := _unread(tool)) is not None:
        return refused

    resolved = devices.resolve_with_case(device)
    if resolved is None:
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.NOT_ELIGIBLE,
            detail=(
                f"'{(device or '').strip()[:60]}' no es un dispositivo COROS que este registro "
                f"conozca. Los que conoce: {_device_names()}."
            ),
        )
    found, named_case = resolved
    case = case_mm if case_mm is not None else named_case

    try:
        strap_mm = devices.strap_width(found, case)
        straps = devices.straps_for(snapshot().visible, found.slug, case_mm=case)  # type: ignore[union-attr]
    except devices.CaseUnspecified as exc:
        # A question, not a pick. An empty tuple here would read as "COROS sells no APEX 4
        # straps", which is false, and picking a case for someone is a guess.
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


# ── the training vocabulary ────────────────────────────────────────────────────
#
# The six keys `privacy.derive_requirements` produces, and the labels each may carry.
# `privacy.DISCIPLINES` is public and used directly; the four numeric bands are NOT
# exported per key — only the union, `privacy.ALLOWED_VALUES` — so they are mirrored here
# and the assert below fails the import if the gate's own tables move underneath them. A
# label the gate stopped emitting would otherwise stay in the prompt as a correction the
# athlete can make and the gate would then refuse.

TRAINING_KEYS: tuple[RequirementKey, ...] = (
    "discipline",
    "sport_mix",
    "weekly_hours_band",
    "longest_session_band",
    "elevation_band",
    "consistency_band",
)

_BANDS: dict[RequirementKey, tuple[str, ...]] = {
    "weekly_hours_band": ("0-2", "2-4", "4-6", "6-9", "9-12", "12+"),
    "longest_session_band": ("0-1", "1-2", "2-3", "3-5", "5+"),
    "elevation_band": ("0-200", "200-600", "600-1200", "1200-2500", "2500+"),
    "consistency_band": ("0-1", "1-2", "2-4", "4-6", "6+"),
}

assert set().union(*_BANDS.values()) <= privacy.ALLOWED_VALUES, (
    "a band label in huella/agent/tools.py is not one huella/privacy.py can emit. These "
    "tables are mirrored because privacy exports ALLOWED_VALUES as a union and not per "
    "key; AGENTS.md's maintenance contract moves privacy.py and this file together."
)

_BAND_UNITS: dict[RequirementKey, str] = {
    "weekly_hours_band": "horas por semana",
    "longest_session_band": "horas la salida más larga",
    "elevation_band": "metros de desnivel positivo por semana",
    "consistency_band": "actividades por semana",
}

BAND_VOCABULARY: str = "\n".join(
    [
        *(
            f"{key}: {', '.join(labels)}  ({_BAND_UNITS[key]})"
            for key, labels in _BANDS.items()
        ),
        f"discipline: {', '.join(sorted(privacy.DISCIPLINES))}",
        "sport_mix: una disciplina, o dos unidas con '+' (por ejemplo run+ride)",
    ]
)


def _allowed_label(key: str, value: Any) -> bool:
    if key in _BANDS:
        return isinstance(value, str) and value in _BANDS[key]
    if key in {"discipline", "sport_mix"}:
        return isinstance(value, str) and value in privacy.ALLOWED_VALUES
    return True


# ── the athlete's own corrections: structured, editable, deletable ─────────────

_OVERRIDE_RATIONALE = "Corrección tuya. Reemplaza lo que salió de tu historial."
_STATED_RATIONALE = "Lo dijiste tú."

_KEYS: frozenset[str] = frozenset(get_args(RequirementKey))


@dataclass
class Preferences:
    """KB §5.1.5's preference state, typed. One `Requirement` per key, `source="user"`,
    written the moment a correction is read and applied every turn from then on.

    A correction is never carried as prose. `set()` refuses a value the vocabulary cannot
    express, so "corro cuatro horas largo" becomes `longest_session_band="3-5"` or nothing
    at all — a free figure would reach the presentation stage as a measurement, which is
    the one thing D2 exists to prevent.
    """

    by_key: dict[str, Requirement] = field(default_factory=dict)

    def set(self, key: str, value: str | int | bool, *, rationale: str = "") -> Requirement | None:
        if key not in _KEYS or not _allowed_label(key, value):
            return None
        if isinstance(value, str) and not value.strip():
            return None
        stored = Requirement(
            key=key,  # type: ignore[arg-type]
            value=value,
            source="user",
            derived=False,
            # A correction of a derived band gets a generated sentence: it sits beside
            # requirements whose rationale `privacy.seal()` regenerates, and free text
            # there would be indistinguishable from a derived one.
            rationale=(
                _OVERRIDE_RATIONALE
                if key in TRAINING_KEYS
                else (rationale.strip()[:RATIONALE_CHARS] or _STATED_RATIONALE)
            ),
        )
        self.by_key[key] = stored
        return stored

    def drop(self, key: str) -> bool:
        return self.by_key.pop(key, None) is not None

    def clear(self) -> int:
        count = len(self.by_key)
        self.by_key.clear()
        return count

    def all(self) -> tuple[Requirement, ...]:
        return tuple(self.by_key.values())

    def keys(self) -> tuple[str, ...]:
        return tuple(self.by_key)

    def overrides_of(self, derived: Sequence[Requirement]) -> tuple[str, ...]:
        return tuple(r.key for r in derived if r.key in self.by_key)

    def apply(self, derived: Sequence[Requirement]) -> tuple[Requirement, ...]:
        """Serving time, not prompt time. The derived requirement keeps its position so
        the model reads the correction where it would have read the band."""
        replaced = self.overrides_of(derived)
        merged = [self.by_key.get(r.key, r) for r in derived]
        merged += [r for k, r in self.by_key.items() if k not in {d.key for d in derived}]
        if replaced:
            emit(
                "guardrail.preference_override",
                {"keys": list(replaced), "stated": len(self.by_key)},
                "guardrail",
            )
        return tuple(merged)


# ── the uncertainty layer (D2) ─────────────────────────────────────────────────

UncertaintyFlag = Literal[
    "not_connected",
    "unread",
    "reconnect",
    "no_permission",
    "no_history",
    "thin",
    "stale",
    "truncated",
    "manual_reported",
    "overridden",
]

# Any one of these means no advice may lean on demonstrated training this turn. The rest
# are real flags on a real window: it is old, it was capped, or the athlete corrected it.
_UNGROUNDED: frozenset[str] = frozenset(
    {"not_connected", "unread", "reconnect", "no_permission", "no_history", "thin"}
)

# One sentence per flag, generated from three counts and nothing else — the same rule
# `privacy._RATIONALES` follows, and `UncertaintyVerdict` regenerates and compares. There
# is no slot here for an activity name, and no phrasing a caller can supply.
_FLAG_ES: dict[str, str] = {
    "not_connected": (
        "No tienes Strava conectado, así que nada de esto sale de tu entrenamiento: sale de "
        "lo que me cuentes."
    ),
    "unread": (
        "No pude leer tu historial en este turno, así que no hay nada derivado de tu "
        "entrenamiento. Eso no es un historial vacío."
    ),
    "reconnect": (
        "La autorización de Strava dejó de servir, así que no pude leer tu historial y hay "
        "que volver a conectarla."
    ),
    "no_permission": (
        "La autorización no incluye tus actividades, así que no hay historial que leer."
    ),
    "no_history": (
        "En {days} días no hay actividades registradas por un dispositivo, así que no hay "
        "entrenamiento demostrado que leer."
    ),
    "thin": (
        "Son {sample} actividades en {days} días: poca base. Lo que sale de ahí es indicativo "
        "y no una medición de cómo entrenas."
    ),
    "stale": (
        "La última actividad es de hace {stale} días, así que esto describe cómo entrenabas y "
        "no necesariamente cómo entrenas hoy."
    ),
    "truncated": (
        "La lectura se cortó antes de terminar la ventana, así que tu volumen real puede ser "
        "mayor que la banda."
    ),
    "manual_reported": (
        "Buena parte de tu historial está escrito a mano y no medido por un dispositivo; esas "
        "actividades no cuentan aquí."
    ),
    "overridden": (
        "Hay correcciones tuyas que reemplazan lo que salió del historial, y mandan sobre él."
    ),
}

_CLEAR_ES = (
    "Esto sale de {sample} actividades registradas en {days} días. Son bandas derivadas de "
    "contarlas, no mediciones."
)


def _statement(
    flags: Sequence[str], *, sample: int, days: int, stale: int | None
) -> str:
    if not flags:
        return _CLEAR_ES.format(sample=sample, days=days, stale=stale or 0)
    return " ".join(
        _FLAG_ES[flag].format(sample=sample, days=days, stale=stale or 0) for flag in flags
    )


class DemonstratedTraining(BaseModel):
    """A window that really did demonstrate something. Not constructible otherwise.

    `sample_size: Field(ge=MIN_SAMPLE)` is the guarantee rather than documentation: there
    is no way to build this object for a thin window, so thin training cannot travel
    through the pipeline wearing a clearance. `privacy.seal()` runs on the requirements
    for the same reason — the gate sealed them once, and this is an independent path
    saying so."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_demonstrated: Literal[True] = True
    requirements: tuple[Requirement, ...]
    sample_size: int = Field(ge=MIN_SAMPLE)
    window_days: int = Field(gt=0)

    @model_validator(mode="after")
    def demonstrated_training_is_what_the_gate_sealed(self):
        if not self.requirements:
            raise ValueError(
                "a demonstrated window with no requirements is a thin one wearing the wrong "
                "type. Leave demonstrated=None and let the uncertainty flags say why."
            )
        privacy.seal(self.requirements)
        return self


class UncertaintyVerdict(BaseModel):
    """What this turn's advice is allowed to claim, and what it has to admit.

    D2 in one object: `grounded` is `demonstrated is not None`, and the validator refuses
    the combination that would let a recommendation lean on a window nobody read. The
    statement is regenerated and compared, so the athlete-facing sentence carries counts
    this system computed and nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    readable: bool = False
    conclusive: bool = False
    confidence: Literal["high", "medium", "none"] = "none"
    flags: tuple[UncertaintyFlag, ...] = ()
    demonstrated: DemonstratedTraining | None = None
    sample_size: int = 0
    window_days: int = 0
    stale_days: int | None = None
    manual_dropped: int = 0
    truncated: bool = False
    overrides: tuple[str, ...] = ()
    statement: str = ""
    detail: str = ""

    @property
    def grounded(self) -> bool:
        return self.demonstrated is not None

    @model_validator(mode="after")
    def a_flagged_window_cannot_also_be_a_demonstrated_one(self):
        blocking = sorted(set(self.flags) & _UNGROUNDED)
        if self.demonstrated is not None:
            if blocking:
                raise ValueError(
                    f"a window flagged {blocking} was handed a DemonstratedTraining. Those "
                    "flags mean nothing was read or too little was; advice must not lean on it."
                )
            if not self.readable:
                raise ValueError("demonstrated training out of a window that was never read")
        if (self.confidence != "none") != (self.demonstrated is not None):
            raise ValueError(
                "confidence and demonstrated disagree. 'none' is exactly the ungrounded case; "
                "anything else claims a window this verdict does not carry."
            )
        if self.confidence == "high" and self.flags:
            raise ValueError(f"high confidence with flags {list(self.flags)} attached")
        expected = _statement(
            self.flags, sample=self.sample_size, days=self.window_days, stale=self.stale_days
        )
        if self.statement != expected:
            raise ValueError(
                "the athlete-facing statement is not the phrasing these flags generate. It is "
                "a pure function of the flags and three counts so that nothing else can ride "
                "out in it."
            )
        return self


def check_uncertainty(
    summary: privacy.Sync | None, *, connected: bool, overrides: Sequence[str] = ()
) -> UncertaintyVerdict:
    """The D2 verdict, in code, before anything is generated.

    A refusal is never a thin history: `summary.outcome` is carried through from the gate,
    so RATE_LIMITED reaches here as "we could not look" and reaches the athlete as a
    different sentence from "you have not trained"."""
    flags: list[str] = []
    sample = summary.sample_size if summary else 0
    days = summary.window_days if summary else privacy.WINDOW_DAYS
    stale = summary.stale_days if summary else None
    manual = summary.manual_dropped if summary else 0
    truncated = bool(summary.truncated) if summary else False
    outcome = summary.outcome if summary else ToolOutcome.UPSTREAM_ERROR
    detail = summary.detail if summary else ""

    if not connected:
        flags.append("not_connected")
    elif summary is None:
        flags.append("unread")
    elif outcome is ToolOutcome.NEEDS_HUMAN:
        flags.append("reconnect")
    elif outcome is ToolOutcome.NOT_ELIGIBLE:
        flags.append("no_permission")
    elif not outcome.is_ok:
        flags.append("unread")
    elif sample == 0:
        flags.append("no_history")
    elif sample < MIN_SAMPLE:
        flags.append("thin")

    readable = connected and summary is not None and outcome.is_ok
    if readable:
        if stale is not None and stale > MAX_STALE_DAYS:
            flags.append("stale")
        if truncated:
            flags.append("truncated")
        if manual and manual >= sample:
            flags.append("manual_reported")
    if overrides:
        flags.append("overridden")

    grounded = readable and not (set(flags) & _UNGROUNDED)
    demonstrated = (
        DemonstratedTraining(
            requirements=summary.requirements,  # type: ignore[union-attr]
            sample_size=sample,
            window_days=days,
        )
        if grounded
        else None
    )
    verdict = UncertaintyVerdict(
        readable=readable,
        conclusive=connected and summary is not None and outcome.is_conclusive,
        confidence=("none" if demonstrated is None else ("high" if not flags else "medium")),
        flags=tuple(flags),  # type: ignore[arg-type]
        demonstrated=demonstrated,
        sample_size=sample,
        window_days=days,
        stale_days=stale,
        manual_dropped=manual,
        truncated=truncated,
        overrides=tuple(overrides),
        statement=_statement(flags, sample=sample, days=days, stale=stale),
        detail=detail,
    )
    emit(
        "guardrail.uncertainty",
        {
            "grounded": verdict.grounded,
            "confidence": verdict.confidence,
            "flags": list(verdict.flags),
            "outcome": outcome.value,
            "activities": sample,
            "window_days": days,
            "stale_days": stale,
            "manual_dropped": manual,
            "truncated": truncated,
            "overrides": list(overrides),
        },
        "guardrail",
    )
    return verdict


# ── the turn's one reading of the history ──────────────────────────────────────


class TrainingView(BaseModel):
    """Everything the model may learn about an athlete's training this turn.

    Same contract as `Snapshot`: a non-OK view has to say why, because every tool answer
    quotes it. `derived` is re-sealed here — `privacy.seal()` is the gate's own check, run
    a second time from outside, so a requirement this module built by hand could not pass
    as one the gate produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ToolOutcome = ToolOutcome.OK
    detail: str = ""
    connected: bool = False
    derived: tuple[Requirement, ...] = ()
    stated: tuple[Requirement, ...] = ()
    uncertainty: UncertaintyVerdict

    @model_validator(mode="after")
    def a_view_is_a_reading_or_a_reason(self):
        if not self.outcome.is_ok and not self.detail:
            raise ValueError(
                f"a {self.outcome.name} training view has to say why — the tool answer quotes "
                "it and the athlete reads it."
            )
        privacy.seal(self.derived)
        for requirement in self.stated:
            if requirement.derived or requirement.source == "strava":
                raise ValueError(
                    f"{requirement.key} is the athlete's own correction and claims to be "
                    "derived from Strava. Provenance is what lets the uncertainty layer say "
                    "which half of the advice was measured and which was said."
                )
        if self.uncertainty.grounded and not self.outcome.is_ok:
            raise ValueError(
                f"grounded advice out of a {self.outcome.name} reading. A refusal is not a "
                "window."
            )
        return self

    @property
    def effective(self) -> tuple[Requirement, ...]:
        """What the rest of the turn reasons over: the athlete's corrections in the place
        of the bands they correct, then anything they said that the gate never derives."""
        stated = {r.key: r for r in self.stated}
        merged = [stated.get(r.key, r) for r in self.derived]
        merged += [r for k, r in stated.items() if k not in {d.key for d in self.derived}]
        return tuple(merged)


_training: ContextVar[TrainingView | None] = ContextVar("huella_training", default=None)


def bind_training(view: TrainingView | None) -> None:
    _training.set(view)


def training() -> TrainingView | None:
    return _training.get()


def training_view(
    summary: privacy.Sync | None, *, connected: bool, preferences: Preferences | None = None
) -> TrainingView:
    """Build the turn's view out of what the gate answered and what the athlete corrected.

    The `Sync` is the only thing that crosses from `privacy.py`, and it carries counts and
    sealed requirements — no window, no credential, nothing to unpack."""
    prefs = preferences if preferences is not None else Preferences()
    derived = summary.requirements if summary is not None else ()
    verdict = check_uncertainty(
        summary, connected=connected, overrides=prefs.overrides_of(derived)
    )
    if not connected:
        outcome, detail = ToolOutcome.NOT_ELIGIBLE, (
            "no hay una cuenta de Strava conectada en esta sesión, así que no hay historial "
            "que leer. No es un historial vacío: pregúntale al atleta cómo entrena."
        )
    elif summary is None:
        outcome, detail = ToolOutcome.UPSTREAM_ERROR, (
            "no se leyó el historial en este turno. No es que no haya entrenamiento."
        )
    else:
        outcome, detail = summary.outcome, summary.detail

    return TrainingView(
        outcome=outcome,
        detail=detail,
        connected=connected,
        derived=derived if outcome.is_ok else (),
        stated=prefs.all(),
        uncertainty=verdict,
    )


def _rendered(requirement: Requirement) -> dict[str, Any]:
    return {
        "key": requirement.key,
        "value": requirement.value,
        "source": requirement.source,
        "derived": requirement.derived,
        "sample_size": requirement.sample_size,
        "window_days": requirement.window_days,
        "rationale": requirement.rationale,
    }


def as_uncertainty(verdict: UncertaintyVerdict) -> dict[str, Any]:
    """The verdict as the model and the trace see it. `statement` is what a person reads;
    the flags are the part a UI renders as a flag rather than as prose."""
    return {
        "grounded": verdict.grounded,
        "confidence": verdict.confidence,
        "flags": list(verdict.flags),
        "activities": verdict.sample_size,
        "window_days": verdict.window_days,
        "stale_days": verdict.stale_days,
        "statement": verdict.statement,
    }


async def get_training_summary(**_: Any) -> ToolResult:
    """What the athlete's training demonstrates, as the privacy gate derived it.

    Never recomputed here and never re-read: this answers from the view the turn already
    bound, so a model that calls it twice spends no Strava quota and cannot get two
    different answers inside one turn."""
    tool = ToolId.GET_TRAINING_SUMMARY.value
    view = training()
    if view is None:
        return ToolResult(
            tool=tool,
            outcome=ToolOutcome.UPSTREAM_ERROR,
            detail=(
                "no se leyó el historial en este turno, así que no hay resumen que consultar. "
                "No es que el atleta no entrene."
            ),
        )
    if not view.outcome.is_ok:
        return ToolResult(tool=tool, outcome=view.outcome, detail=view.detail)

    requirements = view.effective
    emit(
        "tool.get_training_summary",
        {
            "requirements": len(requirements),
            "activities": view.uncertainty.sample_size,
            "grounded": view.uncertainty.grounded,
            "flags": list(view.uncertainty.flags),
        },
    )
    return ToolResult(
        tool=tool,
        outcome=ToolOutcome.OK,
        data={
            "requirements": [_rendered(r) for r in requirements],
            "overrides": list(view.uncertainty.overrides),
            "manual_ignored": view.uncertainty.manual_dropped,
            "truncated": view.uncertainty.truncated,
            "uncertainty": as_uncertainty(view.uncertainty),
            "note": (
                "Cada valor es una BANDA derivada de contar actividades registradas por un "
                "dispositivo, nunca una medición. Las actividades escritas a mano se "
                "descartan. Aquí no hay pulso, ritmo, potencia ni distancia: no se leen."
            ),
        },
    )


# ── a derived band never leaves as a measured figure ───────────────────────────
#
# `guardrails.scrub_prose` covers the product half and, by way of its battery patterns,
# hours and days as well — but not kilometres, metres of ascent, or a count of sessions,
# which are exactly the figures a training advisor invents. This is the Huella-shaped
# complement, and it runs FIRST: a band written as "6-9 horas" survives here and is then
# offered to `scrub_prose` as backing text, so the two do not fight over the same span.

# A band is spoken three ways and all three are the same claim: "6-9", "entre 6 y 9",
# "de 6 a 9". The separator alternatives are what keep a TRUTHFUL band whole — matching
# only the hyphen leaves `9 horas` as the span, and excising that out of `entre 6 y 9
# horas` produces `entre 6 y por semana`, which is neither true nor Spanish.
_RANGE = r"(?:\s*[-–—]\s*|\s+(?:a|y)\s+)"
_FIGURE = rf"(?P<num>\d+(?:[.,]\d+)?(?:{_RANGE}\d+(?:[.,]\d+)?)?\+?)"

# Swallowed with the figure so an excision does not leave its own connector dangling.
_LEAD = (
    r"(?:\b(?:entre|de|unas|unos|casi|hasta|cerca\s+de|alrededor\s+de|m[aá]s\s+de|"
    r"menos\s+de|aproximadamente|como)\s+)?"
)

_TRAINING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hours", re.compile(rf"(?i){_LEAD}\b{_FIGURE}\s*(?:h|hs|hrs?|horas?)\b")),
    ("distance", re.compile(rf"(?i){_LEAD}\b{_FIGURE}\s*(?:km|kil[oó]metros?|millas?)\b")),
    (
        "elevation",
        re.compile(
            rf"(?i){_LEAD}\b{_FIGURE}\s*(?:m|mts\.?|metros?)\s*(?:de\s+)?"
            r"(?:desnivel|elevaci[oó]n|ascenso|subida|d\+)"
        ),
    ),
    (
        "sessions",
        re.compile(
            rf"(?i){_LEAD}\b{_FIGURE}\s*(?:actividades?|salidas?|sesiones?|entrenamientos?|"
            r"carreras?)\b"
        ),
    ),
    (
        "frequency",
        re.compile(rf"(?i){_LEAD}\b{_FIGURE}\s*(?:veces|d[ií]as?)\s+(?:por|a\s+la)\s+semana\b"),
    ),
)

_SPACE = re.compile(r"\s+")


def _label(raw: Any) -> str:
    return re.sub(_RANGE, "-", str(raw).strip()).replace(",", ".")


def band_phrases(requirements: Sequence[Requirement]) -> tuple[str, ...]:
    """The band labels with the units they are spoken in, as backing text for
    `guardrails.scrub_prose`. Without them its battery pattern excises the `9 horas` out
    of a truthful `6-9 horas` and leaves a dangling `6-`."""
    out: list[str] = []
    for requirement in requirements:
        unit = _BAND_UNITS.get(requirement.key)
        if unit is None or not isinstance(requirement.value, str):
            continue
        head = unit.split()[0]
        out += [f"{requirement.value} {head}", f"{requirement.value} {unit}"]
    return tuple(out)


def scrub_training_prose(text: str, requirements: Sequence[Requirement]) -> str:
    """Excise every training figure that is not a band this turn actually handed over.

    A derived band is a range and the athlete's own corrections are ranges too, so any
    figure that is not one of those labels was produced by the model doing arithmetic on
    them — which is the definition of presenting a derived number as a measured one."""
    if not text:
        return ""
    allowed = {
        _label(str(r.value)) for r in requirements if isinstance(r.value, (str, int))
    }
    spans: list[tuple[int, int, str]] = []
    for kind, pattern in _TRAINING_PATTERNS:
        for match in pattern.finditer(text):
            if _label(match.group("num")) in allowed:
                continue
            spans.append((match.start(), match.end(), kind))

    kept: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if kept and span[0] < kept[-1][1]:
            continue
        kept.append(span)
    if not kept:
        return text

    out = text
    for start, end, _ in sorted(kept, key=lambda s: s[0], reverse=True):
        out = out[:start] + out[end:]
    out = re.sub(r"\s+([.,;:])", r"\1", _SPACE.sub(" ", out)).strip()
    emit(
        # Counts and kinds, never the figure: a trace payload is pasted back into a model's
        # context by the evidence bundle, and the excised text is what must not travel.
        "guardrail.training_figures",
        {"excised": len(kept), "kinds": sorted({k for _, _, k in kept}), "allowed": len(allowed)},
        "guardrail",
    )
    return out


# ── what the model is told about them ─────────────────────────────────────────

DECLARATIONS: list[types.FunctionDeclaration] = [
    types.FunctionDeclaration(
        name=ToolId.GET_TRAINING_SUMMARY.value,
        description=(
            "Lo que el entrenamiento del atleta DEMUESTRA, derivado de sus actividades reales "
            "de Strava por el código de privacidad: disciplina dominante, reparto de deportes, "
            "y bandas de volumen semanal, salida más larga, desnivel y constancia. Cada valor "
            "es un RANGO derivado de contar actividades, nunca una medición, y las actividades "
            "escritas a mano se descartan. No devuelve pulso, ritmo, potencia ni distancia: "
            "este sistema no los lee. Si responde not_eligible no hay cuenta conectada o no "
            "hay permiso, y toca preguntarle a la persona; si responde rate_limited o timeout "
            "no se pudo leer, que no es lo mismo que un historial vacío."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name=ToolId.LIST_COLLECTIONS.value,
        description=(
            "Los tres grupos en los que se divide el catálogo completo de COROS Colombia, con "
            "cuántos productos tiene cada uno y cuántos están disponibles. Es el catálogo "
            "entero, no una página: los conteos son hechos, no estimaciones. Empieza por aquí "
            "cuando no sepas qué vende esta tienda."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name=ToolId.GET_COLLECTION_PRODUCTS.value,
        description=(
            "Los productos de UN grupo: 'relojes', 'correas' o 'accesorios'. El handle tiene "
            "que ser uno de esos tres; cualquier otro devuelve not_eligible con la lista de "
            "los que existen. Una lista vacía significa que ese grupo hoy no tiene nada, no "
            "que haya que sustituirlo con algo de otro grupo."
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
            "compara tus palabras contra el título, los nombres de opción y las etiquetas de "
            "variante, y TODAS tienen que coincidir. No es búsqueda semántica, así que una "
            "frase conversacional no encuentra nada. Como se revisa todo el catálogo, cero "
            "resultados (unavailable) significa que ese producto no existe aquí — no que haya "
            "que reformular."
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
            "La ÚNICA fuente sobre correas y compatibilidad: qué ancho declara COROS para un "
            "dispositivo, qué correas de este catálogo le sirven, y si el dispositivo se vende "
            "en Colombia. Un ancho igual no es compatibilidad y un título no es una ficha "
            "técnica; nunca deduzcas ninguna de las dos. El APEX 4 viene en 42 mm y 46 mm y "
            "llevan correas distintas, así que sin 'case_mm' esta herramienta devuelve la "
            "pregunta en vez de elegir por la persona."
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

# `tools=` takes Tool objects: a bare FunctionDeclaration raises AttributeError inside the
# SDK, before any HTTP call. `gemini.as_tools()` is the one place that knows it.
TOOLS: list[Any] = gemini.as_tools(DECLARATIONS)

DISPATCH: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    ToolId.GET_TRAINING_SUMMARY.value: get_training_summary,
    ToolId.LIST_COLLECTIONS.value: list_collections,
    ToolId.GET_COLLECTION_PRODUCTS.value: get_collection_products,
    ToolId.SEARCH_PRODUCTS.value: search_products,
    ToolId.LOOKUP_DEVICE_COMPAT.value: lookup_device_compat,
}

assert set(DISPATCH) == {t.value for t, s in SURFACES.items() if "huella" in s}, (
    "capability.SURFACES and this dispatch table disagree about Huella's tool surface. "
    "AGENTS.md's maintenance contract moves capability.py, tools.py and prompts.py together."
)


def declared(names: Iterable[str]) -> list[types.FunctionDeclaration]:
    """The subset of the surface a capability verdict cleared. `loop.declarations()` asks
    the map rather than hardcoding a list, so a tool moved out of `SURFACES` disappears
    from the prompt instead of being offered and refused."""
    allowed = set(names)
    return [d for d in DECLARATIONS if d.name in allowed]
