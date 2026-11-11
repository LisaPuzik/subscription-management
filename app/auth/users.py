import uuid
from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.errors import AppError
from app.models import User

MIN_PASSWORD = 12
MAX_PASSWORD = 128


class UserCreate(schemas.BaseUserCreate):
    pass


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def validate_password(self, password: str, user) -> None:
        if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            raise InvalidPasswordException(f"Password length must be between {MIN_PASSWORD} and {MAX_PASSWORD}")
        if password.strip().lower() == user.email.strip().lower():
            raise InvalidPasswordException("Password must not match email")


async def get_user_db(session: AsyncSession = Depends(get_session)) -> AsyncIterator:
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(user_db=Depends(get_user_db)) -> AsyncIterator[UserManager]:
    manager = UserManager(user_db)
    secret = get_settings().jwt_secret.get_secret_value()
    manager.reset_password_token_secret = secret
    manager.verification_token_secret = secret
    yield manager


def get_jwt_strategy() -> JWTStrategy:
    settings = get_settings()
    return JWTStrategy(
        secret=settings.jwt_secret.get_secret_value(),
        lifetime_seconds=settings.access_token_ttl_seconds,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=BearerTransport(tokenUrl="api/v1/auth/jwt/login"),
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)


async def current_verified_user(user: User = Depends(current_active_user)) -> User:
    if get_settings().require_email_verification and not user.is_verified:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Email is not verified")
    return user


async def check_password(manager: UserManager, password: str, policy_user) -> None:
    try:
        await manager.validate_password(password, policy_user)
    except InvalidPasswordException as exc:
        raise AppError(
            422, "VALIDATION_ERROR", "Invalid request data", {"field": "password", "reason": exc.reason}
        ) from None
