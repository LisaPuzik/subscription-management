import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.currency import amount_error, money
from app.domain import schedule
from app.errors import AppError, not_found, validation_error
from app.models import Payment, Subscription, User
from app.schemas.domain import PaymentIn, PaymentOut, RefundIn
from app.services import categories
from app.services.common import audit, snapshot, utcnow
from app.services.subscriptions import archived_error, get_owned

CLOCK_SKEW = timedelta(minutes=5)
ACTIVE_STATUSES = ("succeeded", "pending")
FIELDS = ["amount", "currency", "payment_type", "status", "occurred_at", "subscription_id", "category_id"]


def check_amount(amount, currency: str) -> None:
    error = amount_error(amount, currency)
    if error:
        raise validation_error("amount", error)


def check_not_future(occurred_at: datetime) -> None:
    if occurred_at > utcnow() + CLOCK_SKEW:
        raise validation_error("occurred_at", "Must not be in the future")


def to_out(payment: Payment) -> PaymentOut:
    data = {f: getattr(payment, f) for f in PaymentOut.model_fields}
    data["amount"] = money(payment.amount, payment.currency)
    return PaymentOut(**data)


def duplicate_period() -> AppError:
    return AppError(
        409, "DUPLICATE_BILLING_PERIOD", "Billing period is already paid", {"field": "billing_period_start"}
    )


def check_billing_period(subscription: Subscription, body: PaymentIn) -> None:
    period = body.billing_period_start
    if period is None:
        raise validation_error("billing_period_start", "Required for subscription payments")
    if body.currency != subscription.currency:
        raise AppError(422, "CURRENCY_MISMATCH", "Currency does not match subscription", {"field": "currency"})
    if period < subscription.start_date:
        raise validation_error("billing_period_start", "Must not be earlier than start_date")
    if period > subscription.next_payment_date:
        raise AppError(
            409,
            "FUTURE_BILLING_PERIOD_NOT_ALLOWED",
            "Billing period is later than next payment date",
            {"field": "billing_period_start"},
        )
    anchor = subscription.schedule_anchor_date
    unit, count = subscription.billing_interval_unit, subscription.interval_count
    if period >= anchor and not schedule.is_occurrence(anchor, unit, count, period):
        raise validation_error("billing_period_start", "Date is not in the subscription schedule")


async def create_charge(session: AsyncSession, user: User, body: PaymentIn) -> Payment:
    check_amount(body.amount, body.currency)
    check_not_future(body.occurred_at)
    category = None
    if body.category_id is not None:
        category = await categories.assignable(session, user, body.category_id)

    subscription = None
    if body.subscription_id is not None:
        if body.service_name is not None:
            raise validation_error("service_name", "Not allowed for subscription payments")
        subscription = await get_owned(session, user, body.subscription_id, lock=True)
        if subscription.is_archived:
            raise archived_error()
        if subscription.status == "cancelled":
            raise AppError(409, "SUBSCRIPTION_NOT_ACTIVE", "Subscription is cancelled")
        check_billing_period(subscription, body)
        exists = await session.scalar(
            select(Payment.id).where(
                Payment.subscription_id == subscription.id,
                Payment.billing_period_start == body.billing_period_start,
                Payment.payment_type == "charge",
                Payment.status.in_(ACTIVE_STATUSES),
            )
        )
        if exists:
            raise duplicate_period()
        if category is None and subscription.category_id is not None:
            category = await categories.get_visible(session, user, subscription.category_id)
        service_name = subscription.service_name
    else:
        if body.service_name is None:
            raise validation_error("service_name", "Required for one-time payments")
        if body.billing_period_start is not None:
            raise validation_error("billing_period_start", "Allowed only for subscription payments")
        service_name = body.service_name

    payment = Payment(
        user_id=user.id,
        subscription_id=body.subscription_id,
        category_id=category.id if category else None,
        amount=body.amount,
        currency=body.currency,
        payment_type="charge",
        status="succeeded",
        occurred_at=body.occurred_at,
        billing_period_start=body.billing_period_start,
        service_name_snapshot=service_name,
        category_snapshot=category.name if category else None,
        source="manual",
    )
    session.add(payment)
    if subscription is not None and body.billing_period_start == subscription.next_payment_date:
        old_next = subscription.next_payment_date
        subscription.next_payment_date = schedule.first_after(
            subscription.schedule_anchor_date,
            subscription.billing_interval_unit,
            subscription.interval_count,
            old_next,
        )
        audit(
            session,
            user.id,
            "subscription",
            subscription.id,
            "schedule_advanced",
            {"next_payment_date": old_next},
            {"next_payment_date": subscription.next_payment_date},
        )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise duplicate_period() from None
    audit(session, user.id, "payment", payment.id, "created", None, snapshot(payment, FIELDS))
    return payment


async def get_owned_payment(session: AsyncSession, user: User, payment_id: uuid.UUID, lock: bool = False) -> Payment:
    query = select(Payment).where(Payment.id == payment_id, Payment.user_id == user.id)
    if lock:
        query = query.with_for_update()
    payment = await session.scalar(query)
    if payment is None:
        raise not_found()
    return payment


async def refunded_total(session: AsyncSession, payment_id: uuid.UUID):
    return await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.original_payment_id == payment_id, Payment.status.in_(ACTIVE_STATUSES)
        )
    )


async def create_refund(session: AsyncSession, user: User, payment_id: uuid.UUID, body: RefundIn) -> Payment:
    original = await get_owned_payment(session, user, payment_id, lock=True)
    if original.payment_type != "charge" or original.status != "succeeded":
        raise AppError(409, "REFUND_NOT_ALLOWED", "Refund is not allowed for this payment")
    check_amount(body.amount, original.currency)
    check_not_future(body.occurred_at)
    if body.occurred_at < original.occurred_at:
        raise validation_error("occurred_at", "Must not be earlier than the original payment")
    available = original.amount - await refunded_total(session, original.id)
    if body.amount > available:
        raise AppError(
            409,
            "REFUND_LIMIT_EXCEEDED",
            "Refund exceeds the remaining amount",
            {"available": money(available, original.currency)},
        )
    refund = Payment(
        user_id=user.id,
        subscription_id=original.subscription_id,
        original_payment_id=original.id,
        category_id=original.category_id,
        amount=body.amount,
        currency=original.currency,
        payment_type="refund",
        status="succeeded",
        occurred_at=body.occurred_at,
        billing_period_start=original.billing_period_start,
        service_name_snapshot=original.service_name_snapshot,
        category_snapshot=original.category_snapshot,
        source="manual",
    )
    session.add(refund)
    await session.flush()
    audit(session, user.id, "payment", refund.id, "refund_created", None, snapshot(refund, FIELDS))
    return refund


async def reverse(session: AsyncSession, user: User, payment_id: uuid.UUID, reason: str) -> Payment:
    payment = await get_owned_payment(session, user, payment_id, lock=True)
    if payment.status == "reversed":
        raise AppError(409, "PAYMENT_ALREADY_REVERSED", "Payment is already reversed")
    if payment.payment_type != "charge" or payment.status != "succeeded":
        raise AppError(409, "REVERSAL_NOT_ALLOWED", "Payment cannot be reversed")
    if await refunded_total(session, payment.id) > 0:
        raise AppError(409, "REVERSAL_NOT_ALLOWED", "Payment has refunds")
    payment.status = "reversed"
    audit(
        session,
        user.id,
        "payment",
        payment.id,
        "reversed",
        {"status": "succeeded"},
        {"status": "reversed", "reason": reason},
    )
    return payment
