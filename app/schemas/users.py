import uuid
import zoneinfo
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field

from app.schemas.common import Currency, PatchModel, StrictModel, UtcDateTime

TIMEZONES = zoneinfo.available_timezones()


def _check_timezone(value: str) -> str:
    if value not in TIMEZONES:
        raise ValueError("Unknown IANA timezone")
    return value


Timezone = Annotated[str, AfterValidator(_check_timezone)]
Password = Annotated[str, Field(min_length=1, max_length=1024)]
Token = Annotated[str, Field(min_length=1, max_length=256)]


class RegisterIn(StrictModel):
    email: EmailStr
    password: Password


class RegisterOut(BaseModel):
    id: uuid.UUID
    email: str
    is_active: bool
    is_verified: bool
    created_at: UtcDateTime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class EmailIn(StrictModel):
    email: EmailStr


class ResetPasswordIn(StrictModel):
    token: Token
    password: Password


class TokenIn(StrictModel):
    token: Token


class VerifiedOut(BaseModel):
    id: uuid.UUID
    email: str
    is_verified: bool


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    timezone: str
    preferred_currency: str
    is_active: bool
    is_verified: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class UserPatch(PatchModel):
    NOT_NULL: ClassVar[set[str]] = {"timezone", "preferred_currency"}

    timezone: Timezone | None = None
    preferred_currency: Currency | None = None


class PasswordChangeIn(StrictModel):
    current_password: Password
    new_password: Password


class EmailChangeIn(StrictModel):
    new_email: EmailStr
    current_password: Password


class DeleteAccountIn(StrictModel):
    password: Password
    confirmation: Literal["DELETE"]
