import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import IdempotencyKey, User
from app.services.common import utcnow


async def idempotency_key(key: str | None = Header(None, alias="Idempotency-Key")) -> str:
    if key is None:
        raise AppError(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if not 16 <= len(key) <= 128:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Invalid request data",
            {"field": "Idempotency-Key", "reason": "Length must be between 16 and 128 characters"},
        )
    return key


def request_hash(body: BaseModel, params: dict) -> str:
    payload = {"body": jsonable_encoder(body.model_dump()), "params": jsonable_encoder(params)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def key_reused() -> AppError:
    return AppError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key was used with another request")


async def run_idempotent(
    session: AsyncSession,
    user: User,
    key: str,
    operation: str,
    digest: str,
    status: int,
    handler: Callable[[], Awaitable[tuple[uuid.UUID, BaseModel]]],
) -> JSONResponse:
    other_operation = await session.scalar(
        select(IdempotencyKey.id).where(
            IdempotencyKey.user_id == user.id,
            IdempotencyKey.key == key,
            IdempotencyKey.operation != operation,
            IdempotencyKey.expires_at > utcnow(),
        )
    )
    if other_operation is not None:
        raise key_reused()
    scope = (
        IdempotencyKey.user_id == user.id,
        IdempotencyKey.operation == operation,
        IdempotencyKey.key == key,
    )
    await session.execute(delete(IdempotencyKey).where(*scope, IdempotencyKey.expires_at <= utcnow()))
    inserted = await session.scalar(
        insert(IdempotencyKey)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            key=key,
            operation=operation,
            request_hash=digest,
            expires_at=utcnow() + timedelta(hours=get_settings().idempotency_ttl_hours),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency_key")
        .returning(IdempotencyKey.id)
    )
    if inserted is None:
        existing = (
            await session.execute(
                select(IdempotencyKey.request_hash, IdempotencyKey.response_status, IdempotencyKey.response_body).where(
                    *scope
                )
            )
        ).first()
        await session.rollback()
        if existing is None or existing.request_hash != digest:
            raise key_reused()
        return JSONResponse(status_code=existing.response_status, content=existing.response_body)

    resource_id, result = await handler()
    body = jsonable_encoder(result)
    record = await session.get(IdempotencyKey, inserted)
    record.response_status = status
    record.response_body = body
    record.resource_id = resource_id
    await session.commit()
    return JSONResponse(status_code=status, content=body)
