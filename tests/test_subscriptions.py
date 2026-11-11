from datetime import UTC, date, datetime, timedelta


def today() -> date:
    return datetime.now(UTC).date()


async def create_subscription(account, **overrides):
    payload = {
        "service_name": "Netflix",
        "description": "Streaming subscription",
        "amount": "15.99",
        "currency": "EUR",
        "billing_interval_unit": "month",
        "interval_count": 1,
        "start_date": today().isoformat(),
        **overrides,
    }
    r = await account.post("/subscriptions", payload, idem=True)
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_and_get(user):
    sub = await create_subscription(user, start_date="2026-01-31")
    assert sub["status"] == "active" and sub["is_archived"] is False
    assert sub["schedule_anchor_date"] == "2026-01-31"
    assert sub["start_date"] == "2026-01-31"
    next_date = date.fromisoformat(sub["next_payment_date"])
    assert next_date >= today()
    assert next_date - today() < timedelta(days=32)
    assert sub["amount"] == "15.99"
    r = await user.get(f"/subscriptions/{sub['id']}")
    assert r.json() == sub


async def test_create_future_start(user):
    start = (today() + timedelta(days=40)).isoformat()
    sub = await create_subscription(user, start_date=start)
    assert sub["next_payment_date"] == start


async def test_create_validation(user):
    base = {
        "service_name": "S",
        "amount": "1.00",
        "currency": "EUR",
        "billing_interval_unit": "month",
        "interval_count": 1,
        "start_date": "2026-01-01",
    }
    cases = [
        {"amount": 1.5},
        {"amount": "-1"},
        {"amount": "1.001"},
        {"currency": "eur"},
        {"currency": "XYZ"},
        {"billing_interval_unit": "hour"},
        {"interval_count": 0},
        {"interval_count": 1201},
        {"service_name": "   "},
        {"status": "paused"},
        {"is_archived": True},
        {"category_id": "not-a-uuid"},
    ]
    for case in cases:
        r = await user.post("/subscriptions", {**base, **case}, idem=True)
        assert r.status_code == 422, case
    r = await user.post("/subscriptions", {**base, "currency": "JPY", "amount": "100"}, idem=True)
    assert r.status_code == 201 and r.json()["amount"] == "100"


async def test_ownership_returns_404(make_user):
    alice = await make_user()
    bob = await make_user()
    sub = await create_subscription(alice)
    url = f"/subscriptions/{sub['id']}"
    assert (await bob.get(url)).status_code == 404
    assert (await bob.patch(url, {"amount": "1.00"})).json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert (await bob.delete(url)).status_code == 404
    assert (await bob.post(f"{url}/restore")).status_code == 404
    assert (await bob.get("/subscriptions")).json()["pagination"]["total"] == 0


