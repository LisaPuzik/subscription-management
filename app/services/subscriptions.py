import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.currency import amount_error, money
from app.domain import schedule
from app.errors import AppError, not_found, validation_error
from app.models import Category, Subscription, User
from app.schemas.domain import CategoryRef, SubscriptionIn, SubscriptionOut, SubscriptionPatch
from app.services import categories
from app.services.common import audit, snapshot, user_today

FIELDS = [
    "service_name",
    "description",
    "amount",
    "currency",
    "billing_interval_unit",
    "interval_count",
    "next_payment_date",
    "schedule_anchor_date",
    "category_id",
    "status",
    "is_archived",
]


def archived_error() -> AppError:
    return AppError(409, "SUBSCRIPTION_ARCHIVED", "Subscription is archived")


def check_amount(amount, currency: str) -> None:
    error = amount_error(amount, currency)
    if error:
        raise validation_error("amount", error)


async def get_owned(session: AsyncSession, user: User, subscription_id: uuid.UUID, lock: bool = False) -> Subscription:
    query = select(Subscription).where(Subscription.id == subscription_id, Subscription.user_id == user.id)
    if lock:
        query = query.with_for_update()
    subscription = await session.scalar(query)
    if subscription is None:
        raise not_found()
    return subscription


async def to_out(session: AsyncSession, subscription: Subscription) -> SubscriptionOut:
    category = None
    if subscription.category_id is not None:
        found = await session.get(Category, subscription.category_id)
        category = CategoryRef(id=found.id, name=found.name)
    data = {f: getattr(subscription, f) for f in SubscriptionOut.model_fields if f != "category"}
    data["amount"] = money(subscription.amount, subscription.currency)
    return SubscriptionOut(**data, category=category)


def next_date(subscription: Subscription, user: User):
    target = user_today(user)
    if subscription.next_payment_date is not None:
        target = max(target, subscription.next_payment_date)
    return schedule.first_on_or_after(
        subscription.schedule_anchor_date,
        subscription.billing_interval_unit,
        subscription.interval_count,
        target,
    )


async def create(session: AsyncSession, user: User, body: SubscriptionIn) -> Subscription:
    check_amount(body.amount, body.currency)
    if body.category_id is not None:
        await categories.assignable(session, user, body.category_id)
    subscription = Subscription(
        user_id=user.id,
        status="active",
        is_archived=False,
        schedule_anchor_date=body.start_date,
        **body.model_dump(),
    )
    subscription.next_payment_date = next_date(subscription, user)
    session.add(subscription)
    await session.flush()
    audit(session, user.id, "subscription", subscription.id, "created", None, snapshot(subscription, FIELDS))
    return subscription


async def update(session: AsyncSession, user: User, subscription: Subscription, body: SubscriptionPatch) -> None:
    if subscription.is_archived:
        raise archived_error()
    changes = body.changes()
    old = snapshot(subscription, FIELDS)

    new_status = changes.pop("status", subscription.status)
    if not schedule.can_change_status(subscription.status, new_status):
        raise AppError(
            409,
            "INVALID_STATUS_TRANSITION",
            f"Status cannot change from {subscription.status} to {new_status}",
            {"field": "status"},
        )
    if changes.get("category_id") is not None:
        await categories.assignable(session, user, changes["category_id"])

    currency = changes.get("currency", subscription.currency)
    amount = changes.get("amount", subscription.amount)
    if "amount" in changes or "currency" in changes:
        check_amount(amount, currency)

    interval_changed = any(
        k in changes and changes[k] != getattr(subscription, k) for k in ("billing_interval_unit", "interval_count")
    )
    new_next = changes.pop("next_payment_date", None)
    if new_next is not None and new_next < subscription.start_date:
        raise validation_error("next_payment_date", "Must not be earlier than start_date")

    for key, value in changes.items():
        setattr(subscription, key, value)
    if new_next is not None:
        subscription.schedule_anchor_date = new_next
        subscription.next_payment_date = new_next
    elif interval_changed:
        subscription.schedule_anchor_date = subscription.next_payment_date

    if subscription.status == "paused" and new_status == "active":
        subscription.next_payment_date = next_date(subscription, user)
    subscription.status = new_status

    audit(session, user.id, "subscription", subscription.id, "updated", old, snapshot(subscription, FIELDS))


async def archive(session: AsyncSession, user: User, subscription: Subscription) -> None:
    if subscription.is_archived:
        return
    subscription.is_archived = True
    audit(session, user.id, "subscription", subscription.id, "archived", {"is_archived": False}, {"is_archived": True})


async def restore(session: AsyncSession, user: User, subscription: Subscription) -> None:
    if not subscription.is_archived:
        return
    subscription.is_archived = False
    if subscription.status == "active":
        subscription.next_payment_date = next_date(subscription, user)
    audit(session, user.id, "subscription", subscription.id, "restored", {"is_archived": True}, {"is_archived": False})
