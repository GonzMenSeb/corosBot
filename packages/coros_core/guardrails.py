"""Deterministic guardrails.

A guardrail written in the prompt is a suggestion; a guardrail written in Python is a
guarantee. Everything here is pure and synchronous — no I/O, no clock, no model — and
every verdict emits a trace event at `level="guardrail"` so a reviewer can watch it fire
instead of taking the prompt's word for it.

Two of these are COROS-specific and carry the product:

  * `check_local_availability` answers the question COROS Colombia's catalogue is shaped
    to get wrong. Ten of the fourteen devices in the registry are sold as straps and not
    as watches, so a search for a VERTIX 2 finds six real products and none of them is a
    watch. The honest answer names the absence; it never quietly hands over a different
    watch, and `UnavailableDevice` has no field a substitute could travel in.
  * `check_buy_nothing` makes "buy nothing" reachable. An agent that can only answer with
    products will always find one.

`find_unbacked_claims` guards the failure that is most likely to be seen live: the agent
will not invent *products* — retrieval prevents that — it invents their *properties*.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from coros_core import devices
from coros_core.models import AdviceItem, AdviceKind, Budget, CatalogProduct, Device
from coros_core.money import MoneyError, major_string_to_minor, minor_to_display
from coros_core.trace import emit

# ---------------------------------------------------------------- verdicts


class FieldMismatch(BaseModel):
    """What the model claimed, next to what the feed says. Recorded rather than argued
    with: the rendered item is rebuilt from the feed regardless."""

    product_id: str
    field: str
    claimed: str
    actual: str


class DroppedItem(BaseModel):
    product_id: str = ""
    title: str = ""
    reason: Literal[
        "not_in_catalog", "not_merchandise", "unknown_variant", "variant_ambiguous", "invalid_item"
    ]
    detail: str = ""


class ProvenanceVerdict(BaseModel):
    ok: bool = True
    renderable: tuple[AdviceItem, ...] = ()
    dropped: tuple[DroppedItem, ...] = ()
    mismatches: tuple[FieldMismatch, ...] = ()


class StockRejection(BaseModel):
    product_id: str = ""
    variant_id: str = ""
    title: str = ""
    reason: Literal[
        "not_in_catalog", "unknown_variant", "variant_ambiguous", "out_of_stock", "invalid_item"
    ]
    detail: str = ""


class StockVerdict(BaseModel):
    ok: bool = True
    items: tuple[AdviceItem, ...] = ()
    rejected: tuple[StockRejection, ...] = ()


class SwapCandidate(BaseModel):
    product_id: str
    title: str
    price_minor: int


class BudgetVerdict(BaseModel):
    ok: bool = True
    budget_minor: int | None = None
    total_minor: int = 0
    over_by_minor: int = 0
    nothing_fits: bool = False
    swap_candidates: tuple[SwapCandidate, ...] = ()
    message: str = ""


class LocalAvailabilityVerdict(BaseModel):
    """One device cleared for recommendation.

    `is_available: Literal[True]` is the guarantee, not documentation: there is no way to
    construct this object for a device COROS Colombia does not sell, so an absent watch
    cannot travel through the pipeline wearing a clearance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    name: str
    is_available: Literal[True] = True


