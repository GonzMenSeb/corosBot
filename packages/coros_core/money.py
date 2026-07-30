"""The one place a COROS price changes scale or becomes text.

COROS publishes every price twice, in two different units:

  | source                             | units | COROS PACE 4 Nylon Blanco       |
  |------------------------------------|-------|---------------------------------|
  | storefront `variants[].price`      | MAJOR | `"1099000.00"` (decimal string) |
  | UCP variant `price`                | MINOR | `{"amount": 109900000, ...}`    |

Same variant, a factor of 100 apart. Verified live 29 Jul 2026 against
`coros.com.co/products.json` and `POST /api/ucp/mcp` `search_catalog`; the page
itself renders `$1.099.000`.

So: minor units everywhere internally, integers only, converted at the boundary and
nowhere else. Print a UCP `amount` as if it were already pesos and the watch is quoted
at $109.900.000 — a number plausible enough in a currency this size that nobody catches
it by eye, on a storefront where a real watch does cost seven figures.

`tests/test_money.py` fails the build if a `* 100` or `/ 100` on a price appears in any
other module.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Any

MINOR_PER_MAJOR = 100

# Only what has been seen on the wire. An unlisted code raises rather than borrowing
# the peso's format: `$1,099,000` reads as roughly one peso to a Colombian, since the
# comma is the decimal mark here.
_SYMBOLS = {"COP": "$"}

_WHOLE = Decimal(1)


class MoneyError(ValueError):
    """A price could not be read. Raised, never swallowed into a zero — a missing
    price has to reach the caller as a failed tool outcome, not as something free."""


def major_string_to_minor(price: str | int | float) -> int:
    """`"1099000.00"` -> `109900000`. The storefront feed boundary.

    Refuses precision the conversion cannot hold instead of rounding it away: a feed
    that starts sending three decimals has changed shape, and that is worth a stack
    trace rather than a silently truncated price.
    """
    if isinstance(price, bool) or not isinstance(price, (str, int, float)):
        raise MoneyError(f"a price has to be a decimal string or a number, got {price!r}")
    try:
        major = Decimal(str(price).strip())
    except DecimalException:
        raise MoneyError(f"{price!r} is not a decimal number") from None
    if not major.is_finite():
        raise MoneyError(f"{price!r} is not a finite price")
    if major < 0:
        raise MoneyError(f"{price!r} is negative; a discount is a separate field, not a price")
    minor = major * MINOR_PER_MAJOR
    if minor != minor.to_integral_value():
        raise MoneyError(
            f"{price!r} carries more precision than {MINOR_PER_MAJOR} minor units per major "
            "can hold. Re-check the feed before widening this — every COROS price observed "
            "so far ends in .00."
        )
    return int(minor)


def ucp_amount_to_minor(price: Mapping[str, Any], expect_currency: str = "COP") -> int:
    """`{"amount": 109900000, "currency": "COP"}` -> `109900000`. A passthrough.

    It exists so the UCP boundary is as explicit and as greppable as the feed one. The
    value is *already* minor; the mistake this prevents is the reflex to multiply it.
    """
    amount = price.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise MoneyError(
            f"UCP price {dict(price)!r} has no whole-integer amount. Minor units arrive as "
            "an int; anything else means the payload changed and the scale is unknown."
        )
    if amount < 0:
        raise MoneyError(f"UCP price {dict(price)!r} is negative")
    currency = str(price.get("currency") or "").strip().upper()
    if currency != expect_currency.strip().upper():
        raise MoneyError(
            f"UCP price {dict(price)!r} is not in {expect_currency}. Converting currencies "
            "is not this system's job — surface it and stop."
        )
    return amount


def minor_to_display(minor: int, currency: str = "COP") -> str:
    """`109900000` -> `"$1.099.000"`. The only path from a number to human text.

    Dot thousands and no decimals, matching how coros.com.co prints its own prices.
    Rounding happens here and only here, so it can never feed back into arithmetic.
    """
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise MoneyError(
            f"minor units are whole numbers and {minor!r} is not one. A float here means "
            "the arithmetic upstream left integer units — fix it there, not by rounding."
        )
    symbol = _SYMBOLS.get(currency.strip().upper())
    if symbol is None:
        raise MoneyError(
            f"no verified {currency} format. Add it to _SYMBOLS together with a test that "
            "pins how a native speaker reads it — separators are not interchangeable."
        )
    pesos = (Decimal(abs(minor)) / MINOR_PER_MAJOR).quantize(_WHOLE, rounding=ROUND_HALF_UP)
    sign = "-" if minor < 0 else ""
    return f"{sign}{symbol}{f'{pesos:,}'.replace(',', '.')}"
