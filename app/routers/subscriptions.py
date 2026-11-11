import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_verified_user
from app.currency import money
from app.db import get_session
from app.models import Category, Subscription, User
from app.schemas.common import LimitQuery, OffsetQuery, Page
from app.schemas.domain import (
    SubscriptionIn,
    SubscriptionListItem,
    SubscriptionOut,
    SubscriptionPatch,
    SubscriptionStatus,
)
from app.services import subscriptions as service
from app.services.idempotency import idempotency_key, request_hash, run_idempotent
from app.services.listing import ordering, page, paginate

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

SORT_COLUMNS = {
    "service_name": func.lower(Subscription.service_name),
    "amount": Subscription.amount,
    "next_payment_date": Subscription.next_payment_date,
    "created_at": Subscription.created_at,
}


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.post("", status_code=201, response_model=SubscriptionOut)
async def create_subscription(
    body: SubscriptionIn,
    key: str = Depends(idempotency_key),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    async def handler():
        subscription = await service.create(session, user, body)
        return subscription.id, await service.to_out(session, subscription)

    return await run_idempotent(session, user, key, "POST /api/v1/subscriptions", request_hash(body, {}), 201, handler)


@router.get("", response_model=Page[SubscriptionListItem])
async def list_subscriptions(
    status: SubscriptionStatus | None = None,
    category_id: uuid.UUID | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    search: str | None = None,
    include_archived: bool = False,
    sort_by: Literal["service_name", "amount", "next_payment_date", "created_at"] = "next_payment_date",
    sort_order: Literal["asc", "desc"] = "asc",
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = (
        select(Subscription, Category.name)
        .outerjoin(Category, Category.id == Subscription.category_id)
        .where(Subscription.user_id == user.id)
    )
    if not include_archived:
        query = query.where(Subscription.is_archived.is_(False))
    if status:
        query = query.where(Subscription.status == status)
    if category_id:
        query = query.where(Subscription.category_id == category_id)
    if due_from:
        query = query.where(Subscription.next_payment_date >= due_from)
    if due_to:
        query = query.where(Subscription.next_payment_date < due_to)
    if search and search.strip():
        query = query.where(Subscription.service_name.ilike(f"%{escape_like(search.strip())}%", escape="\\"))
    order = ordering(SORT_COLUMNS[sort_by], sort_order, Subscription.id)
    rows, total = await paginate(session, query, order, limit, offset)
    items = [
        SubscriptionListItem(
            id=s.id,
            service_name=s.service_name,
            amount=money(s.amount, s.currency),
            currency=s.currency,
            billing_interval_unit=s.billing_interval_unit,
            interval_count=s.interval_count,
            category_id=s.category_id,
            category_name=name,
            next_payment_date=s.next_payment_date,
            status=s.status,
            is_archived=s.is_archived,
        )
        for s, name in rows
    ]
    return page(items, total, limit, offset)


@router.get("/{subscription_id}", response_model=SubscriptionOut)
async def get_subscription(
    subscription_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionOut:
    return await service.to_out(session, await service.get_owned(session, user, subscription_id))


@router.patch("/{subscription_id}", response_model=SubscriptionOut)
async def patch_subscription(
    subscription_id: uuid.UUID,
    body: SubscriptionPatch,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionOut:
    subscription = await service.get_owned(session, user, subscription_id, lock=True)
    await service.update(session, user, subscription, body)
    await session.commit()
    return await service.to_out(session, subscription)


@router.delete("/{subscription_id}", status_code=204, response_class=Response)
async def archive_subscription(
    subscription_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    subscription = await service.get_owned(session, user, subscription_id, lock=True)
    await service.archive(session, user, subscription)
    await session.commit()
    return Response(status_code=204)


@router.post("/{subscription_id}/restore", response_model=SubscriptionOut)
async def restore_subscription(
    subscription_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionOut:
    subscription = await service.get_owned(session, user, subscription_id, lock=True)
    await service.restore(session, user, subscription)
    await session.commit()
    return await service.to_out(session, subscription)
