from decimal import Decimal

import pycountry
from babel.numbers import get_currency_precision

NON_MONETARY = {
    "XAG",
    "XAU",
    "XBA",
    "XBB",
    "XBC",
    "XBD",
    "XDR",
    "XPD",
    "XPT",
    "XSU",
    "XTS",
    "XUA",
    "XXX",
}

SUPPORTED_CURRENCIES: dict[str, int] = {
    c.alpha_3: get_currency_precision(c.alpha_3) for c in pycountry.currencies if c.alpha_3 not in NON_MONETARY
}

MAX_AMOUNT = Decimal("1e12")


def minor_unit(currency: str) -> int:
    return SUPPORTED_CURRENCIES[currency]


def amount_error(amount: Decimal, currency: str) -> str | None:
    if amount <= 0:
        return "Amount must be positive"
    if amount >= MAX_AMOUNT:
        return "Amount is too large"
    places = minor_unit(currency)
    if amount != amount.quantize(Decimal(1).scaleb(-places)):
        return f"Amount has too many decimal places, allowed: {places}"
    return None


def money(value: Decimal, currency: str) -> str:
    return str(value.quantize(Decimal(1).scaleb(-minor_unit(currency))))
