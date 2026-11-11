import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import AuthSession, OneTimeToken, User
from app.services.common import audit, hash_token, new_token, utcnow


def invalid_refresh() -> AppError:
    return AppError(401, "INVALID_REFRESH_TOKEN", "Refresh token is invalid")


def invalid_token() -> AppError:
    return AppError(400, "INVALID_OR_EXPIRED_TOKEN", "Token is invalid or expired")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[AuthSession, str]:
    token = new_token()
    auth_session = AuthSession(
        id=uuid.uuid4(),
        user_id=user_id,
        refresh_token_hash=hash_token(token),
        token_family_id=family_id or uuid.uuid4(),
        expires_at=utcnow() + timedelta(days=get_settings().refresh_token_ttl_days),
        user_agent=(user_agent or "")[:512] or None,
        ip_address=ip_address,
    )
    session.add(auth_session)
    return auth_session, token


async def rotate_session(
    session: AsyncSession, token: str, user_agent: str | None, ip_address: str | None
) -> tuple[uuid.UUID, str]:
    current = await session.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == hash_token(token)).with_for_update()
    )
    if current is None:
        raise invalid_refresh()
    now = utcnow()
    if current.revoked_at is not None:
        await revoke_family(session, current.user_id, current.token_family_id)
        await session.commit()
        raise invalid_refresh()
    user = await session.get(User, current.user_id)
    if current.expires_at <= now or user is None or not user.is_active:
        raise invalid_refresh()
    new_session, new_refresh = create_session(session, current.user_id, current.token_family_id, user_agent, ip_address)
    await session.flush()
    current.revoked_at = now
    current.replaced_by_session_id = new_session.id
    return current.user_id, new_refresh


async def revoke_family(session: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.token_family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    audit(session, user_id, "auth_session", family_id, "session_family_revoked")


async def revoke_session(session: AsyncSession, user_id: uuid.UUID, token: str) -> None:
    current = await session.scalar(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == hash_token(token),
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    if current is not None:
        current.revoked_at = utcnow()
        audit(session, user_id, "auth_session", current.id, "session_revoked")


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    audit(session, user_id, "user", user_id, "all_sessions_revoked")


async def issue_one_time_token(
    session: AsyncSession, user_id: uuid.UUID, token_type: str, ttl: timedelta, email: str | None = None
) -> str:
    await session.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user_id,
            OneTimeToken.token_type == token_type,
            OneTimeToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )
    token = new_token()
    session.add(
        OneTimeToken(
            user_id=user_id,
            token_hash=hash_token(token),
            token_type=token_type,
            email=email,
            expires_at=utcnow() + ttl,
        )
    )
    return token


async def consume_one_time_token(session: AsyncSession, token: str, token_type: str) -> OneTimeToken:
    record = await session.scalar(
        select(OneTimeToken)
        .where(OneTimeToken.token_hash == hash_token(token), OneTimeToken.token_type == token_type)
        .with_for_update()
    )
    now = utcnow()
    if record is None or record.used_at is not None or record.expires_at <= now:
        raise invalid_token()
    record.used_at = now
    return record


async def release_expired_reservations(session: AsyncSession) -> None:
    await session.execute(
        update(User)
        .where(User.pending_email.is_not(None), User.pending_email_reserved_until <= utcnow())
        .values(pending_email=None, pending_email_reserved_until=None)
    )


async def email_taken(session: AsyncSession, email: str, exclude_user: uuid.UUID | None = None) -> bool:
    query = select(User.id).where((User.email == email) | (User.pending_email == email))
    if exclude_user is not None:
        query = query.where(User.id != exclude_user)
    return await session.scalar(query.limit(1)) is not None
