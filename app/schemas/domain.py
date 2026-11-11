import uuid
from datetime import date
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field

from app.schemas.common import Currency, Money, PatchModel, StrictModel, UtcDateTime

IntervalUnit = Literal["day", "week", "month", "year"]
SubscriptionStatus = Literal["active", "paused", "cancelled"]
ServiceName = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2000)]
IntervalCount = Annotated[int, Field(ge=1, le=1200)]
CategoryName = Annotated[str, Field(min_length=1, max_length=100)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$"), AfterValidator(str.upper)]


class CategoryIn(StrictModel):
    name: CategoryName
    description: Description | None = None
    color: Color = "#9E9E9E"


class CategoryPatch(PatchModel):
    NOT_NULL: ClassVar[set[str]] = {"name", "color", "is_active"}

    name: CategoryName | None = None
    description: Description | None = None
    color: Color | None = None
    is_active: bool | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    description: str | None
    color: str
    is_system: bool
    is_active: bool
    created_at: UtcDateTime


class SubscriptionIn(StrictModel):
    service_name: ServiceName
    description: Description | None = None
    amount: Money
    currency: Currency
    billing_interval_unit: IntervalUnit
    interval_count: IntervalCount
    start_date: date
    category_id: uuid.UUID | None = None


class SubscriptionPatch(PatchModel):
    NOT_NULL: ClassVar[set[str]] = {
        "service_name",
        "amount",
        "currency",
        "billing_interval_unit",
        "interval_count",
        "next_payment_date",
        "status",
    }

    service_name: ServiceName | None = None
    description: Description | None = None
    amount: Money | None = None
    currency: Currency | None = None
    billing_interval_unit: IntervalUnit | None = None
    interval_count: IntervalCount | None = None
    next_payment_date: date | None = None
    category_id: uuid.UUID | None = None
    status: SubscriptionStatus | None = None


class CategoryRef(BaseModel):
    id: uuid.UUID
    name: str


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    service_name: str
    description: str | None
    amount: str
    currency: str
    billing_interval_unit: str
    interval_count: int
    start_date: date
    schedule_anchor_date: date
    next_payment_date: date
    category: CategoryRef | None
    status: str
    is_archived: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class SubscriptionListItem(BaseModel):
    id: uuid.UUID
    service_name: str
    amount: str
    currency: str
    billing_interval_unit: str
    interval_count: int
    category_id: uuid.UUID | None
    category_name: str | None
    next_payment_date: date
    status: str
    is_archived: bool


class PaymentIn(StrictModel):
    subscription_id: uuid.UUID | None = None
    service_name: ServiceName | None = None
    amount: Money
    currency: Currency
    occurred_at: AwareDatetime
    billing_period_start: date | None = None
    category_id: uuid.UUID | None = None


class RefundIn(StrictModel):
    amount: Money
    occurred_at: AwareDatetime


class ReverseIn(StrictModel):
    reason: Annotated[str, Field(min_length=10, max_length=1000)]


class PaymentOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    subscription_id: uuid.UUID | None
    original_payment_id: uuid.UUID | None
    amount: str
    currency: str
    payment_type: str
    status: str
    occurred_at: UtcDateTime
    billing_period_start: date | None
    category_id: uuid.UUID | None
    service_name_snapshot: str
    category_snapshot: str | None
    source: str
    created_at: UtcDateTime


class RefundBrief(BaseModel):
    id: uuid.UUID
    amount: str
    currency: str
    status: str
    occurred_at: UtcDateTime


class PaymentDetail(PaymentOut):
    refunds: list[RefundBrief] | None = None


class PaymentListItem(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID | None
    service_name: str
    amount: str
    currency: str
    payment_type: str
    status: str
    occurred_at: UtcDateTime
    billing_period_start: date | None
    category_id: uuid.UUID | None
    category_name: str | None
    original_payment_id: uuid.UUID | None
    source: str


class ReverseOut(BaseModel):
    id: uuid.UUID
    status: str
    reversed_at: UtcDateTime
    reason: str
