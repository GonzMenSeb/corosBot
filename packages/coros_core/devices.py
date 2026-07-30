"""The curated device registry: the only place a COROS device is identified and the only
place a strap width is decided.

Two tables, both hand-authored from `https://coros.com.co/products.json?limit=250` on
30 Jul 2026, and both auditable against it by `audit()`. Nothing here is inferred at
run time, which is the point: a width the model produces is a guess, and a width from
this file is a fact with a line number.

Four rules make it the grounding floor. Do not "simplify" any of them:

  * THE JOIN IS THE PRODUCT ID, with the handle as a second key, and NEVER
    `product_type`. That field is empty on 24 of 45 live products including the PACE 4,
    and on the DURA it says `Relojes GPS` for a bike computer — COROS's own homepage
    labels that product card `alt="Ciclocomputador COROS DURA"`. Enforced by an AST scan
    in `tests/test_devices.py`: a docstring may name the field, code may not read it.
  * A WIDTH IS RECORDED ONLY WHERE COROS STATES IT IN A TITLE OR A DESCRIPTION. The
    handles `20mm-silicone-band` and `20mm-nylon-band` claim 20 mm and no title or
    description does, so `pace-2` and `apex-2` carry no width at all. Handles lie here:
    `correa-de-nylon-de-24-mm-morada-para-apex-4-46-copia` is a 22 mm silicone strap for
    the APEX 4 42 (`AGENTS.md`).
  * COMPATIBILITY IS CURATED PER PRODUCT AND NEVER DERIVED FROM A WIDTH. COROS sells a
    24 mm strap "solo compatible con el APEX 4 46mm" and, separately, 24 mm NOMAD
    straps. Equal width, and the vendor says they do not swap. `strap_mm` on a strap row
    exists to be cross-checked against the device it fits, not to match on.
  * AMBIGUITY RAISES. The APEX 4 ships in 42 mm and 46 mm and they take different
    straps, so "a strap for my APEX 4" is a question, not a request. Returning `()` there
    would read as "COROS sells no APEX 4 straps", which is false.

The strap widths themselves come from four live descriptions that read "Solo compatible
con el APEX 4 42mm, PACE 4, PACE Pro, PACE 3, APEX 2 Pro, APEX Pro … Ancho: 22 mm",
from "Solo compatible con el APEX 4 46mm … Ancho: 24 mm", from three NOMAD strap titles
that say "24mm", and from two VERTIX 2/2S titles that say "26 mm".

Ten of the fourteen devices here are NOT sold in Colombia: the feed lists straps for
them and no watch SKU. That is six more than the plan for this module assumed — PACE 2,
APEX 2, APEX Pro and the two gen-1 watches are in the same position — and it is the
whole of B1's refusal path, so it is recorded in `AGENTS.md` rather than left implicit.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict

from coros_core.models import CatalogProduct, CatalogVariant, Device, DeviceCase


class DeviceError(Exception):
    """Nothing in this module answers a question it cannot answer from the tables."""


class UnknownDevice(DeviceError):
    pass


class CaseUnspecified(DeviceError):
    """The device ships in more than one case and the caller named none. Ask the person."""


class UnknownCase(DeviceError):
    """A case size this device does not ship in."""


Material = Literal["silicona", "nylon", "mixto"]


class DeviceFit(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    # Set only where COROS names a case: a 22 mm strap fits the APEX 4 42 and not the 46.
    case_mm: int | None = None


class StrapFit(BaseModel):
    """One strap product and the devices COROS says it fits, in COROS's words.

    `strap_mm` is `None` unless a title or a description states the width. `evidence`
    quotes what was read, so a curation error is reviewable without a second fetch."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    handle: str
    material: Material
    devices: tuple[DeviceFit, ...]
    strap_mm: int | None = None
    evidence: str = ""

    def fits(self, slug: str, case_mm: int | None = None) -> bool:
        return any(
            f.slug == slug and (case_mm is None or f.case_mm is None or f.case_mm == case_mm)
            for f in self.devices
        )


