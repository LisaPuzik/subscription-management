from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.schedule import add_months, occurrences_between
from tests.test_payments import one_time
from tests.test_subscriptions import create_subscription

TZ = "Europe/Vilnius"


def local_today() -> date:
    return datetime.now(ZoneInfo(TZ)).date()


async def pay(account, amount, occurred_at, currency="EUR", category_id=None):
    payload = {**one_time(amount, occurred_at), "currency": currency}
    if category_id:
        payload["category_id"] = category_id
    r = await account.post("/payments", payload, idem=True)
    assert r.status_code == 201, r.text
    return r.json()


async def refund(account, payment, amount, occurred_at):
    r = await account.post(
        f"/payments/{payment['id']}/refunds", {"amount": amount, "occurred_at": occurred_at}, idem=True
    )
    assert r.status_code == 201, r.text


async def test_summary_spec_example(make_user):
    user = await make_user(timezone=TZ)
    await pay(user, "10.00", "2026-02-02T10:00:00Z")
    b = await pay(user, "20.00", "2026-02-03T10:00:00Z")
    await pay(user, "15.00", "2026-02-04T10:00:00Z")
    await refund(user, b, "5.00", "2026-02-05T10:00:00Z")
    await pay(user, "50.00", "2026-02-06T10:00:00Z", currency="USD")
    reversed_payment = await pay(user, "99.00", "2026-02-07T10:00:00Z")
    await user.post(f"/payments/{reversed_payment['id']}/reverse", {"reason": "Entered by mistake"})

    r = await user.get("/analytics/summary", params={"date_from": "2026-02-01", "date_to": "2026-03-01"})
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == {"date_from": "2026-02-01", "date_to": "2026-03-01", "timezone": TZ}
    eur, usd = body["totals_by_currency"]
    assert eur == {
        "currency": "EUR",
        "gross_charges": "45.00",
        "refunds": "5.00",
        "net_expenses": "40.00",
        "charges_count": 3,
        "refunds_count": 1,
        "payments_count": 4,
    }
    assert usd["currency"] == "USD" and usd["net_expenses"] == "50.00"

    r = await user.get(
        "/analytics/summary", params={"date_from": "2026-02-01", "date_to": "2026-03-01", "currency": "USD"}
    )
    assert [t["currency"] for t in r.json()["totals_by_currency"]] == ["USD"]


async def test_period_validation(user):
    for params in (
        {"date_from": "2026-02-01"},
        {"date_from": "2026-03-01", "date_to": "2026-02-01"},
        {"date_from": "2026-02-01", "date_to": "2026-02-01"},
        {"date_from": "2026-02-01", "date_to": "2026-03-01", "currency": "eur"},
    ):
        for path in ("/analytics/summary", "/analytics/monthly", "/analytics/categories"):
            r = await user.get(path, params=params)
            assert r.status_code == 422, (path, params)


