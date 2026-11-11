import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import delete

from app.config import get_settings
from app.context import request_id_var
from app.db import get_sessionmaker
from app.errors import error_response, register_error_handlers
from app.models import IdempotencyKey, OneTimeToken
from app.routers import analytics, auth, categories, payments, subscriptions, users
from app.services.auth import release_expired_reservations
from app.services.common import utcnow

logger = logging.getLogger(__name__)


async def cleanup_once() -> None:
    async with get_sessionmaker()() as session:
        now = utcnow()
        await release_expired_reservations(session)
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at <= now))
        await session.execute(delete(OneTimeToken).where(OneTimeToken.expires_at <= now))
        await session.commit()


async def cleanup_loop(interval: int) -> None:
    while True:
        try:
            await cleanup_once()
        except Exception as exc:
            logger.error("Cleanup failed: %s", type(exc).__name__)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    task = asyncio.create_task(cleanup_loop(settings.cleanup_interval_seconds))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def create_app() -> FastAPI:
    get_settings()
    app = FastAPI(title="Subscription Management API", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        value = str(uuid.uuid4())
        token = request_id_var.set(value)
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception("Unhandled error: %s", type(exc).__name__)
            response = error_response(500, "INTERNAL_ERROR", "Internal server error")
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = value
        return response

    register_error_handlers(app)
    api = APIRouter(prefix="/api/v1")
    for module in (auth, users, categories, subscriptions, payments, analytics):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