DEVICES: tuple[Device, ...] = (
    Device(
        slug="pace-4",
        name="COROS PACE 4",
        sold_locally=True,
        cases=(DeviceCase(strap_mm=22),),
        product_ids=("7752529543211",),
        handles=("coros-pace-4",),
        note="22 mm: four strap descriptions list PACE 4 under `Ancho: 22 mm`. Empty "
        "product_type on the flagship watch.",
    ),
    Device(
        slug="apex-4",
        name="COROS APEX 4",
        sold_locally=True,
        cases=(DeviceCase(case_mm=42, strap_mm=22), DeviceCase(case_mm=46, strap_mm=24)),
        product_ids=("7704999428139",),
        handles=("coros-apex-4",),
        note="The only device with two cases, carried in its `Tamaño` option: 42 mm takes "
        "22 mm, 46 mm takes 24 mm, both stated in the straps' own descriptions.",
    ),
    Device(
        slug="nomad",
        name="COROS NOMAD",
        sold_locally=True,
        cases=(DeviceCase(strap_mm=24),),
        product_ids=("7426932899883",),
        handles=("coros-nomad",),
        note="24 mm: three silicone strap titles read `24mm Coros NOMAD`. The vendor does "
        "not list them for the APEX 4 46 despite the shared width.",
    ),
    Device(
        slug="dura",
        name="COROS DURA",
        sold_locally=True,
        product_ids=("7251062620203",),
        handles=("coros-dura",),
        note="A bike computer, not a watch: COROS's homepage labels the card "
        '`alt="Ciclocomputador COROS DURA"` while the feed files it under `Relojes GPS`. '
        "No strap — no product in the feed lists one for it.",
    ),
    Device(
        slug="pace-pro",
        name="COROS PACE Pro",
        sold_locally=False,
        cases=(DeviceCase(strap_mm=22),),
        note="Straps and a charging adapter, no watch SKU. 22 mm stated on "
        "`bandas-pace-pro-nylon` as `Ancho: 22 mm`.",
    ),
    Device(
        slug="pace-3",
        name="COROS PACE 3",
        sold_locally=False,
        cases=(DeviceCase(strap_mm=22),),
        note="Straps only, no watch SKU. 22 mm stated on the PACE Pro | PACE 3 nylon strap.",
    ),
    Device(
        slug="pace-2",
        name="COROS PACE 2",
        sold_locally=False,
        note="Straps only, no watch SKU. No width: only the handles `20mm-silicone-band` "
        "and `20mm-nylon-band` say 20 mm, and a handle is not a source.",
    ),
    Device(
        slug="apex-2",
        name="COROS APEX 2",
        sold_locally=False,
        note="Straps only, no watch SKU, shared with the PACE 2. No width for the same "
        "reason: the claim lives in a handle.",
    ),
    Device(
        slug="apex-2-pro",
        name="COROS APEX 2 Pro",
        sold_locally=False,
        cases=(DeviceCase(strap_mm=22),),
        note="Straps only, no watch SKU. 22 mm: the APEX 4 42 straps name it in "
        "`Solo compatible con … APEX 2 Pro, APEX Pro` under `Ancho: 22 mm`.",
    ),
    Device(
        slug="apex-pro",
        name="COROS APEX Pro",
        sold_locally=False,
        cases=(DeviceCase(strap_mm=22),),
        note="Straps only, no watch SKU. 22 mm, from the same two descriptions.",
    ),
    Device(
        slug="apex",
        name="COROS APEX",
        sold_locally=False,
        note="Gen 1. Named only in the chargers' copy (`PACE 2/APEX/APEX Pro/VERTIX/"
        "VERTIX 2`); no watch SKU and no strap product names it, so no width.",
    ),
    Device(
        slug="vertix-2",
        name="COROS VERTIX 2",
        sold_locally=False,
        cases=(DeviceCase(strap_mm=26),),
        note="Six strap products, no watch SKU — the case B1 has to answer without "
        "substituting. 26 mm stated in two titles.",
    ),
    Device(
        slug="vertix-2s",
        name="COROS VERTIX 2S",
        sold_locally=False,
        cases=(DeviceCase(strap_mm=26),),
        note="Shares the VERTIX 2's 26 mm straps, per the `Vertix 2/2S` titles. No watch SKU.",
    ),
    Device(
        slug="vertix",
        name="COROS VERTIX",
        sold_locally=False,
        note="Gen 1. Named only in the chargers' copy; no watch SKU, no strap, no width.",
    ),
)

