"""The shared contract. Both apps and the deterministic core import from here; nothing
here imports either app.

Four choices are load-bearing. Do not "simplify" them:
  * `Url` is a `str` validated as a URL, never a pydantic `HttpUrl` — see below.
  * frozen models hold tuples, never lists. `ConfigDict(frozen=True)` overrides
    `__setattr__` and nothing else, so a frozen model with a `list` field is not
    actually frozen: `spec.requirements.append(...)` still works. Verified on
    pydantic 2.13.4, 29 Jul 2026.
  * the policy models also set `extra="forbid"`. A model that invents
    `max_total_minor_override` must be rejected, not quietly ignored.
  * every optional field carries an explicit default. In pydantic v2 `X | None`
    without a default is a REQUIRED field that happens to accept None.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    model_validator,
)

_http_url = TypeAdapter(HttpUrl)


def _must_be_url(v: str) -> str:
    _http_url.validate_python(v)
    return v


# A `str` validated as an HttpUrl but STORED as a str.
#
# Do not "restore" this to a bare `HttpUrl`. Reflex 0.9.7's wire encoder serialises a
# pydantic HttpUrl to `null` — silently, no error — so every product link and photo
# would reach the browser empty. Verified against reflex.utils.format.json_dumps on
# 29 Jul 2026 (reflex 0.9.7, pydantic 2.13.4) and pinned by tests/test_models.py.
Url = Annotated[str, AfterValidator(_must_be_url)]

Provenance = Literal["catalog", "device_registry", "strava", "user", "assumed"]

Intent = Literal[
    "advice", "clarify", "greeting", "off_topic", "out_of_scope", "safety_critical", "injection"
]

AdviceKind = Literal[
    "recommend", "buy_nothing", "not_sold_locally", "insufficient_evidence", "needs_human"
]

# The allowlist. Widening it is the only way a new kind of fact can reach the catalog
# path, which is what makes Huella's privacy boundary reviewable in one place. The
# training keys are bands rather than figures: an exact weekly volume identifies an
# athlete, a band does not, and a derived number must never read as a measured one.
RequirementKey = Literal[
    "discipline",
    "device",
    "case_mm",
    "strap_mm",
    "budget_minor",
    "battery_hours",
    "water_resistance",
    "terrain",
    "sport_mix",
    "weekly_hours_band",
    "longest_session_band",
    "elevation_band",
    "consistency_band",
]


class Requirement(BaseModel):
    """The only shape allowed to cross from a person's health or training data into the
    catalog path. `extra="forbid"` plus a primitive-only `value` is what makes that
    structural: there is no field a raw Strava payload could ride in on, and no float
    to carry a fingerprint like a 4.7523 min/km average."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: RequirementKey
    value: str | int | bool
    source: Provenance = "assumed"
    derived: bool = False
    sample_size: int = Field(default=0, ge=0)
    window_days: int = Field(default=0, ge=0)
    rationale: str = ""

    @model_validator(mode="after")
    def a_derived_value_declares_its_evidence(self):
        if self.derived and self.sample_size == 0:
            raise ValueError(
                f"{self.key} is derived but claims no sample. Advice leaning on a thin or "
                "stale window has to be flagged as such, and the count is how the UI knows."
            )
        return self


