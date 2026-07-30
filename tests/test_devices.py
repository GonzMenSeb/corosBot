"""The device registry, pinned.

Every id, handle, title, variant label and quoted sentence below is verbatim from
`https://coros.com.co/products.json?limit=250` on 30 Jul 2026. The registry is the
grounding floor: if it agrees with the feed, no strap width and no "we sell that here"
in either app came from the model.

Two facts here contradict the plan that asked for this module, and both are recorded in
`AGENTS.md` in the same commit:
  * `not_sold_locally` is ten devices, not six. PACE 2, APEX 2 and APEX Pro have straps
    in the feed and no watch SKU either, and the two gen-1 watches are named in the
    chargers' own copy.
  * The DURA is a bike computer — COROS's homepage says `alt="Ciclocomputador COROS
    DURA"` — and its `product_type` says `Relojes GPS`. It takes no strap.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from coros_core import catalog, devices
from coros_core.catalog import CatalogUnavailable
from coros_core.devices import CaseUnspecified, UnknownCase, UnknownDevice
from coros_core.models import CatalogProduct, CatalogVariant

REPO = Path(__file__).resolve().parent.parent
FACTS = 'AGENTS.md "load-bearing facts"'


def product(
    product_id: str,
    handle: str,
    title: str,
    *,
    product_type: str = "",
    option_names: tuple[str, ...] = ("Color",),
    variants: tuple[tuple[str, str, bool], ...] = (("v1", "Negro", True),),
) -> CatalogProduct:
    return CatalogProduct(
        product_id=product_id,
        handle=handle,
        title=title,
        product_url=f"https://coros.com.co/products/{handle}",
        product_type=product_type,
        option_names=option_names,
        variants=tuple(
            CatalogVariant(variant_id=v, label=label, price_minor=13_000_000, available=ok)
            for v, label, ok in variants
        ),
    )


# The four devices COROS Colombia actually sells, verbatim. PACE 4's `product_type` is
# empty and the DURA's says `Relojes GPS` for a bike computer: between them they are the
# reason this registry exists.
PACE_4 = product(
    "7752529543211",
    "coros-pace-4",
    "COROS PACE 4",
    option_names=("Serie", "Material de la correa", "Color"),
    variants=(
        ("44066526363691", "Estándar / Nylon / Blanco", True),
        ("44066526429227", "Estándar / Nylon / Negro Cristal", False),
    ),
)
APEX_4 = product(
    "7704999428139",
    "coros-apex-4",
    "COROS APEX 4",
    product_type="Relojes GPS",
    option_names=("Color", "Tamaño"),
    variants=(
        ("42372173430827", "Negro / 46mm", True),
        ("42468630954027", "Negro / 42mm", True),
        ("42372173398059", "Blanco / 46mm", False),
    ),
)
NOMAD = product(
    "7426932899883",
    "coros-nomad",
    "Coros NOMAD",
    product_type="Relojes GPS",
    variants=(("41936872439851", "Verde", True),),
)
DURA = product(
    "7251062620203",
    "coros-dura",
    "COROS DURA",
    product_type="Relojes GPS",
    variants=(("41122023276587", "Negro", False),),
)

# One strap per width, plus the two whose handles lie about what they are.
APEX_4_46_SILICONE = product(
    "8014580744235",
    "correa-de-24mm-silicona-blanco-coros-apex-4-46-mm",
    "Correa de 24mm silicona Blanco COROS APEX 4 46 mm",
    variants=(("44412968435755", "Blanco/Silicona", True),),
)
APEX_4_42_SILICONE = product(
    "7930745749547",
    "correa-de-nylon-de-24-mm-morada-para-apex-4-46-copia",
    "Correa de 22mm silicona Blanco COROS APEX 4 42 mm",
    variants=(("44131578118187", "Blanco/Silicona", True),),
)
NOMAD_SILICONE = product(
    "7926626189355",
    "correa-de-silicona-verde-24mm-coros-nomad",
    "Correa de silicona verde  24mm Coros NOMAD",
    variants=(("44121022791723", "Verde", True),),
)
VERTIX_2_SILICONE = product(
    "7422219452459",
    "correa-de-silicona-de-26-mm-vertix-2-2s",
    "Correa de silicona de 26 mm Vertix 2/2S",
    variants=(
        ("41912842977323", "Tierra", False),
        ("41912843010091", "Luna", True),
    ),
)
PACE_4_SILICONE = product(
    "7814703185963",
    "banda-silicona-pace-4-pace-3",
    "Banda silicona | Pace 4 | Pace 3",
    variants=(("44006836076587", "Negro", True), ("44006836109355", "Blanco", True)),
)
# Not a watch strap, and its title names no device: the HR monitor's chest band.
HR_BAND = product(
    "7190652649515",
    "banda-monitor-hr",
    "Banda Monitor HR",
    product_type="Bandas",
    variants=(("41421925744683", "Lima", True),),
)

FEED = (
    PACE_4,
    APEX_4,
    NOMAD,
    DURA,
    APEX_4_46_SILICONE,
    APEX_4_42_SILICONE,
    NOMAD_SILICONE,
    VERTIX_2_SILICONE,
    PACE_4_SILICONE,
    HR_BAND,
)


class TestTheJoinIsAnIdAndNeverAProductType:
    def test_a_watch_is_recognised_by_its_product_id(self) -> None:
        assert devices.device_for_product(PACE_4).slug == "pace-4"
        assert devices.device_for_product(APEX_4).slug == "apex-4"
        assert devices.device_for_product(NOMAD).slug == "nomad"
        assert devices.device_for_product(DURA).slug == "dura"

    def test_the_id_wins_when_a_merchandiser_edits_the_handle(self) -> None:
        """Shopify ids are immutable and handles are not — one live handle describes a
        different product entirely. See `AGENTS.md`."""
        renamed = PACE_4.model_copy(update={"handle": "coros-pace-4-2026"})
        assert devices.device_for_product(renamed).slug == "pace-4"

    def test_a_handle_still_resolves_a_product_that_was_recreated(self) -> None:
        recreated = APEX_4.model_copy(update={"product_id": "9999999999999"})
        assert devices.device_for_product(recreated).slug == "apex-4"

    def test_an_empty_product_type_costs_nothing(self) -> None:
        assert PACE_4.product_type == ""
        assert devices.device_for_product(PACE_4) is not None

    def test_a_relojes_gps_product_type_on_a_bike_computer_costs_nothing_either(self) -> None:
        """The DURA is a ciclocomputador and the feed files it under GPS watches."""
        assert DURA.product_type == "Relojes GPS"
        assert devices.strap_width(devices.require("dura")) is None

    def test_a_strap_is_not_a_device(self) -> None:
        assert devices.device_for_product(APEX_4_46_SILICONE) is None
        assert devices.strap_fit_for_product(APEX_4_46_SILICONE) is not None

    def test_the_registry_never_reads_a_product_type(self) -> None:
        """The one field that looks like it identifies a watch, and cannot."""
        source = (REPO / "packages" / "coros_core" / "devices.py").read_text()
        tree = ast.parse(source)
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if (isinstance(node, ast.Attribute) and node.attr == "product_type")
            or (
                isinstance(node, ast.Constant)
                and node.value == "product_type"
                and id(node) not in docstrings
            )
        ]
        assert not offenders, (
            f"devices.py reads product_type on line(s) {offenders}. It is empty on 24 of 45\n"
            f"  live products, PACE 4 included, and wrong on the DURA — {FACTS}. The join is\n"
            "  the product id with the handle as a fallback. A docstring may say so; code may not."
        )


class TestAStrapWidthIsDecidedHereAndNotByTheModel:
    def test_the_pace_4_takes_twenty_two(self) -> None:
        assert devices.strap_width(devices.require("pace-4")) == 22

    def test_the_nomad_takes_twenty_four(self) -> None:
        assert devices.strap_width(devices.require("nomad")) == 24

    def test_the_apex_4_depends_on_which_case_and_says_so(self) -> None:
        apex_4 = devices.require("apex-4")
        assert devices.strap_width(apex_4, 42) == 22
        assert devices.strap_width(apex_4, 46) == 24

    def test_an_apex_4_with_no_case_is_a_question_and_not_a_guess(self) -> None:
        with pytest.raises(CaseUnspecified):
            devices.strap_width(devices.require("apex-4"))

    def test_a_case_size_coros_does_not_ship_gets_no_width(self) -> None:
        assert devices.strap_width(devices.require("apex-4"), 47) is None

    def test_a_device_that_takes_no_strap_has_no_width(self) -> None:
        assert devices.strap_width(devices.require("dura")) is None

    def test_a_width_the_feed_never_states_is_left_unstated(self) -> None:
        """The handles `20mm-silicone-band` and `20mm-nylon-band` say 20 mm and no title
        or description does. A handle is not a source — see `AGENTS.md`."""
        assert devices.strap_width(devices.require("pace-2")) is None
        assert devices.strap_width(devices.require("apex-2")) is None

    def test_the_case_size_is_read_off_the_variants_own_label(self) -> None:
        by_label = {v.label: v for v in APEX_4.variants}
        assert devices.case_mm_for_variant(APEX_4, by_label["Negro / 46mm"]) == 46
        assert devices.case_mm_for_variant(APEX_4, by_label["Negro / 42mm"]) == 42

    def test_a_label_that_names_no_case_yields_none_rather_than_a_default(self) -> None:
        stray = CatalogVariant(variant_id="x", label="Negro", price_minor=1, available=True)
        assert devices.case_mm_for_variant(APEX_4, stray) is None

    def test_a_case_size_outside_the_registry_is_refused(self) -> None:
        future = CatalogVariant(variant_id="x", label="Negro / 50mm", price_minor=1, available=True)
        assert devices.case_mm_for_variant(APEX_4, future) is None

    def test_a_single_case_watch_reports_no_case_from_its_colour_label(self) -> None:
        assert devices.case_mm_for_variant(NOMAD, NOMAD.variants[0]) is None

    def test_the_strap_width_and_the_case_size_are_never_the_same_number(self) -> None:
        """A 46 mm watch takes a 24 mm strap. Both numbers end in "mm" and the model
        will offer the case size as a strap; the registry is what keeps them apart."""
        apex_4 = devices.require("apex-4")
        assert {c.case_mm for c in apex_4.cases} == {42, 46}
        assert {c.strap_mm for c in apex_4.cases} == {22, 24}


class TestWhatColombiaDoesNotSell:
    def test_the_four_watches_on_the_feed_are_the_four_marked_sold(self) -> None:
        assert {d.slug for d in devices.sold_locally()} == {"pace-4", "apex-4", "nomad", "dura"}

    def test_straps_without_a_watch_sku_are_the_honest_refusal_path(self) -> None:
        assert {d.slug for d in devices.not_sold_locally()} == {
            "pace-pro",
            "pace-3",
            "pace-2",
            "apex-2",
            "apex-2-pro",
            "apex-pro",
            "apex",
            "vertix-2",
            "vertix-2s",
            "vertix",
        }

    def test_a_watch_that_is_not_sold_still_has_straps_that_are(self) -> None:
        """The VERTIX 2 is the case B1 has to get right: no watch, six strap products."""
        vertix_2 = devices.require("vertix-2")
        assert not vertix_2.sold_locally
        assert devices.strap_width(vertix_2) == 26
        assert devices.straps_for(FEED, "vertix-2") == (VERTIX_2_SILICONE,)

    def test_every_device_sold_here_carries_the_id_it_is_joined_by(self) -> None:
        for device in devices.sold_locally():
            assert device.product_ids and device.handles, device.slug

    def test_no_absent_device_carries_a_product_id(self) -> None:
        for device in devices.not_sold_locally():
            assert device.product_ids == (), device.slug


class TestNamingADeviceInProse:
    @pytest.mark.parametrize(
        "text, slug",
        [
            ("PACE 4", "pace-4"),
            ("pace4", "pace-4"),
            ("coros-pace-4", "pace-4"),
            ("Tengo un Coros Apex 4 de 46mm", "apex-4"),
            ("apex4", "apex-4"),
            ("mi NOMAD", "nomad"),
            ("Coros Dura", "dura"),
            ("pace pro", "pace-pro"),
            ("PACE Pro", "pace-pro"),
            ("Pace 3", "pace-3"),
            ("apex 2", "apex-2"),
            ("APEX 2 Pro", "apex-2-pro"),
            ("apex2pro", "apex-2-pro"),
            ("apex pro", "apex-pro"),
            ("vertix 2", "vertix-2"),
            ("VERTIX 2S", "vertix-2s"),
            ("vertix2s", "vertix-2s"),
        ],
    )
    def test_a_name_resolves_to_exactly_one_row(self, text, slug) -> None:
        assert devices.resolve(text).slug == slug

    def test_the_longer_name_wins_so_a_pro_is_never_the_base_model(self) -> None:
        """"apex 2 pro" contains "apex 2" contains "apex", and the widths differ."""
        assert devices.resolve("apex 2 pro").slug == "apex-2-pro"
        assert devices.resolve("vertix 2s").slug == "vertix-2s"

    @pytest.mark.parametrize(
        "text", ["", "un reloj para trail running", "garmin forerunner 965", "apex 5", "pace 9"]
    )
    def test_something_that_is_not_a_coros_device_resolves_to_nothing(self, text) -> None:
        assert devices.resolve(text) is None

    def test_a_case_size_in_the_sentence_comes_back_with_the_device(self) -> None:
        assert devices.resolve_with_case("mi APEX 4 42mm") == (devices.require("apex-4"), 42)
        assert devices.resolve_with_case("apex 4 de 46 mm") == (devices.require("apex-4"), 46)

    def test_a_case_size_the_device_does_not_ship_is_dropped_not_carried(self) -> None:
        assert devices.resolve_with_case("apex 4 de 50mm") == (devices.require("apex-4"), None)

    def test_a_case_size_named_for_a_single_case_watch_is_dropped(self) -> None:
        assert devices.resolve_with_case("pace 4 de 42mm") == (devices.require("pace-4"), None)

    def test_an_unknown_slug_raises_rather_than_returning_an_empty_device(self) -> None:
        with pytest.raises(UnknownDevice):
            devices.require("apex-5")
        assert devices.get("apex-5") is None


class TestCompatibilityIsCuratedAndNotInferredFromAWidth:
    def test_two_twenty_four_millimetre_straps_are_not_interchangeable(self) -> None:
        """COROS sells a 24 mm strap "solo compatible con el APEX 4 46mm" and separate
        24 mm NOMAD straps. Equal width, and the vendor says they do not swap."""
        assert devices.straps_for(FEED, "apex-4", case_mm=46) == (APEX_4_46_SILICONE,)
        assert devices.straps_for(FEED, "nomad") == (NOMAD_SILICONE,)

    def test_a_twenty_two_millimetre_strap_is_never_offered_for_the_forty_six(self) -> None:
        assert APEX_4_42_SILICONE not in devices.straps_for(FEED, "apex-4", case_mm=46)
        assert APEX_4_42_SILICONE in devices.straps_for(FEED, "apex-4", case_mm=42)

    def test_a_strap_listed_for_several_watches_is_found_by_each_of_them(self) -> None:
        """One live description reads "Solo compatible con el APEX 4 42mm, PACE 4,
        PACE Pro, PACE 3, APEX 2 Pro, APEX Pro"."""
        for slug in ("pace-4", "pace-pro", "pace-3", "apex-2-pro", "apex-pro"):
            assert APEX_4_42_SILICONE in devices.straps_for(FEED, slug), slug

    def test_asking_for_an_apex_4_strap_without_a_case_is_refused_not_answered_empty(self) -> None:
        """A silent () here reads as "COROS sells no APEX 4 straps", which is false."""
        with pytest.raises(CaseUnspecified):
            devices.straps_for(FEED, "apex-4")

    def test_a_case_the_watch_does_not_ship_in_is_refused(self) -> None:
        with pytest.raises(UnknownCase):
            devices.straps_for(FEED, "apex-4", case_mm=47)

    def test_out_of_stock_straps_are_excluded_by_default_and_available_on_request(self) -> None:
        sold_out = VERTIX_2_SILICONE.model_copy(
            update={
                "variants": tuple(
                    v.model_copy(update={"available": False}) for v in VERTIX_2_SILICONE.variants
                )
            }
        )
        feed = tuple(sold_out if p is VERTIX_2_SILICONE else p for p in FEED)
        assert devices.straps_for(feed, "vertix-2") == ()
        assert devices.straps_for(feed, "vertix-2", available_only=False) == (sold_out,)

    def test_a_strap_in_the_registry_but_not_in_the_feed_is_simply_absent(self) -> None:
        assert devices.straps_for((PACE_4,), "pace-4") == ()

    def test_the_hr_chest_band_is_not_a_watch_strap(self) -> None:
        assert devices.strap_fit_for_product(HR_BAND) is None
        assert HR_BAND not in devices.straps_for(FEED, "pace-4")