# Aliases are matched after normalisation, so `pace4`, `PACE-4` and `Pace 4` are one
# entry. Longest match wins: "apex 2 pro" contains "apex 2" contains "apex", and all
# three are different watches with different straps.
ALIASES: dict[str, tuple[str, ...]] = {
    "pace-4": ("pace 4",),
    "apex-4": ("apex 4",),
    "nomad": ("nomad",),
    "dura": ("dura",),
    "pace-pro": ("pace pro",),
    "pace-3": ("pace 3",),
    "pace-2": ("pace 2",),
    "apex-2": ("apex 2",),
    "apex-2-pro": ("apex 2 pro",),
    "apex-pro": ("apex pro",),
    "apex": ("apex",),
    "vertix-2": ("vertix 2",),
    # `vertix 2s`, `vertix2s` and `VERTIX 2S` all normalise to this, and it beats
    # `vertix 2` on length — the 2S is a different watch that happens to share straps.
    "vertix-2s": ("vertix 2 s",),
    "vertix": ("vertix",),
}

# Which variant option carries the case size, for the one device that has two. Read off
# the product's own `option_names`, never assumed to be at a fixed position.
CASE_OPTION: dict[str, str] = {"apex-4": "Tamaño"}

_APEX_4_42 = (
    DeviceFit(slug="apex-4", case_mm=42),
    DeviceFit(slug="pace-4"),
    DeviceFit(slug="pace-pro"),
    DeviceFit(slug="pace-3"),
    DeviceFit(slug="apex-2-pro"),
    DeviceFit(slug="apex-pro"),
)
_TWENTY_TWO = (
    "desc: `Solo compatible con el APEX 4 42mm, PACE 4, PACE Pro, PACE 3, APEX 2 Pro, "
    "APEX Pro` · `Ancho: 22 mm`"
)
_TWENTY_FOUR = "desc: `Solo compatible con el APEX 4 46mm` · `Ancho: 24 mm`"

