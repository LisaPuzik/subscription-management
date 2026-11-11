from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.currency import SUPPORTED_CURRENCIES, amount_error, money


def test_minor_units():
    assert SUPPORTED_CURRENCIES["EUR"] == 2
    assert SUPPORTED_CURRENCIES["JPY"] == 0
    assert SUPPORTED_CURRENCIES["KWD"] == 3
    assert "XXX" not in SUPPORTED_CURRENCIES


def test_amount_precision():
    assert amount_error(Decimal("15.99"), "EUR") is None
    assert amount_error(Decimal("15.990"), "EUR") is None
    assert "decimal places" in amount_error(Decimal("15.999"), "EUR")
    assert amount_error(Decimal("100"), "JPY") is None
    assert amount_error(Decimal("100.5"), "JPY") is not None
    assert amount_error(Decimal("0"), "EUR") is not None
    assert amount_error(Decimal("1e12"), "EUR") is not None


def test_money_format():
    assert money(Decimal("5"), "EUR") == "5.00"
    assert money(Decimal("5.00000000"), "JPY") == "5"
    assert money(Decimal("1.5"), "KWD") == "1.500"


def test_settings_validation():
    base = {"database_url": "postgresql+asyncpg://x", "jwt_secret": "s" * 32}
    for bad in ({"default_currency": "ABC"}, {"jwt_secret": "short"}, {"environment": "production"}):
        with pytest.raises(ValidationError):
            Settings(**{**base, **bad})
    assert Settings(**base, environment="production", smtp_host="smtp.example.com").smtp_host