class TestTheRegistryAgreesWithItself:
    def test_a_slug_names_one_device(self) -> None:
        slugs = [d.slug for d in devices.DEVICES]
        assert len(slugs) == len(set(slugs))

    def test_an_alias_names_one_device_after_normalising(self) -> None:
        """`pace4`, `PACE-4` and `Pace 4` are one key, so two rows cannot both claim it."""
        seen: dict[str, str] = {}
        for slug, aliases in devices.ALIASES.items():
            for alias in aliases:
                key = devices.normalize(alias)
                assert key not in seen, f"{key!r} is claimed by {seen.get(key)} and {slug}"
                seen[key] = slug

    def test_every_device_answers_to_its_own_name(self) -> None:
        for device in devices.DEVICES:
            assert devices.resolve(device.name) is device, device.name
            assert devices.resolve(device.slug) is device, device.slug

    def test_every_strap_fits_a_device_that_exists(self) -> None:
        for strap in devices.STRAPS:
            assert strap.devices, strap.handle
            for fit in strap.devices:
                device = devices.get(fit.slug)
                assert device is not None, f"{strap.handle} fits unknown {fit.slug}"
                if fit.case_mm is not None:
                    assert device.strap_mm_for(fit.case_mm) is not None, (
                        f"{strap.handle} names a {fit.case_mm} mm {fit.slug} the registry "
                        "does not ship"
                    )

    def test_a_stated_strap_width_agrees_with_every_device_it_fits(self) -> None:
        """A curated table is only worth having if a typo in it fails the build."""
        for strap in devices.STRAPS:
            if strap.strap_mm is None:
                continue
            for fit in strap.devices:
                width = devices.get(fit.slug).strap_mm_for(fit.case_mm)
                assert width in (None, strap.strap_mm), (
                    f"{strap.handle} states {strap.strap_mm} mm but {fit.slug} takes {width} mm"
                )

    def test_a_product_id_is_claimed_once(self) -> None:
        ids = [pid for d in devices.DEVICES for pid in d.product_ids]
        ids += [s.product_id for s in devices.STRAPS]
        assert len(ids) == len(set(ids))

    def test_every_row_carries_the_evidence_it_was_curated_from(self) -> None:
        for device in devices.DEVICES:
            assert device.note, device.slug
        for strap in devices.STRAPS:
            assert strap.evidence, strap.handle


