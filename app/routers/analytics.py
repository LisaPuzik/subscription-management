import re
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import DateTime, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_verified_user
from app.db import get_session
from app.domain import analytics
from app.domain.schedule import add_months
from app.errors import validation_error
from app.models import Category, Payment, Subscription, User
from app.schemas.common import Currency
from app.services.common import local_start, user_today

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_MONTHS = 600


class Period:
    def __init__(self, date_from: date = Query(...), date_to: date = Query(...), currency: Currency | None = None):
        if date_from >= date_to:
            raise validation_error("date_to", "date_to must be later than date_from")
        self.date_from = date_from
        self.date_to = date_to
        self.currency = currency


def actual_query(user: User, period: Period, *columns):
    query = select(*columns, Payment.payment_type, func.sum(Payment.amount), func.count()).where(
        Payment.user_id == user.id,
        Payment.status == "succeeded",
        Payment.occurred_at >= local_start(period.date_from, user.timezone),
        Payment.occurred_at < local_start(period.date_to, user.timezone),
    )
    if period.currency:
        query = query.where(Payment.currency == period.currency)
    return query.group_by(*columns, Payment.payment_type)


def period_out(user: User, period: Period) -> dict:
    return {"date_from": period.date_from, "date_to": period.date_to, "timezone": user.timezone}


@router.get("/summary")
async def summary(
    period: Period = Depends(),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(actual_query(user, period, Payment.currency))
    rows = [analytics.Row(currency=c, payment_type=t, total=s, count=n) for c, t, s, n in result]
    return {"period": period_out(user, period), "totals_by_currency": analytics.summary(rows)}


@router.get("/monthly")
async def monthly(
    period: Period = Depends(),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    months = analytics.month_keys(period.date_from, period.date_to)
    if len(months) > MAX_MONTHS:
        raise validation_error("date_to", f"Period must not exceed {MAX_MONTHS} months")
    starts = [local_start(date.fromisoformat(f"{m}-01"), user.timezone) for m in months]
    bucket = func.width_bucket(
        Payment.occurred_at, bindparam("month_starts", starts, type_=ARRAY(DateTime(timezone=True)))
    )
    result = await session.execute(actual_query(user, period, Payment.currency, bucket))
    rows = [
        analytics.Row(currency=c, month=months[b - 1], payment_type=t, total=s, count=n) for c, b, t, s, n in result
    ]
    currencies = {period.currency} if period.currency else set()
    return {"period": period_out(user, period), "items": analytics.monthly(rows, months, currencies)}


@router.get("/categories")
async def by_category(
    period: Period = Depends(),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = actual_query(user, period, Payment.currency, Payment.category_id, Payment.category_snapshot)
    rows = [
        analytics.Row(currency=c, category_id=i, category_name=name, payment_type=t, total=s, count=n)
        for c, i, name, t, s, n in await session.execute(query)
    ]
    return {"period": period_out(user, period), "items": analytics.by_category(rows)}


@router.get("/upcoming")
async def upcoming(
    month: str | None = Query(None, description="YYYY-MM"),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    today = user_today(user)
    current_month = today.replace(day=1)
    if month is None:
        month_start = add_months(current_month, 1)
    else:
        match = re.fullmatch(r"(\d{4})-(\d{2})", month)
        if not match or not 1 <= int(match[2]) <= 12:
            raise validation_error("month", "Expected format YYYY-MM")
        month_start = date(int(match[1]), int(match[2]), 1)
        if month_start < current_month:
            raise validation_error("month", "Month must not be in the past")
    month_end = add_months(month_start, 1)

    result = await session.execute(
        select(Subscription, Category.name)
        .outerjoin(Category, Category.id == Subscription.category_id)
        .where(
            Subscription.user_id == user.id,
            Subscription.status == "active",
            Subscription.is_archived.is_(False),
            Subscription.next_payment_date < month_end,
        )
    )
    planned = [
        analytics.PlannedSubscription(
            id=s.id,
            service_name=s.service_name,
            amount=s.amount,
            currency=s.currency,
            unit=s.billing_interval_unit,
            count=s.interval_count,
            anchor=s.schedule_anchor_date,
            next_payment_date=s.next_payment_date,
            category_id=s.category_id,
            category_name=name,
        )
        for s, name in result
    ]
    paid = set()
    if planned:
        paid_rows = await session.execute(
            select(Payment.subscription_id, Payment.billing_period_start).where(
                Payment.subscription_id.in_([p.id for p in planned]),
                Payment.payment_type == "charge",
                Payment.status.in_(("succeeded", "pending")),
                Payment.billing_period_start >= month_start,
                Payment.billing_period_start < month_end,
            )
        )
        paid = {(sid, day) for sid, day in paid_rows}
    totals, items = analytics.forecast(planned, month_start, today, paid)
    return {
        "month": f"{month_start.year:04d}-{month_start.month:02d}",
        "timezone": user.timezone,
        "totals_by_currency": totals,
        "items": items,
    }