async def test_monthly_local_boundaries_and_gaps(make_user):
    user = await make_user(timezone=TZ)
    p = await pay(user, "100.00", "2026-01-31T22:30:00Z")
    await pay(user, "30.00", "2026-01-10T10:00:00Z")
    await pay(user, "7.00", "2026-01-12T10:00:00Z", currency="USD")
    await refund(user, p, "10.00", "2026-03-05T10:00:00Z")
    await pay(user, "1.00", "2026-01-01T10:00:00Z")

    r = await user.get("/analytics/monthly", params={"date_from": "2026-01-05", "date_to": "2026-03-10"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["currency"] for i in items] == ["EUR", "USD"]
    for item in items:
        assert [m["month"] for m in item["months"]] == ["2026-01", "2026-02", "2026-03"]
    jan, feb, mar = items[0]["months"]
    assert jan["gross_charges"] == "30.00" and jan["charges_count"] == 1
    assert feb["gross_charges"] == "100.00"
    assert mar == {
        "month": "2026-03",
        "gross_charges": "0.00",
        "refunds": "10.00",
        "net_expenses": "-10.00",
        "charges_count": 0,
        "refunds_count": 1,
    }
    assert items[1]["months"][1]["net_expenses"] == "0.00"


async def test_categories_use_snapshot(make_user):
    user = await make_user(timezone=TZ)
    category = (await user.post("/categories", {"name": "Transport"})).json()
    p = await pay(user, "20.00", "2026-02-02T10:00:00Z", category_id=category["id"])
    await refund(user, p, "5.00", "2026-02-03T10:00:00Z")
    await user.patch(f"/categories/{category['id']}", {"name": "Travel"})
    await pay(user, "8.00", "2026-02-04T10:00:00Z", category_id=category["id"])
    await pay(user, "10.00", "2026-02-05T10:00:00Z")

    r = await user.get("/analytics/categories", params={"date_from": "2026-02-01", "date_to": "2026-03-01"})
    [eur] = r.json()["items"]
    groups = {(c["category_id"], c["category_name"]): c for c in eur["categories"]}
    old = groups[(category["id"], "Transport")]
    assert (old["gross_charges"], old["refunds"], old["net_expenses"], old["payments_count"]) == (
        "20.00",
        "5.00",
        "15.00",
        2,
    )
    assert groups[(category["id"], "Travel")]["net_expenses"] == "8.00"
    none = groups[(None, None)]
    assert none["display_name"] == "Без категории" and none["net_expenses"] == "10.00"


async def test_upcoming_forecast(make_user):
    user = await make_user(timezone=TZ)
    month = add_months(local_today().replace(day=1), 2)
    month_param = f"{month.year:04d}-{month.month:02d}"
    category = (await user.post("/categories", {"name": "Media"})).json()

    await create_subscription(
        user,
        service_name="Streaming",
        amount="10.00",
        start_date=month.replace(day=5).isoformat(),
        category_id=category["id"],
    )
    await create_subscription(user, service_name="Cloud", amount="5.00", start_date=month.replace(day=20).isoformat())
    await create_subscription(
        user,
        service_name="Software",
        amount="60.00",
        billing_interval_unit="year",
        start_date=month.replace(day=10).isoformat(),
    )
    await create_subscription(
        user,
        service_name="Yearly other",
        amount="99.00",
        billing_interval_unit="year",
        start_date=add_months(month, 1).isoformat(),
    )
    weekly_start = local_today()
    await create_subscription(
        user, service_name="Gym", amount="7.00", billing_interval_unit="week", start_date=weekly_start.isoformat()
    )
    paused = await create_subscription(user, service_name="Paused", amount="1.00", start_date=month.isoformat())
    await user.patch(f"/subscriptions/{paused['id']}", {"status": "paused"})
    cancelled = await create_subscription(user, service_name="Cancelled", amount="1.00", start_date=month.isoformat())
    await user.patch(f"/subscriptions/{cancelled['id']}", {"status": "cancelled"})
    archived = await create_subscription(user, service_name="Archived", amount="1.00", start_date=month.isoformat())
    await user.delete(f"/subscriptions/{archived['id']}")
    await create_subscription(user, service_name="Dollar", amount="3.00", currency="USD", start_date=month.isoformat())

    r = await user.get("/analytics/upcoming", params={"month": month_param})
    assert r.status_code == 200, r.text
    body = r.json()
    weeks = len(occurrences_between(weekly_start, "week", 1, month, add_months(month, 1)))
    eur, usd = body["totals_by_currency"]
    assert eur["forecast_amount"] == f"{75 + 7 * weeks}.00"
    assert usd["forecast_amount"] == "3.00"
    names = [i["service_name"] for i in body["items"]]
    assert names.count("Gym") == weeks
    assert not {"Paused", "Cancelled", "Archived", "Yearly other"} & set(names)
    media = next(c for c in eur["categories"] if c["category_id"] == category["id"])
    assert media["forecast_amount"] == "10.00"
    streaming = next(i for i in body["items"] if i["service_name"] == "Streaming")
    assert streaming["expected_payment_date"] == month.replace(day=5).isoformat()
    assert streaming["billing_interval_unit"] == "month" and streaming["category_name"] == "Media"


async def test_upcoming_defaults_and_validation(make_user):
    user = await make_user(timezone=TZ)
    r = await user.get("/analytics/upcoming")
    expected = add_months(local_today().replace(day=1), 1)
    assert r.json()["month"] == f"{expected.year:04d}-{expected.month:02d}"
    assert r.json()["totals_by_currency"] == [] and r.json()["items"] == []
    past = add_months(local_today().replace(day=1), -1)
    for month in (f"{past.year:04d}-{past.month:02d}", "2026-13", "2026/12", "abc"):
        assert (await user.get("/analytics/upcoming", params={"month": month})).status_code == 422


async def test_upcoming_excludes_paid_period(make_user):
    user = await make_user(timezone=TZ)
    today = local_today()
    sub = await create_subscription(
        user, service_name="Daily", amount="1.00", billing_interval_unit="day", start_date=today.isoformat()
    )
    current = f"{today.year:04d}-{today.month:02d}"
    before = (await user.get("/analytics/upcoming", params={"month": current})).json()
    await user.post(
        "/payments",
        {
            "subscription_id": sub["id"],
            "amount": "1.00",
            "currency": "EUR",
            "occurred_at": datetime.now(ZoneInfo(TZ)).isoformat(),
            "billing_period_start": today.isoformat(),
        },
        idem=True,
    )
    after = (await user.get("/analytics/upcoming", params={"month": current})).json()
    assert len(after["items"]) == len(before["items"]) - 1
    assert today.isoformat() not in [i["expected_payment_date"] for i in after["items"]]