STRAPS: tuple[StrapFit, ...] = (
    StrapFit(
        product_id="8014580744235",
        handle="correa-de-24mm-silicona-blanco-coros-apex-4-46-mm",
        material="silicona",
        devices=(DeviceFit(slug="apex-4", case_mm=46),),
        strap_mm=24,
        evidence=_TWENTY_FOUR,
    ),
    StrapFit(
        product_id="7930745749547",
        handle="correa-de-nylon-de-24-mm-morada-para-apex-4-46-copia",
        material="silicona",
        devices=_APEX_4_42,
        strap_mm=22,
        evidence="title: `Correa de 22mm silicona Blanco COROS APEX 4 42 mm` · " + _TWENTY_TWO,
    ),
    StrapFit(
        product_id="7930746109995",
        handle="correa-de-22mm-silicona-negra-coros-apex-4-42-mm-copia",
        material="silicona",
        devices=_APEX_4_42,
        strap_mm=22,
        evidence=_TWENTY_TWO,
    ),
    StrapFit(
        product_id="7940748935211",
        handle="correa-de-nylon-de-24-mm-verde-para-apex-4-46mm",
        material="nylon",
        devices=(DeviceFit(slug="apex-4", case_mm=46),),
        strap_mm=24,
        evidence="desc: `Solo compatible con el APEX 4 46` · `Ancho: 24 mm`",
    ),
    StrapFit(
        product_id="7914943512619",
        handle="correa-de-apex-4-46mm-silicona",
        material="silicona",
        devices=(DeviceFit(slug="apex-4", case_mm=46),),
        evidence="title: `Correa de Apex 4 - 46mm Silicona` — 46 mm is the case, and the "
        "vendor states no width here, so none is recorded",
    ),
    StrapFit(
        product_id="7926626189355",
        handle="correa-de-silicona-verde-24mm-coros-nomad",
        material="silicona",
        devices=(DeviceFit(slug="nomad"),),
        strap_mm=24,
        evidence="title: `Correa de silicona verde  24mm Coros NOMAD` · `Ancho: 24 mm`",
    ),
    StrapFit(
        product_id="7926632153131",
        handle="correa-de-silicona-cafe-24mm-coros-nomad",
        material="silicona",
        devices=(DeviceFit(slug="nomad"),),
        strap_mm=24,
        evidence="title: `Correa de silicona café  24mm Coros NOMAD` · `Ancho: 24 mm`",
    ),
    StrapFit(
        product_id="7926640705579",
        handle="correa-de-silicona-gris-oscuro-24mm-coros-nomad",
        material="silicona",
        devices=(DeviceFit(slug="nomad"),),
        strap_mm=24,
        evidence="title: `Correa de silicona Gris Oscuro  24mm Coros NOMAD` · `Ancho: 24 mm`",
    ),
    StrapFit(
        product_id="7926644539435",
        handle="correa-de-nylon-de-22-mm-amarilla-para-apex-4-42mm",
        material="nylon",
        devices=_APEX_4_42,
        strap_mm=22,
        evidence=_TWENTY_TWO,
    ),
    StrapFit(
        product_id="7926686744619",
        handle="correa-de-nylon-de-24-mm-morada-para-apex-4-42mm",
        material="nylon",
        devices=_APEX_4_42,
        strap_mm=22,
        evidence="title: `Correa de nylon de 22 mm morada para Apex 4 42mm` — the handle "
        "says 24 mm and the title says 22 · " + _TWENTY_TWO,
    ),
    StrapFit(
        product_id="7926706438187",
        handle="correa-de-nylon-de-24-mm-morada-para-apex-4-46",
        material="nylon",
        devices=(DeviceFit(slug="apex-4", case_mm=46),),
        strap_mm=24,
        evidence="desc: `Solo compatible con el APEX 4 46` · `Ancho: 24 mm`",
    ),
    StrapFit(
        product_id="7814703185963",
        handle="banda-silicona-pace-4-pace-3",
        material="silicona",
        devices=(DeviceFit(slug="pace-4"), DeviceFit(slug="pace-3")),
        evidence="title: `Banda silicona | Pace 4 | Pace 3` — no width stated",
    ),
    StrapFit(
        product_id="7807476924459",
        handle="bandas-pace-4-pace-pro-nylon-silicona",
        material="nylon",
        devices=(
            DeviceFit(slug="pace-4"),
            DeviceFit(slug="pace-3"),
            DeviceFit(slug="pace-pro"),
            DeviceFit(slug="apex-2-pro"),
            DeviceFit(slug="apex-4", case_mm=42),
        ),
        evidence="title: `Correa Nylon | Pace 4 | Pace 3 | Pace PRO | Apex 2 pro | "
        "Apex 4 (42mm)` — no width stated",
    ),
    StrapFit(
        product_id="7807482888235",
        handle="correa-nylon-nomad-copia",
        material="nylon",
        devices=(
            DeviceFit(slug="apex-4", case_mm=42),
            DeviceFit(slug="pace-4"),
            DeviceFit(slug="pace-3"),
            DeviceFit(slug="pace-pro"),
            DeviceFit(slug="apex-2-pro"),
        ),
        evidence="title: `Correa de Apex 4 - 42mm | Edición Kilian Jornet`, desc ends "
        "`Pace 3 | Pace PRO | Apex 2 pro | Pace 4`. The handle says NOMAD; it is not one.",
    ),
    StrapFit(
        product_id="7798685073451",
        handle="correa-nylon-nomad",
        material="nylon",
        devices=(DeviceFit(slug="nomad"),),
        evidence="title: `Correa Nylon Nomad` — no width stated",
    ),
    StrapFit(
        product_id="7430658588715",
        handle="correa-de-silicona-coros-pace-pro",
        material="silicona",
        devices=(DeviceFit(slug="pace-pro"),),
        evidence="title: `Correa  silicona COROS PACE Pro` — no width stated",
    ),
    StrapFit(
        product_id="6942124343339",
        handle="20mm-silicone-band",
        material="silicona",
        devices=(DeviceFit(slug="apex-2"), DeviceFit(slug="pace-2")),
        evidence="title: `Correa APEX 2, PACE 2  | Silicona`. The 20 mm is in the handle "
        "only, so it is not recorded.",
    ),
    StrapFit(
        product_id="7422219452459",
        handle="correa-de-silicona-de-26-mm-vertix-2-2s",
        material="silicona",
        devices=(DeviceFit(slug="vertix-2"), DeviceFit(slug="vertix-2s")),
        strap_mm=26,
        evidence="title: `Correa de silicona de 26 mm Vertix 2/2S`",
    ),
    StrapFit(
        product_id="7422213193771",
        handle="correa-26mm-nylon-vertix-2-2s",
        material="nylon",
        devices=(DeviceFit(slug="vertix-2"), DeviceFit(slug="vertix-2s")),
        strap_mm=26,
        evidence="title: `Correa 26mm Nylon Vertix 2/2S`",
    ),
    StrapFit(
        product_id="6942123065387",
        handle="20mm-nylon-band",
        material="nylon",
        devices=(DeviceFit(slug="pace-2"), DeviceFit(slug="apex-2")),
        evidence="title: `Correa PACE 2 y APEX 2 | Nylon`. Width in the handle only.",
    ),
    StrapFit(
        product_id="6942114349099",
        handle="22mm-nylon-band",
        material="nylon",
        devices=(DeviceFit(slug="apex-2-pro"), DeviceFit(slug="apex-pro")),
        evidence="title: `Correa APEX 2 Pro, APEX Pro | Nylon`. Width in the handle only; "
        "the 22 mm for both devices is stated on the APEX 4 42 straps instead.",
    ),
    StrapFit(
        product_id="6942119428139",
        handle="22mm-silicone-band",
        material="silicona",
        devices=(DeviceFit(slug="apex-2-pro"), DeviceFit(slug="apex-pro")),
        evidence="title: `Correa APEX 2 Pro, APEX Pro | Silicona`. Width in the handle only.",
    ),
    StrapFit(
        product_id="7316916633643",
        handle="bandas-pace-pro-nylon",
        material="nylon",
        devices=(DeviceFit(slug="pace-pro"), DeviceFit(slug="pace-3")),
        strap_mm=22,
        evidence="title: `Correa PACE Pro | PACE 3 | Nylon` · desc: `Material: nailon "
        "Ancho: 22 mm` — the one strap that states its own width in full",
    ),
    StrapFit(
        product_id="7201350418475",
        handle="bandas-pace-3",
        material="mixto",
        devices=(DeviceFit(slug="pace-3"), DeviceFit(slug="pace-pro")),
        evidence="title: `Correa Pace 3 | PACE Pro | Nylon - Silicona` — the material is "
        "per variant (`Blanco/Nylon`, `Negro/Silicona`); no width stated",
    ),
    StrapFit(
        product_id="6942096326699",
        handle="26mm-silicone-band",
        material="silicona",
        devices=(DeviceFit(slug="vertix-2"),),
        evidence="title: `Correa Vertix 2 | Silicona` — the VERTIX 2 only, unlike the "
        "`Vertix 2/2S` products. Width in the handle only.",
    ),
    StrapFit(
        product_id="6942092066859",
        handle="26mm-nylon-band",
        material="nylon",
        devices=(DeviceFit(slug="vertix-2"),),
        evidence="title: `Correa Vertix 2 | Nylon`. Width in the handle only.",
    ),
)

