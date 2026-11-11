import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from pytest_asyncio import is_async_test

ROOT = Path(__file__).resolve().parent.parent


def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(Path(tempfile.gettempdir()) / "sub-management-pgdata", cleanup_mode=None)
    server.psql("DROP DATABASE IF EXISTS sub_test WITH (FORCE);")
    server.psql("CREATE DATABASE sub_test;")
    return server.get_uri("sub_test").replace("postgresql://", "postgresql+asyncpg://")


os.environ.update(
    DATABASE_URL=database_url(),
    JWT_SECRET="test-secret-" + "x" * 32,
    ENVIRONMENT="test",
    RATE_LIMIT_REQUESTS="10000",
    DEFAULT_CURRENCY="EUR",
)

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import get_engine
from app.email import get_email_sender
from app.main import app
from app.ratelimit import limiter

PASSWORD = "CorrectHorse123!"
CLEANUP = (
    "TRUNCATE audit_log, idempotency_key, one_time_token, auth_session, payment, subscription",
    "DELETE FROM category WHERE NOT is_system",
    'DELETE FROM "user"',
)


def pytest_collection_modifyitems(items):
    marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if is_async_test(item):
            item.add_marker(marker, append=False)


@pytest.fixture(scope="session", autouse=True)
def migrated():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")


class Outbox:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.messages.append((to, subject, body))

    def token(self, to: str, path: str) -> str:
        for recipient, _, body in reversed(self.messages):
            if recipient == to and f"/{path}?" in body:
                return re.search(r"token=([\w-]+)", body).group(1)
        raise AssertionError(f"no {path} email for {to}")


@pytest.fixture
def outbox():
    box = Outbox()
    app.dependency_overrides[get_email_sender] = lambda: box
    yield box
    app.dependency_overrides.pop(get_email_sender, None)


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    yield
    limiter.reset()
    async with get_engine().begin() as conn:
        for statement in CLEANUP:
            await conn.execute(text(statement))


def new_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@dataclass
class Account:
    client: AsyncClient
    email: str
    password: str
    access: str = ""

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access}"}

    def key(self) -> dict:
        return {**self.headers, "Idempotency-Key": f"key-{uuid.uuid4().hex}"}

    async def get(self, url: str, **kw):
        return await self.client.get(f"/api/v1{url}", headers=self.headers, **kw)

    async def post(self, url: str, json=None, idem: bool = False, headers: dict | None = None):
        base = self.key() if idem else self.headers
        return await self.client.post(f"/api/v1{url}", json=json, headers={**base, **(headers or {})})

    async def patch(self, url: str, json):
        return await self.client.patch(f"/api/v1{url}", json=json, headers=self.headers)

    async def delete(self, url: str, json=None):
        return await self.client.request("DELETE", f"/api/v1{url}", json=json, headers=self.headers)

    async def login(self):
        r = await self.client.post("/api/v1/auth/jwt/login", data={"username": self.email, "password": self.password})
        assert r.status_code == 200, r.text
        self.access = r.json()["access_token"]
        return r


@pytest_asyncio.fixture
async def make_user(outbox):
    clients = []

    async def factory(email: str | None = None, timezone: str | None = None, verify: bool = True) -> Account:
        email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
        client = new_client()
        clients.append(client)
        r = await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
        assert r.status_code == 201, r.text
        account = Account(client, email, PASSWORD)
        if verify:
            r = await client.post("/api/v1/auth/verify", json={"token": outbox.token(email, "verify-email")})
            assert r.status_code == 200, r.text
            await account.login()
            if timezone:
                r = await account.patch("/users/me", {"timezone": timezone})
                assert r.status_code == 200, r.text
        return account

    yield factory
    for client in clients:
        await client.aclose()


@pytest_asyncio.fixture
async def user(make_user) -> Account:
    return await make_user()


@pytest_asyncio.fixture
async def client():
    async with new_client() as c:
        yield c