class Budget(BaseModel):
    """Narrowing-only by construction. A model that decides the person can afford more
    has no API to say so — KB `docs/lifeseek/SPEC.md` §1.5: a textual prohibition was
    violated 0.46% of the time despite the warning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_total_minor: int | None = Field(default=None, ge=0)
    currency: str = "COP"

    @property
    def is_unlimited(self) -> bool:
        return self.max_total_minor is None

    def allows(self, total_minor: int) -> bool:
        return self.max_total_minor is None or total_minor <= self.max_total_minor

    def narrowed_to(self, max_total_minor: int) -> Budget:
        if self.max_total_minor is not None and max_total_minor > self.max_total_minor:
            raise ValueError(
                f"refusing to widen the budget from {self.max_total_minor} to "
                f"{max_total_minor} {self.currency} minor units. A budget only ever narrows."
            )
        return Budget(max_total_minor=max_total_minor, currency=self.currency)


class AdviceSpec(BaseModel):
    """What a session is allowed to answer, pinned when it starts. `digest` is the
    identity every trace event and evidence bundle records, so a run can be replayed
    against the spec it actually ran under."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    discipline: str = ""
    budget: Budget = Field(default_factory=Budget)
    requirements: tuple[Requirement, ...] = ()
    excluded_devices: tuple[str, ...] = ()
    locale: str = "es-CO"

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def with_requirements(self, *added: Requirement) -> AdviceSpec:
        """The only supported way a spec grows. Requirements constrain, so this narrows;
        there is deliberately no counterpart for the budget or the exclusions."""
        return AdviceSpec(**{**self.model_dump(), "requirements": self.requirements + added})


class IntentVerdict(BaseModel):
    intent: Intent
    discipline: str = ""
    reason: str = ""


class CatalogVariant(BaseModel):
    """One size or colour of a product, normalised from the storefront feed."""

    variant_id: str
    size_label: str = ""
    price_minor: int
    available: bool


class CatalogProduct(BaseModel):
    """A feed product after normalisation. The only product shape the agent loop and the
    UI ever see — raw feed dicts stop at catalog.py.

    No gid-prefix validator, unlike DecaBot: COROS's variant id format has not been
    verified against the live UCP endpoint yet. PR 2 pins it in tests/test_contracts.py
    and enforces it where it parses, not here."""

    product_id: str
    handle: str
    title: str
    product_url: Url
    image_url: Url | None = None
    # Empty on roughly half the COROS feed, PACE 4 included. Never key on it.
    product_type: str = ""
    vendor: str = ""
    tags: tuple[str, ...] = ()
    variants: tuple[CatalogVariant, ...] = ()

    @property
    def in_stock(self) -> bool:
        return any(v.available for v in self.variants)

    @property
    def min_price_minor(self) -> int | None:
        prices = [v.price_minor for v in self.variants if v.available]
        return min(prices) if prices else None


class DeviceCase(BaseModel):
    case_mm: int
    strap_mm: int


class Device(BaseModel):
    """A curated fact, joined to the live feed by handle. `sold_locally=False` means the
    Colombian storefront lists straps for this watch but no watch SKU — the distinction
    the honest-refusal path is built on, and one no feed field expresses."""

    slug: str
    name: str
    sold_locally: bool
    cases: tuple[DeviceCase, ...] = ()
    handles: tuple[str, ...] = ()
    note: str = ""

    def strap_mm_for(self, case_mm: int) -> int | None:
        return next((c.strap_mm for c in self.cases if c.case_mm == case_mm), None)


class AdviceItem(BaseModel):
    """One recommended product. Built from the catalog cache, never from model prose."""

    product_id: str
    title: str
    product_url: Url
    image_url: Url | None = None
    variant_id: str = ""
    price_minor: int
    rationale: str = ""
    satisfies: tuple[RequirementKey, ...] = ()


class Advice(BaseModel):
    """"Buy nothing" and "not sold in Colombia" are values of `kind`, not sentences
    buried in `explanation`. The validator is what keeps them that way."""

    kind: AdviceKind
    items: tuple[AdviceItem, ...] = ()
    explanation: str = ""
    unavailable_devices: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    spec_digest: str = ""

    @property
    def total_minor(self) -> int:
        return sum(i.price_minor for i in self.items)

    @model_validator(mode="after")
    def the_kind_and_the_payload_agree(self):
        if self.kind == "recommend" and not self.items:
            raise ValueError("a recommendation with no products is a buy_nothing — say so in kind")
        if self.kind != "recommend" and self.items:
            raise ValueError(f"kind={self.kind!r} cannot carry products")
        if self.kind == "not_sold_locally" and not self.unavailable_devices:
            raise ValueError("not_sold_locally has to name what is not sold, not just say it")
        return self