BY_SLUG: dict[str, Device] = {d.slug: d for d in DEVICES}
_BY_PRODUCT_ID: dict[str, Device] = {pid: d for d in DEVICES for pid in d.product_ids}
_BY_HANDLE: dict[str, Device] = {h: d for d in DEVICES for h in d.handles}
_STRAP_BY_PRODUCT_ID: dict[str, StrapFit] = {s.product_id: s for s in STRAPS}
_STRAP_BY_HANDLE: dict[str, StrapFit] = {s.handle: s for s in STRAPS}

# `46mm`, `46 mm`, `Tamaño: 46mm` — a case size, and never a strap width.
_CASE_MM = re.compile(r"^(?:tamano\s*)?(\d{2})\s*mm$")
_MM_IN_PROSE = re.compile(r"(\d{2})\s*mm\b")

# A letter/digit boundary becomes a space so `pace4`, `PACE-4` and `Pace 4` are one key.
_SPLIT = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
_DIGIT = re.compile(r"\d")


def normalize(text: str | None) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return _NOT_ALNUM.sub(" ", _SPLIT.sub(" ", plain).lower()).strip()


def get(slug: str) -> Device | None:
    return BY_SLUG.get(slug)


def require(slug: str) -> Device:
    device = BY_SLUG.get(slug)
    if device is None:
        raise UnknownDevice(
            f"{slug!r} is not in the device registry. Add a row with the evidence it was "
            "read from; do not answer for a device this file has never seen."
        )
    return device


