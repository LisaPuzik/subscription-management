from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, ClassVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from app.currency import SUPPORTED_CURRENCIES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PatchModel(StrictModel):
    NOT_NULL: ClassVar[set[str]] = set()

    @model_validator(mode="after")
    def check_fields(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        for name in self.model_fields_set & self.NOT_NULL:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict:
        return {name: getattr(self, name) for name in self.model_fields_set}


def _parse_money(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Amount must be a string")
    try:
        result = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError("Amount must be a decimal number") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("Amount must be positive")
    return result


def _check_currency(value: str) -> str:
    if value not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency")
    return value


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


Money = Annotated[Decimal, BeforeValidator(_parse_money)]
Currency = Annotated[str, AfterValidator(_check_currency)]
UtcDateTime = Annotated[datetime, PlainSerializer(_utc, return_type=str)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class Pagination(BaseModel):
    total: int
    limit: int
    offset: int


class Page[T](BaseModel):
    items: list[T]
    pagination: Pagination


class Message(BaseModel):
    message: str


LimitQuery = Annotated[int, Field(ge=1, le=100)]
OffsetQuery = Annotated[int, Field(ge=0)]
