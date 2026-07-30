"""The 100× boundary, pinned.

Every number here was read off the live storefront on 29 Jul 2026, for one variant
(`gid://shopify/ProductVariant/44066526363691`, COROS PACE 4 Nylon Blanco):

  * `products.json` variants[].price  -> "1099000.00"                (major, string)
  * UCP `search_catalog` variant price -> {"amount": 109900000, ...}  (minor, int)
  * the storefront's own page renders  -> "$1.099.000"

Same variant, three representations. Get the scale wrong once and Brújula quotes a
watch at a hundred times its price with total confidence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from coros_core.money import (
    MINOR_PER_MAJOR,
    MoneyError,
    major_string_to_minor,
    minor_to_display,
    ucp_amount_to_minor,
)

PACE_4_MAJOR = "1099000.00"
PACE_4_MINOR = 109_900_000
PACE_4_DISPLAY = "$1.099.000"

# Real feed prices, both ends of the range and a strap in between.
FEED_PRICES = [
    ("40000.00", 4_000_000, "$40.000"),
    ("130000.00", 13_000_000, "$130.000"),
    ("1099000.00", 109_900_000, "$1.099.000"),
    ("1250000.00", 125_000_000, "$1.250.000"),
    ("2099000.00", 209_900_000, "$2.099.000"),
]

REPO = Path(__file__).resolve().parent.parent
MONEY_WORDS = re.compile(r"price|minor|major|amount|cop|peso|budget|total", re.IGNORECASE)
RESCALE = re.compile(r"[*/]\s*100\b")


def source_files() -> list[Path]:
    roots = [REPO / "packages", REPO / "apps", REPO / "scripts"]
    return sorted(p for root in roots if root.is_dir() for p in root.rglob("*.py"))


class TestTheTwoSourcesDescribeTheSamePrice:
    def test_the_feed_string_and_the_ucp_amount_land_on_one_number(self) -> None:
        assert major_string_to_minor(PACE_4_MAJOR) == ucp_amount_to_minor(
            {"amount": PACE_4_MINOR, "currency": "COP"}
        )

    def test_the_ucp_amount_is_already_minor_and_is_never_scaled_again(self) -> None:
        assert ucp_amount_to_minor({"amount": PACE_4_MINOR, "currency": "COP"}) == PACE_4_MINOR

    def test_the_scale_between_them_is_the_one_constant(self) -> None:
        assert MINOR_PER_MAJOR * 1_099_000 == PACE_4_MINOR

    def test_a_ucp_amount_shown_raw_would_be_the_hundred_fold_bug(self) -> None:
        """What the bug looks like, so a regression reads as a diff and not as a price."""
        assert minor_to_display(PACE_4_MINOR) == PACE_4_DISPLAY
        assert minor_to_display(PACE_4_MINOR) != "$109.900.000"


class TestTheFeedBoundary:
    @pytest.mark.parametrize("major, minor, _display", FEED_PRICES)
    def test_a_decimal_string_becomes_an_integer_of_minor_units(self, major, minor, _display) -> None:
        assert major_string_to_minor(major) == minor

    def test_the_result_is_an_int_and_not_a_float(self) -> None:
        assert type(major_string_to_minor("1099000.00")) is int

    def test_ints_and_floats_convert_the_same_way_as_their_string(self) -> None:
        assert major_string_to_minor(1_099_000) == PACE_4_MINOR
        assert major_string_to_minor(1_099_000.0) == PACE_4_MINOR
        assert major_string_to_minor(0.07) == 7

    @pytest.mark.parametrize("junk", ["", "   ", "gratis", "1.099.000", "$1099000", None, True, [1]])
    def test_anything_that_is_not_a_number_is_refused_not_guessed(self, junk) -> None:
        with pytest.raises(MoneyError):
            major_string_to_minor(junk)

    @pytest.mark.parametrize("junk", ["nan", "inf", "-inf"])
    def test_a_non_finite_price_is_refused(self, junk) -> None:
        with pytest.raises(MoneyError):
            major_string_to_minor(junk)

    def test_a_negative_price_is_refused(self) -> None:
        with pytest.raises(MoneyError):
            major_string_to_minor("-1099000.00")

    def test_precision_the_currency_cannot_hold_is_refused_not_rounded_away(self) -> None:
        """Silently rounding "1099000.005" hides a feed that changed shape."""
        with pytest.raises(MoneyError):
            major_string_to_minor("1099000.005")


class TestTheUcpBoundary:
    def test_a_zero_amount_is_a_price_and_not_a_missing_one(self) -> None:
        assert ucp_amount_to_minor({"amount": 0, "currency": "COP"}) == 0

    @pytest.mark.parametrize(
        "price",
        [
            {},
            {"currency": "COP"},
            {"amount": None, "currency": "COP"},
            {"amount": "109900000", "currency": "COP"},
            {"amount": 1099000.0, "currency": "COP"},
            {"amount": True, "currency": "COP"},
            {"amount": -1, "currency": "COP"},
        ],
    )
    def test_an_amount_that_is_not_a_whole_non_negative_integer_is_refused(self, price) -> None:
        with pytest.raises(MoneyError):
            ucp_amount_to_minor(price)

    def test_a_currency_that_is_not_the_expected_one_is_refused(self) -> None:
        with pytest.raises(MoneyError):
            ucp_amount_to_minor({"amount": PACE_4_MINOR, "currency": "USD"})

    def test_the_currency_code_is_compared_case_insensitively(self) -> None:
        assert ucp_amount_to_minor({"amount": PACE_4_MINOR, "currency": "cop"}) == PACE_4_MINOR

    def test_a_missing_currency_is_refused_rather_than_assumed_to_be_pesos(self) -> None:
        with pytest.raises(MoneyError):
            ucp_amount_to_minor({"amount": PACE_4_MINOR})


class TestWhatAColombianActuallyReads:
    @pytest.mark.parametrize("_major, minor, display", FEED_PRICES)
    def test_minor_units_render_the_way_the_storefront_renders_them(self, _major, minor, display) -> None:
        assert minor_to_display(minor) == display

    def test_thousands_are_separated_with_dots_and_never_commas(self) -> None:
        """In es-CO a comma is the decimal mark: "$1,099,000" reads as one peso."""
        assert "," not in minor_to_display(PACE_4_MINOR)

    def test_pesos_are_shown_whole_because_no_centavo_circulates(self) -> None:
        assert minor_to_display(100) == "$1"
        assert minor_to_display(0) == "$0"

    def test_a_stray_centavo_rounds_for_display_only(self) -> None:
        assert minor_to_display(150) == "$2"
        assert minor_to_display(149) == "$1"

    def test_a_negative_delta_keeps_the_sign_outside_the_symbol(self) -> None:
        assert minor_to_display(-PACE_4_MINOR) == "-$1.099.000"

    @pytest.mark.parametrize("junk", [1_099_000.0, "109900000", None, True])
    def test_anything_but_whole_minor_units_is_refused(self, junk) -> None:
        """A float reaching here means someone divided instead of using `//` upstream."""
        with pytest.raises(MoneyError):
            minor_to_display(junk)

    def test_an_unverified_currency_is_refused_rather_than_formatted_as_pesos(self) -> None:
        with pytest.raises(MoneyError):
            minor_to_display(5000, "USD")

    def test_the_currency_code_is_accepted_in_any_case(self) -> None:
        assert minor_to_display(PACE_4_MINOR, "cop") == PACE_4_DISPLAY

    def test_a_round_trip_from_the_feed_reaches_the_storefront_string(self) -> None:
        for major, _minor, display in FEED_PRICES:
            assert minor_to_display(major_string_to_minor(major)) == display


class TestThereIsExactlyOneConversionPoint:
    def test_no_other_module_rescales_a_price_by_a_hundred(self) -> None:
        offenders = []
        for path in source_files():
            if path.name == "money.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if RESCALE.search(line) and MONEY_WORDS.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
        assert not offenders, (
            "A price is being rescaled outside coros_core.money:\n  "
            + "\n  ".join(offenders)
            + "\nRoute it through major_string_to_minor / ucp_amount_to_minor /\n"
            "minor_to_display instead. A second conversion point is how a watch ends up\n"
            "quoted at a hundred times its price."
        )
