import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(20, 8)
CURRENCY_CHECK = "{col} ~ '^[A-Z]{{3}}$'"


def in_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


INTERVAL_UNITS = ("day", "week", "month", "year")
SUBSCRIPTION_STATUSES = ("active", "paused", "cancelled")
PAYMENT_TYPES = ("charge", "refund")
PAYMENT_STATUSES = ("succeeded", "pending", "failed", "reversed")
PAYMENT_SOURCES = ("manual", "import", "provider")
TOKEN_TYPES = ("verify_email", "reset_password", "change_email")


class Base(DeclarativeBase):
    __mapper_args__ = {"eager_defaults": True}


def default_currency() -> str:
    from app.config import get_settings

    return get_settings().default_currency


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(SQLAlchemyBaseUserTableUUID, TimestampMixin, Base):
    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_user_email_normalized"),
        CheckConstraint(CURRENCY_CHECK.format(col="preferred_currency"), name="ck_user_currency"),
        Index(
            "uq_user_pending_email",
            text("lower(pending_email)"),
            unique=True,
            postgresql_where=text("pending_email IS NOT NULL"),
        ),
    )

    pending_email: Mapped[str | None] = mapped_column(String(320))
    pending_email_reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    preferred_currency: Mapped[str] = mapped_column(String(3), nullable=False, default=default_currency)


class Category(TimestampMixin, Base):
    __tablename__ = "category"
    __table_args__ = (
        CheckConstraint(
            "(is_system AND user_id IS NULL) OR (NOT is_system AND user_id IS NOT NULL)",
            name="ck_category_owner",
        ),
        CheckConstraint("color ~ '^#[0-9A-F]{6}$'", name="ck_category_color"),
        UniqueConstraint("id", "user_id", name="uq_category_id_user"),
        Index(
            "uq_category_user_name",
            "user_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("NOT is_system"),
        ),
        Index(
            "uq_category_system_name",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("is_system"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscription"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_subscription_amount"),
        CheckConstraint("interval_count BETWEEN 1 AND 1200", name="ck_subscription_interval"),
        CheckConstraint(CURRENCY_CHECK.format(col="currency"), name="ck_subscription_currency"),
        CheckConstraint(in_check("billing_interval_unit", INTERVAL_UNITS), name="ck_subscription_unit"),
        CheckConstraint(in_check("status", SUBSCRIPTION_STATUSES), name="ck_subscription_status"),
        UniqueConstraint("id", "user_id", name="uq_subscription_id_user"),
        Index("ix_subscription_user_next", "user_id", "next_payment_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("category.id"), index=True)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    billing_interval_unit: Mapped[str] = mapped_column(String(5), nullable=False)
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    next_payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    schedule_anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(9), nullable=False, default="active")
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Payment(TimestampMixin, Base):
    __tablename__ = "payment"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_amount"),
        CheckConstraint(CURRENCY_CHECK.format(col="currency"), name="ck_payment_currency"),
        CheckConstraint(in_check("payment_type", PAYMENT_TYPES), name="ck_payment_type"),
        CheckConstraint(in_check("status", PAYMENT_STATUSES), name="ck_payment_status"),
        CheckConstraint(in_check("source", PAYMENT_SOURCES), name="ck_payment_source"),
        CheckConstraint(
            "(payment_type = 'refund') = (original_payment_id IS NOT NULL)",
            name="ck_payment_refund_link",
        ),
        UniqueConstraint("id", "user_id", name="uq_payment_id_user"),
        ForeignKeyConstraint(
            ["subscription_id", "user_id"],
            ["subscription.id", "subscription.user_id"],
            name="fk_payment_subscription_owner",
        ),
        ForeignKeyConstraint(
            ["original_payment_id", "user_id"],
            ["payment.id", "payment.user_id"],
            name="fk_payment_original_owner",
        ),
        Index(
            "uq_payment_billing_period",
            "subscription_id",
            "billing_period_start",
            unique=True,
            postgresql_where=text(
                "payment_type = 'charge' AND status IN ('pending', 'succeeded') "
                "AND subscription_id IS NOT NULL AND billing_period_start IS NOT NULL"
            ),
        ),
        Index("ix_payment_user_occurred", "user_id", "occurred_at"),
        Index("ix_payment_original", "original_payment_id"),
        Index("ix_payment_subscription", "subscription_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    original_payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("category.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_type: Mapped[str] = mapped_column(String(6), nullable=False)
    status: Mapped[str] = mapped_column(String(9), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_start: Mapped[date | None] = mapped_column(Date)
    service_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    category_snapshot: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(8), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth_session.id"))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(45))


class OneTimeToken(Base):
    __tablename__ = "one_time_token"
    __table_args__ = (CheckConstraint(in_check("token_type", TOKEN_TYPES), name="ck_token_type"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_type: Mapped[str] = mapped_column(String(14), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_key"
    __table_args__ = (UniqueConstraint("user_id", "operation", "key", name="uq_idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36))
