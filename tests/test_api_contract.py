async def test_openapi_is_generated(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for path in (
        "/api/v1/auth/register",
        "/api/v1/auth/jwt/login",
        "/api/v1/auth/refresh",
        "/api/v1/users/me",
        "/api/v1/subscriptions/{subscription_id}/restore",
        "/api/v1/payments/{payment_id}/reverse",
        "/api/v1/analytics/upcoming",
    ):
        assert path in paths


async def test_unknown_route_uses_error_format(client):
    r = await client.get("/api/v1/unknown")
    assert r.status_code == 404
    error = r.json()["error"]
    assert error["code"] == "RESOURCE_NOT_FOUND"
    assert error["request_id"] == r.headers["X-Request-ID"]


async def test_invalid_uuid_is_422(user):
    r = await user.get("/subscriptions/not-a-uuid")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