def sold_locally() -> tuple[Device, ...]:
    return tuple(d for d in DEVICES if d.sold_locally)


def not_sold_locally() -> tuple[Device, ...]:
    return tuple(d for d in DEVICES if not d.sold_locally)


def _names_this_device(alias: str, padded: str) -> bool:
    """A family name followed by a number is a DIFFERENT watch, and probably one COROS
    has not launched here. `apex 5` must not come back as the gen-1 APEX, so an alias
    carrying no model number of its own is refused a numeric suffix. `apex 4` carries
    one, so `apex 4 46mm` still matches it."""
    token = f" {alias} "
    numbered = bool(_DIGIT.search(alias))
    start = 0
    while (found := padded.find(token, start)) != -1:
        following = padded[found + len(token) :].split(" ", 1)[0]
        if numbered or not following.isdigit():
            return True
        start = found + 1
    return False


def resolve(text: str | None) -> Device | None:
    """A device named in prose, or None. Longest alias wins; nothing is fuzzy-matched,
    so an unknown name comes back as unknown instead of as the nearest watch."""
    padded = f" {normalize(text)} "
    best: tuple[int, str] | None = None
    for slug, aliases in ALIASES.items():
        for alias in aliases:
            token = normalize(alias)
            if _names_this_device(token, padded) and (best is None or len(token) > best[0]):
                best = (len(token), slug)
    return BY_SLUG[best[1]] if best else None


def resolve_with_case(text: str | None) -> tuple[Device, int | None] | None:
    """The device and, if the sentence names one this device actually ships in, its case
    size. A size the device does not ship in is dropped: "my APEX 4 50mm" is an APEX 4
    with no case, which is a question worth asking rather than a fact worth keeping."""
    device = resolve(text)
    if device is None:
        return None
    known = {c.case_mm for c in device.cases if c.case_mm is not None}
    named = next(
        (mm for m in _MM_IN_PROSE.finditer(normalize(text)) if (mm := int(m.group(1))) in known),
        None,
    )
    return device, named


def device_for_product(product: CatalogProduct) -> Device | None:
    """The id first, because Shopify ids are immutable and handles are edited."""
    return _BY_PRODUCT_ID.get(product.product_id) or _BY_HANDLE.get(product.handle)


def strap_fit_for_product(product: CatalogProduct) -> StrapFit | None:
    return _STRAP_BY_PRODUCT_ID.get(product.product_id) or _STRAP_BY_HANDLE.get(product.handle)


def _case_mm_in_label(product: CatalogProduct, variant: CatalogVariant) -> int | None:
    """What the label SAYS, registry or no registry. Only `audit()` wants this: a size
    COROS ships and this file has never heard of is different drift from a label that
    stopped naming a size at all, and the public function below cannot tell them apart.

    `option_names` drops Shopify's `Title` placeholder, so it is the product's real
    options in order and the label's components line up with it — which holds only
    because no live product has a real option called `Title`."""
    device = device_for_product(product)
    if device is None:
        return None
    option = CASE_OPTION.get(device.slug)
    if option is None or option not in product.option_names:
        return None
    parts = [p.strip() for p in variant.label.split("/")]
    index = product.option_names.index(option)
    if index >= len(parts):
        return None
    found = _CASE_MM.match(normalize(parts[index]))
    return int(found.group(1)) if found else None


def case_mm_for_variant(product: CatalogProduct, variant: CatalogVariant) -> int | None:
    """The case size this variant is, read off COROS's own label and refused unless the
    registry ships it — a 50 mm APEX 4 has no strap width here, and reporting the size
    without one invites the model to supply the missing number."""
    case_mm = _case_mm_in_label(product, variant)
    device = device_for_product(product)
    if case_mm is None or device is None or device.strap_mm_for(case_mm) is None:
        return None
    return case_mm


