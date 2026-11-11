import asyncio

from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Payment, Subscription
from tests.test_payments import one_time

SUB = {
    "service_name": "Spotify",
    "amount": "9.99",
    "currency": "EUR",
    "billing_interval_unit": "month",
    "interval_count": 1,
    "start_date": "2026-01-10",
}


async def count(model) -> int:
    async with get_sessionmaker()() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_header_required_and_length(user):
    r = await user.post("/subscriptions", SUB)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    r = await user.post("/payments", one_time(), headers={"Idempotency-Key": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["details"]["field"] == "Idempotency-Key"
    r = await user.post("/payments", one_time(), headers={"Idempotency-Key": "k" * 129})
    assert r.status_code == 422


async def test_repeat_returns_saved_response(user):
    headers = {"Idempotency-Key": "subscription-key-0001"}
    first = await user.post("/subscriptions", SUB, headers=headers)
    second = await user.post("/subscriptions", SUB, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert await count(Subscription) == 1

    r = await user.post("/subscriptions", {**SUB, "amount": "1.00"}, headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    r = await user.post("/payments", one_time(), headers=headers)
    assert r.status_code == 409


async def test_failed_request_does_not_store_key(user):
    headers = {"Idempotency-Key": "payment-key-000001"}
    r = await user.post("/payments", one_time(amount="1.001"), headers=headers)
    assert r.status_code == 422
    r = await user.post("/payments", one_time(amount="1.00"), headers=headers)
    assert r.status_code == 201


async def test_keys_are_per_user(make_user):
    alice = await make_user()
    bob = await make_user()
    headers = {"Idempotency-Key": "shared-key-0000001"}
    a = await alice.post("/payments", one_time(), headers=headers)
    b = await bob.post("/payments", one_time(), headers=headers)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


async def test_concurrent_same_key(user):
    headers = {"Idempotency-Key": "concurrent-key-0001"}
    results = await asyncio.gather(*(user.post("/payments", one_time(), headers=headers) for _ in range(5)))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["id"] for r in results}) == 1
    assert await count(Payment) == 1


async def test_refund_key_bound_to_payment(user):
    p1 = (await user.post("/payments", one_time("10.00"), idem=True)).json()
    p2 = (await user.post("/payments", one_time("10.00"), idem=True)).json()
    headers = {"Idempotency-Key": "refund-key-0000001"}
    body = {"amount": "1.00", "occurred_at": p1["occurred_at"]}
    first = await user.post(f"/payments/{p1['id']}/refunds", body, headers=headers)
    again = await user.post(f"/payments/{p1['id']}/refunds", body, headers=headers)
    assert first.json() == again.json()
    other = await user.post(f"/payments/{p2['id']}/refunds", body, headers=headers)
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
