import hashlib
import secrets
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.context import request_id_var
from app.models import AuditLog, User


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_today(user: User) -> date:
    return datetime.now(ZoneInfo(user.timezone)).date()


def local_start(day: date, timezone: str) -> datetime:
    return datetime.combine(day, time(), tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def to_json(value):
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def audit(
    session: AsyncSession,
    user_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    old: dict | None = None,
    new: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            old_values=to_json(old) if old is not None else None,
            new_values=to_json(new) if new is not None else None,
            request_id=request_id_var.get(),
        )
    )


def snapshot(obj, fields: list[str]) -> dict:
    return {f: getattr(obj, f) for f in fields}