class UnavailableDevice(BaseModel):
    """A device the person named that COROS Colombia does not sell.

    Deliberately has no `alternative`/`instead`/`closest` field. A substitution is the
    failure this whole check exists to prevent, and the cheapest way to make one
    impossible is to give it nowhere to sit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    name: str
    straps_listed: bool = False
    statement: str = ""
    tradeoff: str = ""


class LocalAvailabilityReport(BaseModel):
    ok: bool = True
    cleared: tuple[LocalAvailabilityVerdict, ...] = ()
    unavailable: tuple[UnavailableDevice, ...] = ()


BuyNothingReason = Literal[
    "", "inconclusive", "nothing_in_stock", "already_owned", "over_budget"
]


class BuyNothingVerdict(BaseModel):
    buy_nothing: bool = False
    reason: BuyNothingReason = ""
    detail: str = ""
    considered: int = 0

    @property
    def advice_kind(self) -> AdviceKind:
        if self.reason == "inconclusive":
            return "insufficient_evidence"
        return "buy_nothing" if self.buy_nothing else "recommend"


class SpecClaim(BaseModel):
    text: str
    kind: str
    start: int
    end: int


# ---------------------------------------------------------------- provenance


def _index(catalog: Sequence[CatalogProduct]) -> dict[str, CatalogProduct]:
    return {p.product_id: p for p in catalog}


def _pick_variant(product: CatalogProduct, variant_id: str) -> tuple[Any, str]:
    """The variant, or the reason there isn't one. Ambiguity is a question, never a pick:
    `devices.straps_for` refuses the same way, and DecaBot's one live size mistake came
    from a stage that chose the first option rather than asking."""
    if variant_id:
        found = next((v for v in product.variants if v.variant_id == variant_id), None)
        return found, "" if found else "unknown_variant"
    if len(product.variants) == 1:
        return product.variants[0], ""
    return None, "unknown_variant" if not product.variants else "variant_ambiguous"


def check_provenance(
    candidates: Sequence[Mapping[str, Any] | AdviceItem], catalog: Sequence[CatalogProduct]
) -> ProvenanceVerdict:
    """Every field of a rendered item comes from the feed. The model contributes a
    product id, a variant id and its reasoning; a title, a URL or a price it also sent is
    compared and then discarded."""
    by_id = _index(catalog)
    renderable: list[AdviceItem] = []
    dropped: list[DroppedItem] = []
    mismatches: list[FieldMismatch] = []

    for raw in candidates:
        data = raw.model_dump() if isinstance(raw, AdviceItem) else dict(raw)
        product_id = str(data.get("product_id", "") or "")
        claimed_title = str(data.get("title", "") or "")

        product = by_id.get(product_id)
        if product is None:
            dropped.append(
                DroppedItem(
                    product_id=product_id,
                    title=claimed_title,
                    reason="not_in_catalog",
                    detail="no product with this id is in the feed this turn fetched",
                )
            )
            continue
        if product.hidden:
            dropped.append(
                DroppedItem(
                    product_id=product_id,
                    title=product.title,
                    reason="not_merchandise",
                    detail="tagged gwp-hidden: on the feed, not for sale",
                )
            )
            continue

        variant, problem = _pick_variant(product, str(data.get("variant_id", "") or ""))
        if variant is None:
            dropped.append(
                DroppedItem(
                    product_id=product_id,
                    title=product.title,
                    reason=problem,  # type: ignore[arg-type]
                    detail=f"{product.title} has {len(product.variants)} variants and none was named",
                )
            )
            continue

        for field, claimed, actual in (
            ("title", claimed_title, product.title),
            ("product_url", str(data.get("product_url", "") or ""), product.product_url),
            ("price_minor", str(data.get("price_minor", "") or ""), str(variant.price_minor)),
        ):
            if claimed and claimed != actual:
                mismatches.append(
                    FieldMismatch(
                        product_id=product_id, field=field, claimed=claimed, actual=actual
                    )
                )

        try:
            renderable.append(
                AdviceItem(
                    product_id=product.product_id,
                    title=product.title,
                    product_url=product.product_url,
                    image_url=product.image_url,
                    variant_id=variant.variant_id,
                    price_minor=variant.price_minor,
                    rationale=str(data.get("rationale", "") or ""),
                    satisfies=tuple(data.get("satisfies", ()) or ()),
                )
            )
        except ValidationError as e:
            dropped.append(
                DroppedItem(
                    product_id=product_id,
                    title=product.title,
                    reason="invalid_item",
                    detail=str(e)[:300],
                )
            )

    verdict = ProvenanceVerdict(
        ok=not dropped and not mismatches,
        renderable=tuple(renderable),
        dropped=tuple(dropped),
        mismatches=tuple(mismatches),
    )
    emit(
        "guardrail.provenance",
        {
            "renderable": len(verdict.renderable),
            "dropped": [d.model_dump() for d in verdict.dropped],
            "mismatches": [m.model_dump() for m in verdict.mismatches],
        },
        "guardrail",
    )
    return verdict


# ---------------------------------------------------------------- stock


def check_stock(
    candidates: Sequence[Mapping[str, Any] | AdviceItem], catalog: Sequence[CatalogProduct]
) -> StockVerdict:
    """Availability is read out of the feed, never off the candidate. A model that labels
    a sold-out variant `available: true` changes nothing here."""
    by_id = _index(catalog)
    items: list[AdviceItem] = []
    rejected: list[StockRejection] = []

    for raw in candidates:
        data = raw.model_dump() if isinstance(raw, AdviceItem) else dict(raw)
        product_id = str(data.get("product_id", "") or "")
        variant_id = str(data.get("variant_id", "") or "")

        product = by_id.get(product_id)
        if product is None:
            rejected.append(
                StockRejection(
                    product_id=product_id,
                    variant_id=variant_id,
                    title=str(data.get("title", "") or ""),
                    reason="not_in_catalog",
                    detail="not in the feed this turn fetched, so nothing is known about its stock",
                )
            )
            continue

        variant, problem = _pick_variant(product, variant_id)
        if variant is None:
            rejected.append(
                StockRejection(
                    product_id=product_id,
                    title=product.title,
                    reason=problem,  # type: ignore[arg-type]
                    detail=f"{product.title}: no variant was named and there is more than one",
                )
            )
            continue
        if not variant.available:
            rejected.append(
                StockRejection(
                    product_id=product_id,
                    variant_id=variant.variant_id,
                    title=product.title,
                    reason="out_of_stock",
                    detail=f"{product.title} ({variant.label or 'única opción'}) está agotado",
                )
            )
            continue

        if isinstance(raw, AdviceItem):
            items.append(raw)
            continue
        try:
            items.append(AdviceItem(**data))
        except ValidationError as e:
            rejected.append(
                StockRejection(
                    product_id=product_id,
                    variant_id=variant.variant_id,
                    title=product.title,
                    reason="invalid_item",
                    detail=str(e)[:300],
                )
            )

    verdict = StockVerdict(ok=not rejected, items=tuple(items), rejected=tuple(rejected))
    emit(
        "guardrail.stock",
        {"confirmed": len(verdict.items), "rejected": [r.model_dump() for r in verdict.rejected]},
        "guardrail",
    )
    return verdict


# ---------------------------------------------------------------- budget


def check_budget(items: Sequence[AdviceItem], budget: Budget | None = None) -> BudgetVerdict:
    """Integer arithmetic in minor units, done in code. The model is never asked to add.

    `Budget` only ever narrows, so there is no path from here to a wider one — the
    verdict reports the gap and names swaps, and that is all it can do."""
    budget = budget or Budget()
    currency = budget.currency
    cap = budget.max_total_minor
    total = sum(i.price_minor for i in items)
    verdict = BudgetVerdict(budget_minor=cap, total_minor=total)

    def done(v: BudgetVerdict) -> BudgetVerdict:
        emit("guardrail.budget", v.model_dump(), "guardrail")
        return v

    if cap is None:
        verdict.message = f"Selección: {minor_to_display(total, currency)}. Sin presupuesto fijado."
        return done(verdict)

    if not items:
        # "algo para $500.000" that fits nothing must not report "$500.000 por debajo del
        # presupuesto" — an empty selection is the nothing-fits case, not a cheap one.
        verdict.ok = False
        verdict.nothing_fits = True
        verdict.message = (
            f"Nada cabe en {minor_to_display(cap, currency)} — no pude poner un solo "
            "producto en la selección."
        )
        return done(verdict)

    if total <= cap:
        verdict.message = (
            f"{minor_to_display(total, currency)} de {minor_to_display(cap, currency)} — "
            f"{minor_to_display(cap - total, currency)} por debajo del presupuesto."
        )
        return done(verdict)

    verdict.ok = False
    verdict.over_by_minor = total - cap
    cheapest = min(i.price_minor for i in items)
    verdict.nothing_fits = cheapest > cap

    if verdict.nothing_fits:
        verdict.message = (
            f"Nada de esta selección cabe en {minor_to_display(cap, currency)} — lo más "
            f"barato son {minor_to_display(cheapest, currency)}."
        )
        return done(verdict)

    # most expensive first: swapping those closes the gap in the fewest changes
    swaps: list[SwapCandidate] = []
    for i in sorted(items, key=lambda i: i.price_minor, reverse=True):
        swaps.append(
            SwapCandidate(product_id=i.product_id, title=i.title, price_minor=i.price_minor)
        )
        if sum(s.price_minor for s in swaps) >= verdict.over_by_minor:
            break

    verdict.swap_candidates = tuple(swaps)
    verdict.message = (
        f"{minor_to_display(total, currency)} se pasa por "
        f"{minor_to_display(verdict.over_by_minor, currency)} de "
        f"{minor_to_display(cap, currency)}. Cambia: "
        + ", ".join(s.title for s in swaps)
        + "."
    )
    return done(verdict)


# ---------------------------------------------------------------- local availability

_MAX_ALIAS_TOKENS = max(
    len(devices.normalize(alias).split())
    for aliases in devices.ALIASES.values()
    for alias in aliases
)


def devices_named(text: str) -> tuple[Device, ...]:
    """Every device named in prose, in order of first mention.

    `devices.resolve` answers for a whole string and returns one device, so a sentence
    naming two watches loses one. This walks token windows and asks it repeatedly.

    The guard is what makes that safe: a window only counts when extending it by one
    token still resolves to the same device. Without it `"APEX 5"` shrinks to the window
    `"apex"` and comes back as the gen-1 APEX — exactly the substitution
    `devices._names_this_device` exists to refuse."""
    tokens = devices.normalize(text).split()
    found: dict[str, Device] = {}
    for i in range(len(tokens)):
        for length in range(1, _MAX_ALIAS_TOKENS + 1):
            device = devices.resolve(" ".join(tokens[i : i + length]))
            if device is None:
                continue
            wider = devices.resolve(" ".join(tokens[i : i + length + 1]))
            if wider is not None and wider.slug == device.slug:
                found.setdefault(device.slug, device)
    return tuple(found.values())


def _straps_listed(slug: str) -> bool:
    return any(fit.fits(slug) for fit in devices.STRAPS)


def check_local_availability(
    text: str = "", products: Sequence[CatalogProduct] = ()
) -> LocalAvailabilityReport:
    """B1's differentiator. Devices are taken from prose by name and from candidates by
    product id — never from a product title, which is editable copy.

    Both sentences are templates over one device's own name, so the report cannot name a
    replacement even by accident."""
    named: dict[str, Device] = {d.slug: d for d in devices_named(text)}
    for product in products:
        device = devices.device_for_product(product)
        if device is not None:
            named.setdefault(device.slug, device)

    cleared: list[LocalAvailabilityVerdict] = []
    unavailable: list[UnavailableDevice] = []
    for device in named.values():
        if device.sold_locally:
            cleared.append(LocalAvailabilityVerdict(slug=device.slug, name=device.name))
            continue
        straps = _straps_listed(device.slug)
        unavailable.append(
            UnavailableDevice(
                slug=device.slug,
                name=device.name,
                straps_listed=straps,
                statement=f"COROS Colombia no vende el {device.name}.",
                tradeoff=(
                    "Sus correas sí están en el catálogo colombiano, así que una búsqueda "
                    "lo hace parecer disponible. El reloj no lo está, y ningún otro modelo "
                    "de este catálogo es ese reloj."
                    if straps
                    else "El catálogo colombiano no lista nada para él, ni el reloj ni "
                    "accesorios, y ningún otro modelo de este catálogo es ese reloj."
                ),
            )
        )

    report = LocalAvailabilityReport(
        ok=not unavailable, cleared=tuple(cleared), unavailable=tuple(unavailable)
    )
    emit(
        "guardrail.local_availability",
        {
            "cleared": [c.slug for c in report.cleared],
            "unavailable": [u.model_dump() for u in report.unavailable],
        },
        "guardrail",
    )
    return report


# ---------------------------------------------------------------- buy nothing


def check_buy_nothing(
    candidates: Sequence[CatalogProduct],
    *,
    budget: Budget | None = None,
    owned: Sequence[str] = (),
    retrieval_conclusive: bool = True,
) -> BuyNothingVerdict:
    """Is "buy nothing" the honest answer?

    `retrieval_conclusive=False` is the distinction the rest of this system is built to
    keep: a 429 is not a fact about stock, and reporting one as "nothing fits" fabricates
    an inventory claim out of a request that never completed."""
    considered = len(candidates)

    def done(v: BuyNothingVerdict) -> BuyNothingVerdict:
        emit("guardrail.buy_nothing", v.model_dump(), "guardrail")
        return v

    if not retrieval_conclusive:
        return done(
            BuyNothingVerdict(
                reason="inconclusive",
                considered=considered,
                detail="la consulta al catálogo no terminó, así que no sé qué hay",
            )
        )

    purchasable = [p for p in candidates if p.in_stock and not p.hidden]
    if not purchasable:
        return done(
            BuyNothingVerdict(
                buy_nothing=True,
                reason="nothing_in_stock",
                considered=considered,
                detail=f"de {considered} productos mirados, ninguno está disponible ahora",
            )
        )

    matched = [devices.device_for_product(p) for p in purchasable]
    owned_slugs = set(owned)
    if owned_slugs and all(d is not None and d.slug in owned_slugs for d in matched):
        names = ", ".join(sorted({d.name for d in matched if d is not None}))
        return done(
            BuyNothingVerdict(
                buy_nothing=True,
                reason="already_owned",
                considered=considered,
                detail=f"lo único que encaja ya lo tienes: {names}",
            )
        )

    prices = [p.min_price_minor for p in purchasable if p.min_price_minor is not None]
    cap = budget.max_total_minor if budget else None
    if cap is not None and prices and min(prices) > cap:
        currency = budget.currency if budget else "COP"
        return done(
            BuyNothingVerdict(
                buy_nothing=True,
                reason="over_budget",
                considered=considered,
                detail=(
                    f"nada cabe en {minor_to_display(cap, currency)}; lo más barato "
                    f"son {minor_to_display(min(prices), currency)}"
                ),
            )
        )

    return done(BuyNothingVerdict(considered=considered))


# ---------------------------------------------------------------- unbacked claims

_WS = re.compile(r"\s+")
_DASHES = {0x2212: "-", 0x2013: "-", 0x2014: "-", 0x2010: "-", 0x2011: "-"}
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")

# Every pattern below was written against COROS Colombia's own Spanish copy in
# `fixtures/products.json` — `28 horas`, `80 días en espera`, `3 ATM`, `IP67`,
# `un peso de solo 5,6 g`, `-10 °C a 60 °C`, `Titanio`, `zafiro`, `Sistemas GNSS
# Multi-Frecuencia`. `frecuencia` alone is not a claim: it is `frecuencia cardíaca` in
# most of the catalogue.
_CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("battery", re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:h|hrs?|horas?|hours?)\b")),
    (
        "battery",
        re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:d[ií]as?|days?|semanas?|weeks?|meses|months?)\b"),
    ),
    ("battery", re.compile(r"(?i)\b\d+\s*mah\b")),
    (
        "water",
        re.compile(
            r"(?i)\b(?:sumergible|impermeable|resistente)\s+(?:hasta\s+)?"
            r"(?P<key>\d+(?:[.,]\d+)?\s*(?:m|metros?|atm|bar))\b"
        ),
    ),
    (
        "water",
        re.compile(
            r"(?i)\b(?P<key>\d+(?:[.,]\d+)?\s*(?:m|metros?))\s+(?:de\s+)?"
            r"(?:profundidad|bajo\s+el\s+agua)"
        ),
    ),
    ("water", re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:atm|bar)\b")),
    ("water", re.compile(r"(?i)\bip\s?[0-9x]{2}\b")),
    ("water", re.compile(r"(?i)\bwr\s?\d{2,3}\b")),
    (
        "water",
        re.compile(
            r"(?i)\b(?:sumergible|impermeable|waterproof|water[- ]?resistant|"
            r"resistencia\s+al\s+agua|resistente\s+al\s+agua)\b"
        ),
    ),
    ("weight", re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:g|gr|gramos?|grams?|kg|oz|onzas?)\b")),
    ("dimension", re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:mm|cm)\b")),
    ("temperature", re.compile(r"(?i)-?\s?\d+(?:[.,]\d+)?\s*[°º]\s*[cf]\b")),
    ("temperature", re.compile(r"(?i)-?\s?\d+(?:[.,]\d+)?\s*grados?\b")),
    (
        "material",
        re.compile(
            r"(?i)\b(?:titanio|titanium|zafiro|sapphire|nylon|nailon|silicona|silicone|"
            r"acero\s+inoxidable|stainless|fibra\s+de\s+(?:vidrio|carbono)|policarbonato|"
            r"cuero|leather)\b"
        ),
    ),
    (
        "gnss",
        re.compile(
            r"(?i)\b(?:gnss|glonass|galileo|beidou|qzss|doble\s+frecuencia|"
            r"multi[- ]?frecuencia|dual[- ]?band|multibanda)\b"
        ),
    ),
    ("price", re.compile(r"\$\s?\d(?:[\d.,]*\d)?")),
    ("price", re.compile(r"(?i)\b\d(?:[\d.,]*\d)?\s*(?:cop|pesos)\b")),
    ("price", re.compile(r"(?i)\bcop\s*\$?\s?\d(?:[\d.,]*\d)?")),
    # Unbacked BY CONSTRUCTION, so this kind never consults the backing text: the feed's
    # `compare_at_price` is deliberately unmapped, so no pre-discount number reaches the
    # model at all. A true discount still has no source in this pipeline.
    ("discount", re.compile(r"(?i)\b\d+\s*%\s*(?:de\s+)?(?:descuento|dcto|off|discount)\b")),
    (
        "discount",
        re.compile(
            r"(?i)\b(?:antes|precio\s+(?:de\s+)?lista|precio\s+normal|rebajado\s+de|was|rrp|"
            r"msrp)\s*:?\s*\$?\s?\d(?:[\d.,]*\d)?"
        ),
    ),
    ("discount", re.compile(r"(?i)\b(?:ahorra|ahorras|save)\s*\$?\s?\d(?:[\d.,]*\d)?")),
    (
        "discount",
        re.compile(
            r"(?i)\b(?:en\s+oferta|oferta\s+especial|liquidaci[oó]n|rebaja|descuento|"
            r"on\s+sale|clearance)\b"
        ),
    ),
]

_UNBACKABLE = {"discount"}

# Retrieval-derived fields ONLY. `AdviceItem.rationale` and `Advice.explanation` are
# model prose — including them would let the model back its own invention by writing it
# twice. `Device.note` is curated with a line number, so it counts.
_BACKING_FIELDS = (
    "title",
    "handle",
    "description",
    "product_type",
    "vendor",
    "name",
    "note",
    "label",
    "sku",
)


def _get(obj: Any, field: str) -> Any:
    return obj.get(field) if isinstance(obj, Mapping) else getattr(obj, field, None)


def _normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).translate(_DASHES).lower()
    s = s.replace("°", "").replace("º", "")
    # es-CO writes `5,6 g`; a model writing `5.6 g` is quoting the same fact.
    return _WS.sub(" ", _DECIMAL_COMMA.sub(".", s))


def _compact(s: str) -> str:
    """Fold to a comparison key. A minus in front of a digit is meaning, not punctuation:
    `-10 °C` must not compare equal to a description that says `10 °C`."""
    return re.sub(r"[\s\-]", "", re.sub(r"-(?=\d)", "neg", _normalise(s)))


def _backing_text(items: Sequence[Any]) -> str:
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
            continue
        parts.extend(str(v) for f in _BACKING_FIELDS if isinstance(v := _get(item, f), str))
        tags = _get(item, "tags")
        if isinstance(tags, (tuple, list)):
            parts.extend(str(t) for t in tags)
        for variant in _get(item, "variants") or ():
            parts.extend(
                str(v) for f in _BACKING_FIELDS if isinstance(v := _get(variant, f), str)
            )
    return _compact(" | ".join(parts))


_PRICE_CLEAN = re.compile(r"[^\d.,]")


def _price_minor(text: str) -> int | None:
    """es-CO: `.` groups thousands and `,` is the decimal mark, which is how
    `minor_to_display` prints and therefore how the model sees prices. A US-formatted
    `$1,099,000` is unreadable under that rule and comes back None — flagged as unbacked
    rather than guessed at, because the two readings differ by a factor of a thousand."""
    digits = _PRICE_CLEAN.sub("", text)
    if not digits:
        return None
    try:
        return major_string_to_minor(digits.replace(".", "").replace(",", "."))
    except MoneyError:
        return None


def _allowed_prices(items: Sequence[Any], extra: Iterable[int]) -> set[int]:
    """A price in prose has to be one the code computed: a variant price, an item price,
    the selection total, or a figure the caller whitelists (a budget)."""
    prices: list[int] = []
    for item in items:
        for holder in (item, *(_get(item, "variants") or ())):
            price = _get(holder, "price_minor")
            if isinstance(price, int) and not isinstance(price, bool):
                prices.append(price)
    allowed = {*prices, *extra}
    if prices:
        allowed.add(sum(prices))
    return allowed


def find_unbacked_claims(
    text: str, items: Sequence[Any], *, allowed_minor: Iterable[int] = ()
) -> list[SpecClaim]:
    """Spec-shaped claims in model prose that no retrieved field backs.

    No unit conversion is attempted: `100 m` is unbacked even where a description says
    `10 ATM`, because the model did not read that off the JSON — it did the arithmetic,
    and arithmetic it does unprompted is arithmetic nothing verified."""
    backing = _backing_text(items)
    prices = _allowed_prices(items, allowed_minor)
    # 1:1 character substitution, so match offsets stay valid against the original text
    probe = text.translate(_DASHES)

    hits: list[SpecClaim] = []
    for kind, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(probe):
            if kind == "price" and _price_minor(match.group(0)) in prices:
                continue
            if kind not in _UNBACKABLE and kind != "price":
                # `key` is the measurement itself; matching the framing words too
                # ("sumergible hasta 50 m") never finds backing for a stated spec.
                if _compact(match.groupdict().get("key") or match.group(0)) in backing:
                    continue
            raw = text[match.start() : match.end()]
            lead = len(raw) - len(raw.lstrip())
            body = raw.strip()
            hits.append(
                SpecClaim(
                    text=body,
                    kind=kind,
                    start=match.start() + lead,
                    end=match.start() + lead + len(body),
                )
            )

    claims: list[SpecClaim] = []
    for claim in sorted(hits, key=lambda c: (c.start, -(c.end - c.start))):
        if claims and claim.start < claims[-1].end:
            continue
        claims.append(claim)
    return claims


def scrub_prose(text: str, items: Sequence[Any], *, allowed_minor: Iterable[int] = ()) -> str:
    """Excise unbacked spec claims from model prose. Detection is the deliverable — the
    claim phrase goes and the surrounding reasoning stays, because reasoning is the part
    the model is actually for."""
    if not text:
        return ""

    claims = find_unbacked_claims(text, items, allowed_minor=allowed_minor)
    if not claims:
        return text

    out = text
    for claim in sorted(claims, key=lambda c: c.start, reverse=True):
        out = out[: claim.start] + out[claim.end :]
    out = re.sub(r"\s+([.,;:])", r"\1", _WS.sub(" ", out))
    out = re.sub(r"^[\s,;:.]+", "", out).strip()

    emit(
        "guardrail.prose",
        {
            "claims": [c.model_dump() for c in claims],
            "kinds": sorted({c.kind for c in claims}),
        },
        "guardrail",
    )
    return out
