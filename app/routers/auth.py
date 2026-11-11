import hmac
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.authentication import JWTStrategy
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import (
    UserCreate,
    UserManager,
    check_password,
    current_active_user,
    get_jwt_strategy,
    get_user_manager,
)
from app.config import get_settings
from app.db import get_session
from app.email import EmailSender, get_email_sender, link
from app.errors import AppError
from app.models import User
from app.ratelimit import rate_limit
from app.schemas.common import Message
from app.schemas.users import (
    EmailIn,
    RegisterIn,
    RegisterOut,
    ResetPasswordIn,
    TokenIn,
    TokenOut,
    VerifiedOut,
)
from app.services import auth as auth_service
from app.services.common import audit, new_token

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
COOKIE_PATH = "/api/v1/auth"


def email_exists() -> AppError:
    return AppError(409, "EMAIL_ALREADY_EXISTS", "Email is already in use")


def set_auth_cookies(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    max_age = settings.refresh_token_ttl_days * 86400
    common = {"max_age": max_age, "path": COOKIE_PATH, "secure": settings.cookie_secure, "samesite": "lax"}
    response.set_cookie(REFRESH_COOKIE, refresh_token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, new_token(), httponly=False, **common)


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path=COOKIE_PATH, secure=settings.cookie_secure, httponly=name == REFRESH_COOKIE, samesite="lax"
        )


def check_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise AppError(403, "CSRF_TOKEN_INVALID", "CSRF token is missing or invalid")


async def token_out(strategy: JWTStrategy, user: User) -> TokenOut:
    return TokenOut(
        access_token=await strategy.write_token(user),
        expires_in=get_settings().access_token_ttl_seconds,
    )


def client_info(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("User-Agent"), request.client.host if request.client else None


def accepted(message: str) -> JSONResponse:
    return JSONResponse(status_code=202, content={"message": message})


@router.post("/register", status_code=201, response_model=RegisterOut, dependencies=[Depends(rate_limit)])
async def register(
    body: RegisterIn,
    session: AsyncSession = Depends(get_session),
    manager: UserManager = Depends(get_user_manager),
    sender: EmailSender = Depends(get_email_sender),
) -> User:
    email = auth_service.normalize_email(body.email)
    await auth_service.release_expired_reservations(session)
    if await auth_service.email_taken(session, email):
        raise email_exists()
    try:
        user = await manager.create(UserCreate(email=email, password=body.password))
    except UserAlreadyExists:
        raise email_exists() from None
    except InvalidPasswordException as exc:
        raise AppError(
            422, "VALIDATION_ERROR", "Invalid request data", {"field": "password", "reason": exc.reason}
        ) from None
    except IntegrityError:
        await session.rollback()
        raise email_exists() from None
    token = await auth_service.issue_one_time_token(
        session, user.id, "verify_email", timedelta(hours=get_settings().verify_token_ttl_hours), user.email
    )
    await session.commit()
    await sender.send(user.email, "Confirm your email", link("verify-email", token))
    return user


@router.post("/jwt/login", response_model=TokenOut, dependencies=[Depends(rate_limit)])
async def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
    manager: UserManager = Depends(get_user_manager),
    strategy: JWTStrategy = Depends(get_jwt_strategy),
) -> TokenOut:
    form.username = auth_service.normalize_email(form.username)
    user = await manager.authenticate(form)
    if user is None or not user.is_active:
        raise AppError(400, "INVALID_CREDENTIALS", "Invalid credentials")
    if get_settings().require_email_verification and not user.is_verified:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Email is not verified")
    _, refresh_token = auth_service.create_session(session, user.id, None, *client_info(request))
    await session.commit()
    set_auth_cookies(response, refresh_token)
    return await token_out(strategy, user)