def strap_width(device: Device, case_mm: int | None = None) -> int | None:
    """The lug width COROS states for this device, or None where it states none.

    None is "the vendor never said" — the PACE 2's width lives in a handle and handles
    lie — or "this device takes no strap", which is the DURA. Neither is a licence to
    reach for a plausible number."""
    if case_mm is None and len(device.cases) > 1:
        raise CaseUnspecified(
            f"the {device.name} ships in "
            f"{', '.join(f'{c.case_mm} mm' for c in device.cases)} and they take different "
            "straps. Ask which one; do not pick."
        )
    return device.strap_mm_for(case_mm)


def straps_for(
    products: tuple[CatalogProduct, ...] | list[CatalogProduct],
    slug: str,
    *,
    case_mm: int | None = None,
    available_only: bool = True,
) -> tuple[CatalogProduct, ...]:
    """The strap products COROS lists for this device, in registry order.

    A device absent from the Colombian catalogue can still have straps in it — the
    VERTIX 2 has six — which is why this takes a slug and not a product."""
    device = require(slug)
    if case_mm is None and len({c.case_mm for c in device.cases}) > 1:
        raise CaseUnspecified(
            f"which {device.name}? The 42 mm and the 46 mm take different straps, and "
            "answering with neither would read as 'COROS sells none'."
        )
    if case_mm is not None and device.cases and device.strap_mm_for(case_mm) is None:
        raise UnknownCase(f"the registry has no {case_mm} mm {device.name}")

    by_id = {p.product_id: p for p in products}
    by_handle = {p.handle: p for p in products}
    found = []
    for strap in STRAPS:
        if not strap.fits(slug, case_mm):
            continue
        product = by_id.get(strap.product_id) or by_handle.get(strap.handle)
        if product is None or (available_only and not product.in_stock):
            continue
        found.append(product)
    return tuple(found)


def audit(products: tuple[CatalogProduct, ...] | list[CatalogProduct]) -> tuple[str, ...]:
    """Every way this registry can have gone stale, as a list of sentences. Empty means
    the tables and the live feed still describe the same catalogue.

    The registry never self-certifies: this reads the feed and compares, which is the
    independent code path `tests/test_devices.py` and `scripts/doctor.py` both call."""
    by_id = {p.product_id: p for p in products}
    drift: list[str] = []

    for device in DEVICES:
        if device.sold_locally and not device.product_ids:
            drift.append(f"{device.slug}: marked sold here with no product id to join on")
        for product_id in device.product_ids:
            product = by_id.get(product_id)
            if product is None:
                drift.append(
                    f"{device.slug}: product {product_id} is gone from the feed — either "
                    "COROS stopped selling it here or the product was recreated"
                )
                continue
            if product.handle not in device.handles:
                drift.append(
                    f"{device.slug}: {product_id} is now /{product.handle}, the registry "
                    f"says {list(device.handles)}"
                )
            if device.slug not in CASE_OPTION:
                continue
            offered = {_case_mm_in_label(product, v) for v in product.variants}
            unknown = {mm for mm in offered if mm is not None} - {
                c.case_mm for c in device.cases
            }
            if unknown:
                drift.append(
                    f"{device.slug}: the feed offers {sorted(unknown)} mm cases the registry "
                    "does not, so their strap width is unknown"
                )
            if None in offered:
                drift.append(
                    f"{device.slug}: a variant label no longer names a case size in its "
                    f"{CASE_OPTION[device.slug]!r} option"
                )

    for strap in STRAPS:
        product = by_id.get(strap.product_id)
        if product is None:
            drift.append(f"strap {strap.product_id} (/{strap.handle}) is gone from the feed")
        elif product.handle != strap.handle:
            drift.append(
                f"strap {strap.product_id} is now /{product.handle}, the registry says "
                f"/{strap.handle}"
            )

    known = set(_BY_PRODUCT_ID) | set(_STRAP_BY_PRODUCT_ID)
    for product in products:
        if product.product_id in known:
            continue
        device = resolve(product.title)
        if device is not None:
            drift.append(
                f"/{product.handle} ({product.product_id}) is not in the registry and its "
                f"title names the {device.name}"
                + (
                    ", which the registry says is not sold in Colombia"
                    if not device.sold_locally
                    else ""
                )
            )
    return tuple(drift)
