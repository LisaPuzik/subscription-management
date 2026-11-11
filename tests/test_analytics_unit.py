import uuid
from datetime import date
from decimal import Decimal

from app.domain.analytics import PlannedSubscription, Row, by_category, forecast, month_keys, monthly, summary


def test_summary_net_expenses_example():
    rows = [Row("EUR", "charge", Decimal("45"), 3), Row("EUR", "refund", Decimal("5"), 1)]
    [eur] = summary(rows)
    assert eur["net_expenses"] == "40.00"
    assert eur["payments_count"] == 4
    assert (eur["charges_count"], eur["refunds_count"]) == (3, 1)


def test_summary_keeps_currencies_apart():
    rows = [Row("USD", "charge", Decimal("50"), 1), Row("EUR", "charge", Decimal("100"), 2)]
    assert [r["currency"] for r in summary(rows)] == ["EUR", "USD"]
    assert summary([]) == []


def test_month_keys_partial_bounds():
    assert month_keys(date(2026, 1, 15), date(2026, 3, 2)) == ["2026-01", "2026-02", "2026-03"]
    assert month_keys(date(2026, 1, 1), date(2026, 2, 1)) == ["2026-01"]
    assert month_keys(date(2026, 12, 1), date(2027, 1, 1)) == ["2026-12"]


def test_monthly_fills_gaps_for_each_currency():
    months = ["2026-01", "2026-02", "2026-03"]
    rows = [
        Row("EUR", "charge", Decimal("100"), 4, month="2026-01"),
        Row("EUR", "refund", Decimal("10"), 1, month="2026-01"),
        Row("USD", "charge", Decimal("50"), 1, month="2026-02"),
    ]
    result = monthly(rows, months, set())
    assert [i["currency"] for i in result] == ["EUR", "USD"]
    for item in result:
        assert [m["month"] for m in item["months"]] == months
    assert result[0]["months"][0]["net_expenses"] == "90.00"
    assert result[0]["months"][2] == {
        "month": "2026-03",
        "gross_charges": "0.00",
        "refunds": "0.00",
        "net_expenses": "0.00",
        "charges_count": 0,
        "refunds_count": 0,
    }


def test_monthly_includes_filtered_currency_without_operations():
    result = monthly([], ["2026-01"], {"USD"})
    assert result[0]["currency"] == "USD"
    assert result[0]["months"][0]["gross_charges"] == "0.00"


def test_by_category_snapshot_and_uncategorized():
    cid = uuid.uuid4()
    rows = [
        Row("EUR", "charge", Decimal("30"), 1, category_id=cid, category_name="Old name"),
        Row("EUR", "charge", Decimal("20"), 1, category_id=cid, category_name="New name"),
        Row("EUR", "charge", Decimal("10"), 1),
    ]
    [eur] = by_category(rows)
    assert [c["category_name"] for c in eur["categories"]] == ["New name", "Old name", None]
    assert eur["categories"][-1]["display_name"] == "Без категории"


def planned(name, amount, unit, count, anchor, next_date=None):
    return PlannedSubscription(
        uuid.uuid4(), name, Decimal(amount), "EUR", unit, count, anchor, next_date or anchor, None, None
    )


def test_forecast_spec_example():
    subs = [
        planned("Streaming", "10", "month", 1, date(2026, 1, 5)),
        planned("Cloud", "5", "month", 1, date(2026, 1, 20)),
        planned("Software", "60", "year", 1, date(2025, 12, 10)),
        planned("Other yearly", "99", "year", 1, date(2026, 6, 1)),
    ]
    totals, items = forecast(subs, date(2026, 12, 1), date(2026, 11, 11), set())
    assert totals[0]["forecast_amount"] == "75.00"
    assert len(items) == 3


def test_forecast_weekly_counts_each_date():
    sub = planned("Gym", "7", "week", 1, date(2026, 1, 1))
    totals, items = forecast([sub], date(2026, 12, 1), date(2026, 11, 11), set())
    assert len(items) == 5
    assert totals[0]["forecast_amount"] == "35.00"


def test_forecast_current_month_skips_past_and_paid():
    sub = planned("Daily", "1", "day", 1, date(2026, 11, 1), next_date=date(2026, 11, 11))
    paid = {(sub.id, date(2026, 11, 20))}
    _, items = forecast([sub], date(2026, 11, 1), date(2026, 11, 20), paid)
    assert items[0]["expected_payment_date"] == date(2026, 11, 21)
    assert len(items) == 10
