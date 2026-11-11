import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import Category, Payment, Subscription, User


async def make_db_user(session, email: str):
    user = User(email=email, hashed_password="x", is_active=True, is_superuser=False, is_verified=True)
    session.add(user)
    await session.flush()
    return user.id


def subscription(user_id, **extra) -> Subscription:
    values = {
        "user_id": user_id,
        "service_name": "S",
        "amount": Decimal("1"),
        "currency": "EUR",
        "billing_interval_unit": "month",
        "interval_count": 1,
        "start_date": date(2026, 1, 1),
        "next_payment_date": date(2026, 1, 1),
        "schedule_anchor_date": date(2026, 1, 1),
        **extra,
    }
    return Subscription(**values)


def payment(user_id, **extra) -> Payment:
    values = {
        "user_id": user_id,
        "amount": Decimal("1"),
        "currency": "EUR",
        "payment_type": "charge",
        "status": "succeeded",
        "occurred_at": datetime.now(UTC),
        "service_name_snapshot": "S",
        "source": "manual",
        **extra,
    }
    return Payment(**values)


async def test_unique_email():
    async with get_sessionmaker()() as session:
        await make_db_user(session, "same@example.com")
        await session.commit()
        session.add(
            User(email="same@example.com", hashed_password="x", is_active=True, is_superuser=False, is_verified=False)
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            User(email="Upper@example.com", hashed_password="x", is_active=True, is_superuser=False, is_verified=False)
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_foreign_category_blocked_by_trigger():
    async with get_sessionmaker()() as session:
        alice = await make_db_user(session, "alice-db@example.com")
        bob = await make_db_user(session, "bob-db@example.com")
        category = Category(id=uuid.uuid4(), user_id=alice, name="Alice", color="#000000", is_system=False)
        category_id = category.id
        session.add(category)
        await session.commit()
        session.add(subscription(bob, category_id=category_id))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(payment(bob, category_id=category_id))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        system = await session.scalar(select(Category.id).where(Category.is_system.is_(True)).limit(1))
        session.add(subscription(bob, category_id=system))
        await session.commit()


async def test_payment_subscription_owner_and_period_uniqueness():
    async with get_sessionmaker()() as session:
        alice = await make_db_user(session, "alice-p@example.com")
        bob = await make_db_user(session, "bob-p@example.com")
        sub = subscription(alice, id=uuid.uuid4())
        sub_id = sub.id
        session.add(sub)
        await session.commit()

        session.add(payment(bob, subscription_id=sub_id))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        period = {"subscription_id": sub_id, "billing_period_start": date(2026, 1, 1)}
        session.add(payment(alice, **period))
        await session.commit()
        session.add(payment(alice, **period, status="failed"))
        await session.commit()
        session.add(payment(alice, **period))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_check_constraints():
    async with get_sessionmaker()() as session:
        user = await make_db_user(session, "checks@example.com")
        await session.commit()
        bad = [
            subscription(user, amount=Decimal("0")),
            subscription(user, interval_count=0),
            subscription(user, currency="eur"),
            subscription(user, status="deleted"),
            payment(user, amount=Decimal("-1")),
            payment(user, payment_type="refund"),
            Category(user_id=None, name="No owner", color="#000000", is_system=False),
            Category(user_id=user, name="Bad color", color="red", is_system=False),
        ]
        for obj in bad:
            session.add(obj)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
        session.add(Category(user_id=user, name="Dup", color="#000000", is_system=False))
        await session.commit()
        session.add(Category(user_id=user, name="DUP", color="#000000", is_system=False, is_active=False))
        with pytest.raises(IntegrityError):
            await session.commit()
