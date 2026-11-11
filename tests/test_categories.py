from datetime import UTC, datetime

from tests.test_subscriptions import create_subscription


async def system_category(account, name="Entertainment"):
    r = await account.get("/categories", params={"limit": 100})
    return next(c for c in r.json()["items"] if c["name"] == name)


async def test_list_contains_system_and_own_only(make_user):
    alice = await make_user()
    bob = await make_user()
    await alice.post("/categories", {"name": "Alice only", "color": "#3498db"})
    r = await bob.get("/categories", params={"limit": 100})
    names = [c["name"] for c in r.json()["items"]]
    assert "Alice only" not in names
    assert "Entertainment" in names
    assert r.json()["pagination"]["limit"] == 100


async def test_create_validation_and_uniqueness(user):
    r = await user.post("/categories", {"name": "  Games ", "color": "#3498db"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Games" and body["color"] == "#3498DB"
    assert body["is_system"] is False and body["user_id"]

    r = await user.post("/categories", {"name": "GAMES"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CATEGORY_ALREADY_EXISTS"
    for payload in (
        {"name": ""},
        {"name": "x" * 101},
        {"name": "A", "color": "blue"},
        {"name": "B", "is_system": True},
    ):
        assert (await user.post("/categories", payload)).status_code == 422


async def test_foreign_and_system_categories_protected(make_user):
    alice = await make_user()
    bob = await make_user()
    own = (await alice.post("/categories", {"name": "Private"})).json()
    assert (await bob.get(f"/categories/{own['id']}")).status_code == 404
    assert (await bob.patch(f"/categories/{own['id']}", {"name": "Hacked"})).status_code == 404
    assert (await bob.delete(f"/categories/{own['id']}")).status_code == 404

    system = await system_category(bob)
    assert (await bob.get(f"/categories/{system['id']}")).status_code == 200
    r = await bob.patch(f"/categories/{system['id']}", {"name": "Mine"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SYSTEM_CATEGORY_READ_ONLY"
    assert (await bob.delete(f"/categories/{system['id']}")).status_code == 403


async def test_deactivation_clears_subscriptions_and_keeps_history(user):
    category = (await user.post("/categories", {"name": "Video"})).json()
    sub = await create_subscription(user, category_id=category["id"])
    now = datetime.now(UTC).isoformat()
    payment = await user.post(
        "/payments",
        {
            "subscription_id": sub["id"],
            "amount": "9.99",
            "currency": "EUR",
            "occurred_at": now,
            "billing_period_start": sub["next_payment_date"],
        },
        idem=True,
    )
    assert payment.json()["category_snapshot"] == "Video"

    await user.patch(f"/categories/{category['id']}", {"name": "Movies"})
    assert (await user.delete(f"/categories/{category['id']}")).status_code == 204
    assert (await user.delete(f"/categories/{category['id']}")).status_code == 204

    r = await user.get(f"/subscriptions/{sub['id']}")
    assert r.json()["category"] is None
    r = await user.get(f"/payments/{payment.json()['id']}")
    assert r.json()["category_id"] == category["id"] and r.json()["category_snapshot"] == "Video"

    names = [c["name"] for c in (await user.get("/categories", params={"limit": 100})).json()["items"]]
    assert "Movies" not in names
    r = await user.get("/categories", params={"include_inactive": "true", "limit": 100})
    assert "Movies" in [c["name"] for c in r.json()["items"]]

    r = await user.post(
        "/subscriptions",
        {
            "service_name": "X",
            "amount": "1.00",
            "currency": "EUR",
            "billing_interval_unit": "month",
            "interval_count": 1,
            "start_date": "2026-01-01",
            "category_id": category["id"],
        },
        idem=True,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "CATEGORY_NOT_AVAILABLE"

    assert (await user.post("/categories", {"name": "movies"})).status_code == 409
    r = await user.patch(f"/categories/{category['id']}", {"is_active": True})
    assert r.status_code == 200 and r.json()["is_active"] is True
