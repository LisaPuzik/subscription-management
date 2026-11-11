from sqlalchemy import update

from app.db import get_sessionmaker
from app.models import AuthSession
from app.services.common import utcnow
from tests.test_auth import csrf
from tests.test_payments import NOW, charge, one_time, past_subscription
from tests.test_subscriptions import create_subscription


async def test_subscription_payment_rejects_service_name(user):
    sub, start = await past_subscription(user)
    r = await user.post("/payments", charge(sub, start.isoformat(), service_name="Other"), idem=True)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["field"] == "service_name"


async def test_refund_cannot_be_reversed(user):
    payment = (await user.post("/payments", one_time("5.00"), idem=True)).json()
    refund = await user.post(
        f"/payments/{payment['id']}/refunds", {"amount": "1.00", "occurred_at": NOW.isoformat()}, idem=True
    )
    r = await user.post(f"/payments/{refund.json()['id']}/reverse", {"reason": "Refund was entered twice"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVERSAL_NOT_ALLOWED"


async def test_subscription_category_change_and_restore_paths(user):
    category = (await user.post("/categories", {"name": "Work"})).json()
    sub = await create_subscription(user)
    url = f"/subscriptions/{sub['id']}"
    r = await user.patch(url, {"category_id": category["id"]})
    assert r.json()["category"] == {"id": category["id"], "name": "Work"}

    r = await user.post(f"{url}/restore")
    assert r.status_code == 200 and r.json()["is_archived"] is False

    await user.delete(url)
    r = await user.post(f"{url}/restore")
    assert r.json()["is_archived"] is False and r.json()["status"] == "active"
    assert r.json()["next_payment_date"] == sub["next_payment_date"]


async def test_refresh_with_unknown_token(user):
    user.client.cookies.set("refresh_token", "unknown-token", domain="test.local", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/refresh", headers=csrf(user))
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_with_expired_session(user):
    async with get_sessionmaker()() as session:
        await session.execute(update(AuthSession).values(expires_at=utcnow()))
        await session.commit()
    r = await user.client.post("/api/v1/auth/refresh", headers=csrf(user))
    assert r.status_code == 401


async def test_logout_with_foreign_or_unknown_cookie(user):
    user.client.cookies.set("refresh_token", "unknown-token", domain="test.local", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/jwt/logout", headers={**user.headers, **csrf(user)})
    assert r.status_code == 204


async def test_logout_requires_csrf_when_cookie_present(user):
    r = await user.client.post("/api/v1/auth/jwt/logout", headers=user.headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_TOKEN_INVALID"
