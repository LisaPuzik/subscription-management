from tests.conftest import PASSWORD, Account


async def test_register_validation_and_duplicates(client, outbox):
    r = await client.post("/api/v1/auth/register", json={"email": "A@Example.com ", "password": PASSWORD})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "a@example.com"
    assert body["is_verified"] is False
    assert "hashed_password" not in body

    r = await client.post("/api/v1/auth/register", json={"email": "a@EXAMPLE.com", "password": PASSWORD})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"

    for payload in (
        {"email": "b@example.com", "password": "short"},
        {"email": "b@example.com", "password": "x" * 129},
        {"email": "verylongemail@example.com", "password": "verylongemail@example.com"},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "c@example.com", "password": PASSWORD, "is_superuser": True},
    ):
        r = await client.post("/api/v1/auth/register", json=payload)
        assert r.status_code == 422, payload
        error = r.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["request_id"]


async def test_login_requires_verified_email(client, outbox):
    await client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": PASSWORD})
    form = {"username": "u@example.com", "password": PASSWORD}
    r = await client.post("/api/v1/auth/jwt/login", data=form)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"

    token = outbox.token("u@example.com", "verify-email")
    r = await client.post("/api/v1/auth/verify", json={"token": token})
    assert r.status_code == 200 and r.json()["is_verified"] is True
    r = await client.post("/api/v1/auth/verify", json={"token": token})
    assert r.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    r = await client.post("/api/v1/auth/jwt/login", data={**form, "password": "WrongPassword123"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = await client.post("/api/v1/auth/jwt/login", data=form)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert "refresh_token" not in body
    cookies = r.headers.get_list("set-cookie")
    refresh = next(c for c in cookies if c.startswith("refresh_token="))
    csrf = next(c for c in cookies if c.startswith("csrf_token="))
    assert "HttpOnly" in refresh and "Secure" in refresh and "Path=/api/v1/auth" in refresh
    assert "HttpOnly" not in csrf


async def test_request_verify_token_does_not_leak(client, outbox):
    r1 = await client.post("/api/v1/auth/request-verify-token", json={"email": "nobody@example.com"})
    await client.post("/api/v1/auth/register", json={"email": "v@example.com", "password": PASSWORD})
    old = outbox.token("v@example.com", "verify-email")
    r2 = await client.post("/api/v1/auth/request-verify-token", json={"email": "v@example.com"})
    assert r1.status_code == r2.status_code == 202
    assert r1.json() == r2.json()
    new = outbox.token("v@example.com", "verify-email")
    assert new != old
    r = await client.post("/api/v1/auth/verify", json={"token": old})
    assert r.status_code == 400
    r = await client.post("/api/v1/auth/verify", json={"token": new})
    assert r.status_code == 200


async def test_protected_endpoints_require_token(client):
    for url in ("/api/v1/users/me", "/api/v1/subscriptions", "/api/v1/analytics/upcoming"):
        r = await client.get(url)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    r = await client.get("/api/v1/users/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def csrf(account: Account) -> dict:
    return {"X-CSRF-Token": account.client.cookies.get("csrf_token", path="/api/v1/auth")}


async def test_refresh_rotation_and_reuse_detection(user):
    old_refresh = user.client.cookies.get("refresh_token", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/refresh")
    assert r.status_code == 403

    r = await user.client.post("/api/v1/auth/refresh", headers=csrf(user))
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    new_refresh = user.client.cookies.get("refresh_token", path="/api/v1/auth")
    assert new_refresh != old_refresh

    user.client.cookies.set("refresh_token", old_refresh, domain="test.local", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/refresh", headers=csrf(user))
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"

    user.client.cookies.set("refresh_token", new_refresh, domain="test.local", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/refresh", headers=csrf(user))
    assert r.status_code == 401


async def test_refresh_without_cookie(client):
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_logout_revokes_refresh(user):
    refresh = user.client.cookies.get("refresh_token", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/jwt/logout", headers={**user.headers, **csrf(user)})
    assert r.status_code == 204 and r.content == b""
    user.client.cookies.set("refresh_token", refresh, domain="test.local", path="/api/v1/auth")
    user.client.cookies.set("csrf_token", "x" * 20, domain="test.local", path="/api/v1/auth")
    r = await user.client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "x" * 20})
    assert r.status_code == 401
    r = await user.get("/users/me")
    assert r.status_code == 200


async def test_forgot_and_reset_password(user, outbox):
    r1 = await user.client.post("/api/v1/auth/forgot-password", json={"email": user.email})
    r2 = await user.client.post("/api/v1/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r1.status_code == r2.status_code == 202
    assert r1.json() == r2.json()

    token = outbox.token(user.email, "reset-password")
    r = await user.client.post("/api/v1/auth/reset-password", json={"token": token, "password": "short"})
    assert r.status_code == 422
    new_password = "BrandNewPassword42!"
    r = await user.client.post("/api/v1/auth/reset-password", json={"token": token, "password": new_password})
    assert r.status_code == 200
    r = await user.client.post("/api/v1/auth/reset-password", json={"token": token, "password": new_password})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    r = await user.client.post("/api/v1/auth/refresh", headers=csrf(user))
    assert r.status_code == 401
    user.password = new_password
    await user.login()


async def test_rate_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_requests", 3)
    codes = [
        (await client.post("/api/v1/auth/forgot-password", json={"email": "x@example.com"})).status_code
        for _ in range(4)
    ]
    assert codes == [202, 202, 202, 429]