@router.post("/refresh", response_model=TokenOut, dependencies=[Depends(rate_limit)])
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    strategy: JWTStrategy = Depends(get_jwt_strategy),
) -> TokenOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise auth_service.invalid_refresh()
    check_csrf(request)
    user_id, refresh_token = await auth_service.rotate_session(session, token, *client_info(request))
    await session.commit()
    user = await session.get(User, user_id)
    set_auth_cookies(response, refresh_token)
    return await token_out(strategy, user)


@router.post("/jwt/logout", status_code=204, response_class=Response)
async def logout(
    request: Request,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        check_csrf(request)
        await auth_service.revoke_session(session, user.id, token)
        await session.commit()
    response = Response(status_code=204)
    clear_auth_cookies(response)
    return response


@router.post("/forgot-password", status_code=202, response_model=Message, dependencies=[Depends(rate_limit)])
async def forgot_password(
    body: EmailIn,
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> JSONResponse:
    user = await session.scalar(select(User).where(User.email == auth_service.normalize_email(body.email)))
    if user is not None and user.is_active:
        token = await auth_service.issue_one_time_token(
            session, user.id, "reset_password", timedelta(minutes=get_settings().reset_token_ttl_minutes)
        )
        await session.commit()
        await sender.send(user.email, "Reset your password", link("reset-password", token))
    return accepted("If the account exists, instructions have been sent.")


@router.post("/reset-password", response_model=Message, dependencies=[Depends(rate_limit)])
async def reset_password(
    body: ResetPasswordIn,
    session: AsyncSession = Depends(get_session),
    manager: UserManager = Depends(get_user_manager),
) -> Message:
    record = await auth_service.consume_one_time_token(session, body.token, "reset_password")
    user = await session.get(User, record.user_id, with_for_update=True)
    await check_password(manager, body.password, user)
    user.hashed_password = manager.password_helper.hash(body.password)
    await auth_service.revoke_all_sessions(session, user.id)
    audit(session, user.id, "user", user.id, "password_reset")
    await session.commit()
    return Message(message="Password has been reset.")


@router.post("/request-verify-token", status_code=202, response_model=Message, dependencies=[Depends(rate_limit)])
async def request_verify_token(
    body: EmailIn,
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> JSONResponse:
    user = await session.scalar(select(User).where(User.email == auth_service.normalize_email(body.email)))
    if user is not None and user.is_active and not user.is_verified:
        token = await auth_service.issue_one_time_token(
            session, user.id, "verify_email", timedelta(hours=get_settings().verify_token_ttl_hours), user.email
        )
        await session.commit()
        await sender.send(user.email, "Confirm your email", link("verify-email", token))
    return accepted("If verification is required, instructions have been sent.")


@router.post("/verify", response_model=VerifiedOut, dependencies=[Depends(rate_limit)])
async def verify(body: TokenIn, session: AsyncSession = Depends(get_session)) -> User:
    record = await auth_service.consume_one_time_token(session, body.token, "verify_email")
    user = await session.get(User, record.user_id, with_for_update=True)
    if user.email != record.email:
        raise auth_service.invalid_token()
    user.is_verified = True
    audit(session, user.id, "user", user.id, "email_verified")
    await session.commit()
    return user


@router.post("/confirm-email-change", response_model=VerifiedOut, dependencies=[Depends(rate_limit)])
async def confirm_email_change(body: TokenIn, session: AsyncSession = Depends(get_session)) -> User:
    record = await auth_service.consume_one_time_token(session, body.token, "change_email")
    user = await session.get(User, record.user_id, with_for_update=True)
    if user.pending_email != record.email:
        raise auth_service.invalid_token()
    if await session.scalar(select(User.id).where(User.email == record.email, User.id != user.id)):
        raise email_exists()
    user.email = record.email
    user.pending_email = None
    user.pending_email_reserved_until = None
    user.is_verified = True
    await auth_service.revoke_all_sessions(session, user.id)
    audit(session, user.id, "user", user.id, "email_changed")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise email_exists() from None
    return user
