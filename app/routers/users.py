from datetime import timedelta

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import UserManager, check_password, current_active_user, get_user_manager
from app.config import get_settings
from app.db import get_session
from app.email import EmailSender, get_email_sender, link
from app.errors import AppError, validation_error
from app.models import AuthSession, Category, IdempotencyKey, OneTimeToken, Payment, Subscription, User
from app.routers.auth import email_exists
from app.schemas.common import Message
from app.schemas.users import DeleteAccountIn, EmailChangeIn, PasswordChangeIn, UserOut, UserPatch
from app.services import auth as auth_service
from app.services.common import audit, utcnow

router = APIRouter(prefix="/users", tags=["users"])


def verify_password(manager: UserManager, user: User, password: str) -> None:
    valid, _ = manager.password_helper.verify_and_update(password, user.hashed_password)
    if not valid:
        raise AppError(400, "INVALID_CREDENTIALS", "Invalid credentials")


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(current_active_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
async def patch_me(
    body: UserPatch,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    changes = body.changes()
    old = {k: getattr(user, k) for k in changes}
    for key, value in changes.items():
        setattr(user, key, value)
    audit(session, user.id, "user", user.id, "profile_updated", old, changes)
    await session.commit()
    return user


@router.post("/me/password-change", status_code=204, response_class=Response)
async def change_password(
    body: PasswordChangeIn,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
    manager: UserManager = Depends(get_user_manager),
) -> Response:
    verify_password(manager, user, body.current_password)
    await check_password(manager, body.new_password, user)
    user.hashed_password = manager.password_helper.hash(body.new_password)
    await auth_service.revoke_all_sessions(session, user.id)
    audit(session, user.id, "user", user.id, "password_changed")
    await session.commit()
    return Response(status_code=204)


@router.post("/me/email-change", status_code=202, response_model=Message)
async def request_email_change(
    body: EmailChangeIn,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
    manager: UserManager = Depends(get_user_manager),
    sender: EmailSender = Depends(get_email_sender),
) -> JSONResponse:
    verify_password(manager, user, body.current_password)
    new_email = auth_service.normalize_email(body.new_email)
    if new_email == user.email:
        raise validation_error("new_email", "New email must differ from the current one")
    await auth_service.release_expired_reservations(session)
    if await auth_service.email_taken(session, new_email, exclude_user=user.id):
        raise email_exists()
    ttl = timedelta(hours=get_settings().email_change_ttl_hours)
    user.pending_email = new_email
    user.pending_email_reserved_until = utcnow() + ttl
    token = await auth_service.issue_one_time_token(session, user.id, "change_email", ttl, new_email)
    audit(session, user.id, "user", user.id, "email_change_requested")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise email_exists() from None
    await sender.send(new_email, "Confirm your new email", link("confirm-email-change", token))
    return JSONResponse(
        status_code=202,
        content={"message": "Confirmation instructions have been sent to the new email address."},
    )


@router.delete("/me", status_code=204, response_class=Response)
async def delete_me(
    body: DeleteAccountIn,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
    manager: UserManager = Depends(get_user_manager),
) -> Response:
    verify_password(manager, user, body.password)
    user_id = user.id
    for model in (IdempotencyKey, OneTimeToken, AuthSession, Payment, Subscription, Category):
        await session.execute(delete(model).where(model.user_id == user_id))
    await session.delete(user)
    await session.flush()
    audit(session, None, "user", user_id, "account_deleted")
    await session.commit()
    return Response(status_code=204)