def new_drift(feed: tuple[CatalogProduct, ...]) -> set[str]:
    """`audit()` is meant for the whole catalogue, and `FEED` here is ten products of it,
    so it always reports the strap rows this file leaves out. What each case below cares
    about is what a mutation ADDS. The live test is the one that asserts silence."""
    return set(devices.audit(feed)) - set(devices.audit(FEED))


class TestDriftAgainstTheFeedIsReportedAndNotAbsorbed:
    def test_a_trimmed_feed_only_complains_about_the_straps_it_omits(self) -> None:
        omitted = {s.product_id for s in devices.STRAPS} - {p.product_id for p in FEED}
        for line in devices.audit(FEED):
            assert any(product_id in line for product_id in omitted), line

    def test_a_watch_that_vanished_from_the_feed_is_reported(self) -> None:
        assert new_drift(tuple(p for p in FEED if p is not NOMAD)) == {
            "nomad: product 7426932899883 is gone from the feed — either COROS stopped "
            "selling it here or the product was recreated"
        }

    def test_a_renamed_handle_is_reported_rather_than_silently_followed(self) -> None:
        renamed = PACE_4.model_copy(update={"handle": "coros-pace-4-nuevo"})
        drift = new_drift(tuple(renamed if p is PACE_4 else p for p in FEED))
        assert any("coros-pace-4-nuevo" in line for line in drift), drift

    def test_a_new_case_size_on_a_known_watch_is_reported(self) -> None:
        """A 50 mm APEX 4 would take a strap width nothing here knows."""
        grown = APEX_4.model_copy(
            update={
                "variants": APEX_4.variants
                + (
                    CatalogVariant(
                        variant_id="1", label="Negro / 50mm", price_minor=1, available=True
                    ),
                )
            }
        )
        drift = new_drift(tuple(grown if p is APEX_4 else p for p in FEED))
        assert any("50" in line for line in drift), drift

    def test_a_case_option_that_stops_naming_a_size_is_reported(self) -> None:
        relabelled = APEX_4.model_copy(
            update={
                "variants": (
                    CatalogVariant(
                        variant_id="1", label="Negro / Grande", price_minor=1, available=True
                    ),
                )
            }
        )
        drift = new_drift(tuple(relabelled if p is APEX_4 else p for p in FEED))
        assert any("no longer names a case size" in line for line in drift), drift

    def test_a_watch_colombia_did_not_used_to_sell_is_reported(self) -> None:
        """The day COROS Colombia lists the PACE Pro, the refusal path is a lie and this
        is what says so."""
        drift = new_drift(FEED + (product("8100000000001", "coros-pace-pro", "COROS PACE Pro"),))
        assert any("not sold in Colombia" in line and "PACE Pro" in line for line in drift), drift

    def test_an_unrelated_new_accessory_is_not_reported(self) -> None:
        hydrop = product("8100000000002", "coros-hydrop", "Coros Hydrop")
        assert new_drift(FEED + (hydrop,)) == set()


# ── live: the registry against the storefront it was curated from ─────────────
#
# ONE request. A burst is what trips the limiter, and a lockout costs everyone on this
# machine the next ~100 s. See `AGENTS.md`.


@pytest.mark.live
def test_the_registry_still_joins_to_the_live_feed() -> None:
    async def probe():
        catalog.reset_pacing()
        try:
            payload = await catalog._get_json(catalog.PRODUCTS_URL, "products")
        except CatalogUnavailable as exc:
            if not exc.rate_limited:
                raise
            return None
        return catalog.normalize(payload, include_hidden=True)

    products = asyncio.run(probe())
    if products is None:
        pytest.skip(
            "storefront rate limited — not a contract violation, and do NOT poll: 30 requests "
            "spaced 10 s apart were all refused. Leave it quiet, then `make verify` again."
        )
    assert devices.audit(products) == (), (
        f"the registry and the live feed have diverged. {FACTS} and the maintenance contract\n"
        "  pin every row here — update AGENTS.md, this test and devices.py together."
    )
