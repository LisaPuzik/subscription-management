import asyncio
from datetime import UTC, date, datetime, timedelta

from app.domain.schedule import add_months, first_after
from tests.test_subscriptions import create_subscription, today

NOW = datetime.now(UTC)


def charge(sub: dict, period: str, amount: str = "15.99", **extra) -> dict:
    return {
        "subscription_id": sub["id"],
        "amount": amount,
        "currency": "EUR",
        "occurred_at": NOW.isoformat(),
        "billing_period_start": period,
        **extra,
    }


def one_time(amount: str = "12.50", occurred_at: str | None = None, **extra) -> dict:
    return {
        "service_name": "Taxi",
        "amount": amount,
        "currency": "EUR",
        "occurred_at": occurred_at or NOW.isoformat(),
        **extra,
    }


async def past_subscription(user, **overrides):
    start = add_months(today(), -3)
    return await create_subscription(user, start_date=start.isoformat(), **overrides), start


async def test_pay_current_period_advances_schedule(user):
    sub, _ = await past_subscription(user)
    r = await user.post(
        "/payments", charge(sub, sub["next_payment_date"], occurred_at="2026-11-10T09:30:00+02:00"), idem=True
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["occurred_at"] == "2026-11-10T07:30:00Z"
    assert (body["payment_type"], body["status"], body["source"]) == ("charge", "succeeded", "manual")
    assert body["service_name_snapshot"] == "Netflix"
    expected = first_after(
        date.fromisoformat(sub["schedule_anchor_date"]), "month", 1, date.fromisoformat(sub["next_payment_date"])
    )
    updated = (await user.get(f"/subscriptions/{sub['id']}")).json()
    assert updated["next_payment_date"] == expected.isoformat()


async def test_historical_period_keeps_schedule(user):
    sub, start = await past_subscription(user)
    r = await user.post("/payments", charge(sub, start.isoformat()), idem=True)
    assert r.status_code == 201
    updated = (await user.get(f"/subscriptions/{sub['id']}")).json()
    assert updated["next_payment_date"] == sub["next_payment_date"]


async def test_billing_period_rules(user):
    sub, start = await past_subscription(user)
    future = add_months(date.fromisoformat(sub["next_payment_date"]), 1).isoformat()
    r = await user.post("/payments", charge(sub, future), idem=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FUTURE_BILLING_PERIOD_NOT_ALLOWED"
    r = await user.post("/payments", charge(sub, (start + timedelta(days=1)).isoformat()), idem=True)
    assert r.status_code == 422
    r = await user.post("/payments", charge(sub, (start - timedelta(days=31)).isoformat()), idem=True)
    assert r.status_code == 422
    r = await user.post("/payments", charge(sub, sub["next_payment_date"], currency="USD"), idem=True)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CURRENCY_MISMATCH"
    payload = charge(sub, start.isoformat())
    payload.pop("billing_period_start")
    assert (await user.post("/payments", payload, idem=True)).status_code == 422

    assert (await user.post("/payments", charge(sub, start.isoformat()), idem=True)).status_code == 201
    r = await user.post("/payments", charge(sub, start.isoformat()), idem=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_BILLING_PERIOD"


async def test_subscription_states(user):
    paused, _ = await past_subscription(user)
    await user.patch(f"/subscriptions/{paused['id']}", {"status": "paused"})
    r = await user.post("/payments", charge(paused, paused["next_payment_date"]), idem=True)
    assert r.status_code == 201
    after = (await user.get(f"/subscriptions/{paused['id']}")).json()
    assert after["status"] == "paused" and after["next_payment_date"] > paused["next_payment_date"]

    cancelled, _ = await past_subscription(user)
    await user.patch(f"/subscriptions/{cancelled['id']}", {"status": "cancelled"})
    r = await user.post("/payments", charge(cancelled, cancelled["next_payment_date"]), idem=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUBSCRIPTION_NOT_ACTIVE"

    archived, _ = await past_subscription(user)
    await user.delete(f"/subscriptions/{archived['id']}")
    r = await user.post("/payments", charge(archived, archived["next_payment_date"]), idem=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUBSCRIPTION_ARCHIVED"


async def test_one_time_payment_validation(user):
    r = await user.post("/payments", one_time(), idem=True)
    assert r.status_code == 201 and r.json()["subscription_id"] is None
    cases = [
        {"service_name": None},
        {"occurred_at": "2026-01-01T10:00:00"},
        {"occurred_at": (NOW + timedelta(minutes=10)).isoformat()},
        {"billing_period_start": "2026-01-01"},
        {"payment_type": "refund"},
        {"status": "pending"},
        {"source": "provider"},
        {"amount": "0"},
        {"amount": 10},
    ]
    for case in cases:
        payload = {**one_time(), **case}
        payload = {k: v for k, v in payload.items() if v is not None}
        r = await user.post("/payments", payload, idem=True)
        assert r.status_code == 422, case
    r = await user.post("/payments", one_time(occurred_at=(NOW + timedelta(minutes=2)).isoformat()), idem=True)
    assert r.status_code == 201


async def test_subscription_change_keeps_payment_history(user):
    sub, start = await past_subscription(user)
    payment = (await user.post("/payments", charge(sub, start.isoformat()), idem=True)).json()
    await user.patch(f"/subscriptions/{sub['id']}", {"service_name": "Other", "amount": "99.00"})
    await user.delete(f"/subscriptions/{sub['id']}")
    stored = (await user.get(f"/payments/{payment['id']}")).json()
    assert stored["service_name_snapshot"] == "Netflix" and stored["amount"] == "15.99"


async def test_payment_ownership(make_user):
    alice = await make_user()
    bob = await make_user()
    sub, start = await past_subscription(alice)
    payment = (await alice.post("/payments", charge(sub, start.isoformat()), idem=True)).json()
    assert (await bob.get(f"/payments/{payment['id']}")).status_code == 404
    r = await bob.post(
        f"/payments/{payment['id']}/refunds", {"amount": "1.00", "occurred_at": NOW.isoformat()}, idem=True
    )
    assert r.status_code == 404
    r = await bob.post(f"/payments/{payment['id']}/reverse", {"reason": "not my payment at all"})
    assert r.status_code == 404
    r = await bob.post("/payments", charge(sub, sub["next_payment_date"]), idem=True)
    assert r.status_code == 404
    assert (await bob.get("/payments")).json()["pagination"]["total"] == 0


async def test_refunds(user):
    payment = (await user.post("/payments", one_time("10.00"), idem=True)).json()
    url = f"/payments/{payment['id']}/refunds"
    refund = {"amount": "4.00", "occurred_at": NOW.isoformat()}

    r = await user.post(url, refund, idem=True)
    assert r.status_code == 201
    body = r.json()
    assert body["payment_type"] == "refund" and body["original_payment_id"] == payment["id"]
    assert body["currency"] == "EUR" and body["service_name_snapshot"] == "Taxi"

    r = await user.post(url, {**refund, "amount": "6.01"}, idem=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUND_LIMIT_EXCEEDED"
    assert (await user.post(url, {**refund, "amount": "6.00"}, idem=True)).status_code == 201
    assert (await user.post(url, {**refund, "amount": "0.01"}, idem=True)).status_code == 409

    r = await user.post(f"/payments/{body['id']}/refunds", refund, idem=True)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUND_NOT_ALLOWED"
    for case in ({"currency": "USD"}, {"amount": "-1"}, {"occurred_at": "2000-01-01T00:00:00Z"}):
        assert (await user.post(url, {**refund, **case}, idem=True)).status_code == 422, case

    detail = (await user.get(f"/payments/{payment['id']}")).json()
    assert [r["amount"] for r in detail["refunds"]] == ["4.00", "6.00"]


async def test_concurrent_refunds_respect_limit(user):
    payment = (await user.post("/payments", one_time("10.00"), idem=True)).json()
    url = f"/payments/{payment['id']}/refunds"
    results = await asyncio.gather(
        *(user.post(url, {"amount": "3.00", "occurred_at": NOW.isoformat()}, idem=True) for _ in range(5))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 201, 201, 409, 409]


async def test_reverse(user):
    sub, start = await past_subscription(user)
    payment = (await user.post("/payments", charge(sub, start.isoformat()), idem=True)).json()
    url = f"/payments/{payment['id']}/reverse"
    assert (await user.post(url, {"reason": "short"})).status_code == 422
    r = await user.post(url, {"reason": "Payment was recorded by mistake"})
    assert r.status_code == 200
    assert r.json()["status"] == "reversed" and r.json()["reason"] == "Payment was recorded by mistake"
    r = await user.post(url, {"reason": "Payment was recorded by mistake"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAYMENT_ALREADY_REVERSED"
    r = await user.post(
        f"/payments/{payment['id']}/refunds", {"amount": "1.00", "occurred_at": NOW.isoformat()}, idem=True
    )
    assert r.json()["error"]["code"] == "REFUND_NOT_ALLOWED"
    assert (await user.post("/payments", charge(sub, start.isoformat()), idem=True)).status_code == 201

    refunded = (await user.post("/payments", one_time("5.00"), idem=True)).json()
    await user.post(
        f"/payments/{refunded['id']}/refunds", {"amount": "1.00", "occurred_at": NOW.isoformat()}, idem=True
    )
    r = await user.post(f"/payments/{refunded['id']}/reverse", {"reason": "Payment was recorded by mistake"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVERSAL_NOT_ALLOWED"


async def test_payment_history_filters(user):
    sub, start = await past_subscription(user)
    await user.post("/payments", charge(sub, start.isoformat(), occurred_at="2026-03-10T10:00:00Z"), idem=True)
    taxi = (await user.post("/payments", one_time("7.00", "2026-04-10T10:00:00Z"), idem=True)).json()
    await user.post("/payments", {**one_time("3.00", "2026-04-11T10:00:00Z"), "currency": "USD"}, idem=True)
    await user.post(
        f"/payments/{taxi['id']}/refunds", {"amount": "1.00", "occurred_at": "2026-05-01T10:00:00Z"}, idem=True
    )

    async def ids(**params):
        r = await user.get("/payments", params=params)
        assert r.status_code == 200, r.text
        return [i["amount"] for i in r.json()["items"]]

    assert await ids() == ["1.00", "3.00", "7.00", "15.99"]
    assert await ids(sort_order="asc", sort_by="amount") == ["1.00", "3.00", "7.00", "15.99"]
    assert await ids(date_from="2026-04-01", date_to="2026-05-01") == ["3.00", "7.00"]
    assert await ids(date_from="2026-04-11") == ["1.00", "3.00"]
    assert await ids(currency="USD") == ["3.00"]
    assert await ids(payment_type="refund") == ["1.00"]
    assert await ids(subscription_id=sub["id"]) == ["15.99"]
    r = await user.get("/payments", params={"date_from": "2026-05-01", "date_to": "2026-04-01"})
    assert r.status_code == 422
    item = (await user.get("/payments", params={"payment_type": "refund"})).json()["items"][0]
    assert item["service_name"] == "Taxi" and item["original_payment_id"] == taxi["id"]