async def test_foreign_category_rejected(make_user):
    alice = await make_user()
    bob = await make_user()
    category = (await alice.post("/categories", {"name": "Alice"})).json()
    r = await bob.post(
        "/subscriptions",
        {
            "service_name": "S",
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


async def test_list_filters_sorting_pagination(user):
    a = await create_subscription(user, service_name="Alpha 100%", amount="30.00")
    b = await create_subscription(user, service_name="beta", amount="10.00")
    c = await create_subscription(user, service_name="Gamma", amount="20.00")
    await user.patch(f"/subscriptions/{c['id']}", {"status": "paused"})

    r = await user.get("/subscriptions", params={"sort_by": "amount", "sort_order": "desc"})
    assert [i["id"] for i in r.json()["items"]] == [a["id"], c["id"], b["id"]]
    r = await user.get("/subscriptions", params={"sort_by": "service_name"})
    assert [i["service_name"] for i in r.json()["items"]] == ["Alpha 100%", "beta", "Gamma"]
    r = await user.get("/subscriptions", params={"status": "paused"})
    assert [i["id"] for i in r.json()["items"]] == [c["id"]]
    r = await user.get("/subscriptions", params={"search": "100%"})
    assert [i["id"] for i in r.json()["items"]] == [a["id"]]
    r = await user.get("/subscriptions", params={"search": "%"})
    assert r.json()["pagination"]["total"] == 1
    r = await user.get("/subscriptions", params={"limit": 2, "offset": 2})
    assert r.json()["pagination"] == {"total": 3, "limit": 2, "offset": 2}
    assert len(r.json()["items"]) == 1

    next_date = a["next_payment_date"]
    after = (date.fromisoformat(next_date) + timedelta(days=1)).isoformat()
    r = await user.get("/subscriptions", params={"due_from": next_date, "due_to": after})
    assert r.json()["pagination"]["total"] == 3
    r = await user.get("/subscriptions", params={"due_to": next_date})
    assert r.json()["pagination"]["total"] == 0

    for params in ({"limit": 101}, {"limit": 0}, {"offset": -1}, {"sort_by": "user_id"}, {"status": "deleted"}):
        assert (await user.get("/subscriptions", params=params)).status_code == 422


async def test_manual_next_payment_date_becomes_anchor(user):
    sub = await create_subscription(user, start_date="2026-01-15")
    new_date = today() + timedelta(days=10)
    r = await user.patch(f"/subscriptions/{sub['id']}", {"next_payment_date": new_date.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["next_payment_date"] == new_date.isoformat()
    assert body["schedule_anchor_date"] == new_date.isoformat()
    assert body["start_date"] == "2026-01-15"
    r = await user.patch(f"/subscriptions/{sub['id']}", {"next_payment_date": "2025-12-31"})
    assert r.status_code == 422


async def test_interval_change_reanchors(user):
    sub = await create_subscription(user)
    r = await user.patch(f"/subscriptions/{sub['id']}", {"billing_interval_unit": "week", "interval_count": 2})
    body = r.json()
    assert body["schedule_anchor_date"] == sub["next_payment_date"]
    assert body["next_payment_date"] == sub["next_payment_date"]


async def test_patch_rules(user):
    sub = await create_subscription(user)
    url = f"/subscriptions/{sub['id']}"
    for payload in ({}, {"service_name": None}, {"start_date": "2026-01-01"}, {"is_archived": True}):
        assert (await user.patch(url, payload)).status_code == 422, payload
    assert (await user.patch(url, {"currency": "JPY"})).status_code == 422
    r = await user.patch(url, {"currency": "JPY", "amount": "1500", "description": None})
    assert r.status_code == 200 and r.json()["description"] is None and r.json()["amount"] == "1500"


async def test_status_lifecycle(user):
    sub = await create_subscription(user)
    url = f"/subscriptions/{sub['id']}"
    assert (await user.patch(url, {"status": "paused"})).json()["status"] == "paused"
    r = await user.patch(url, {"status": "active"})
    assert r.json()["status"] == "active"
    assert date.fromisoformat(r.json()["next_payment_date"]) >= today()
    assert (await user.patch(url, {"status": "cancelled"})).json()["status"] == "cancelled"
    r = await user.patch(url, {"status": "active"})
    assert r.status_code == 409


async def test_archive_and_restore(user):
    sub = await create_subscription(user)
    await user.patch(f"/subscriptions/{sub['id']}", {"status": "paused"})
    url = f"/subscriptions/{sub['id']}"
    assert (await user.delete(url)).status_code == 204
    assert (await user.delete(url)).status_code == 204
    r = await user.get(url)
    assert r.status_code == 200 and r.json()["is_archived"] is True and r.json()["status"] == "paused"
    assert (await user.get("/subscriptions")).json()["pagination"]["total"] == 0
    assert (await user.get("/subscriptions", params={"include_archived": "true"})).json()["pagination"]["total"] == 1
    r = await user.patch(url, {"amount": "1.00"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUBSCRIPTION_ARCHIVED"

    r = await user.post(f"{url}/restore")
    assert r.status_code == 200 and r.json()["is_archived"] is False and r.json()["status"] == "paused"


async def test_restore_keeps_cancelled(user):
    sub = await create_subscription(user)
    url = f"/subscriptions/{sub['id']}"
    await user.patch(url, {"status": "cancelled"})
    await user.delete(url)
    r = await user.post(f"{url}/restore")
    assert r.json()["status"] == "cancelled"
