from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import AuditLog, Payment, User
from tests.conftest import PASSWORD, new_client


async def test_profile_get_and_patch(user):
    r = await user.get("/users/me")
    assert r.status_code == 200
    assert r.json()["timezone"] == "UTC" and r.json()["preferred_currency"] == "EUR"

    r = await user.patch("/users/me", {"timezone": "Europe/Vilnius", "preferred_currency": "USD"})
    assert r.status_code == 200
    assert r.json()["timezone"] == "Europe/Vilnius"

    for payload in (
        {},
        {"timezone": "Mars/Base"},
        {"preferred_currency": "ABC"},
        {"timezone": None},
        {"email": "x@y.z"},
    ):
        r = await user.patch("/users/me", payload)
        assert r.status_code == 422, payload


async def test_password_change_revokes_sessions(user):
    r = await user.post("/users/me/password-change", {"current_password": "wrong-password!", "new_password": "x" * 20})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = await user.post("/users/me/password-change", {"current_password": PASSWORD, "new_password": "AnotherPass1234"})
    assert r.status_code == 204
    csrf = user.client.cookies.get("csrf_token", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401
    user.password = "AnotherPass1234"
    await user.login()


async def test_email_change_with_reservation(make_user, outbox):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")

    r = await alice.post("/users/me/email-change", {"new_email": "bob@example.com", "current_password": PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    r = await alice.post("/users/me/email-change", {"new_email": "new@example.com", "current_password": "bad"})
    assert r.status_code == 400

    r = await alice.post("/users/me/email-change", {"new_email": "New@Example.com", "current_password": PASSWORD})
    assert r.status_code == 202
    assert (await alice.get("/users/me")).json()["email"] == "alice@example.com"

    r = await bob.post("/users/me/email-change", {"new_email": "new@example.com", "current_password": PASSWORD})
    assert r.status_code == 409
    async with new_client() as c:
        r = await c.post("/api/v1/auth/register", json={"email": "new@example.com", "password": PASSWORD})
        assert r.status_code == 409

    token = outbox.token("new@example.com", "confirm-email-change")
    async with new_client() as c:
        r = await c.post("/api/v1/auth/confirm-email-change", json={"token": token})
        assert r.status_code == 200
        assert r.json() == {"id": r.json()["id"], "email": "new@example.com", "is_verified": True}
        r = await c.post("/api/v1/auth/confirm-email-change", json={"token": token})
        assert r.status_code == 400

    async with get_sessionmaker()() as session:
        alice_row = await session.scalar(select(User).where(User.email == "new@example.com"))
        assert alice_row.pending_email is None and alice_row.pending_email_reserved_until is None
    alice.email = "new@example.com"
    await alice.login()


async def test_expired_reservation_is_released(make_user, outbox):
    alice = await make_user()
    bob = await make_user()
    await alice.post("/users/me/email-change", {"new_email": "free@example.com", "current_password": PASSWORD})
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(User).where(User.email == alice.email))
        row.pending_email_reserved_until = func.now() - func.make_interval(0, 0, 0, 1)
        await session.commit()
    r = await bob.post("/users/me/email-change", {"new_email": "free@example.com", "current_password": PASSWORD})
    assert r.status_code == 202
    token = outbox.token("free@example.com", "confirm-email-change")
    async with new_client() as c:
        r = await c.post("/api/v1/auth/confirm-email-change", json={"token": token})
        assert r.json()["email"] == "free@example.com"


async def test_delete_account(user):
    r = await user.post("/categories", {"name": "Mine"})
    category_id = r.json()["id"]
    r = await user.post(
        "/subscriptions",
        {
            "service_name": "S",
            "amount": "5.00",
            "currency": "EUR",
            "billing_interval_unit": "month",
            "interval_count": 1,
            "start_date": "2026-01-01",
            "category_id": category_id,
        },
        idem=True,
    )
    assert r.status_code == 201
    await user.post(
        "/payments",
        {"service_name": "Taxi", "amount": "3.00", "currency": "EUR", "occurred_at": "2026-01-01T10:00:00Z"},
        idem=True,
    )
    r = await user.delete("/users/me", {"password": PASSWORD, "confirmation": "delete"})
    assert r.status_code == 422
    r = await user.delete("/users/me", {"password": "WrongPassword1", "confirmation": "DELETE"})
    assert r.status_code == 400
    r = await user.delete("/users/me", {"password": PASSWORD, "confirmation": "DELETE"})
    assert r.status_code == 204

    assert (await user.get("/users/me")).status_code == 401
    async with get_sessionmaker()() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 0
        event = await session.scalar(select(AuditLog).where(AuditLog.action == "account_deleted"))
        assert event.user_id is None
        assert (
            await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.user_id.is_not(None))) == 0
        )
