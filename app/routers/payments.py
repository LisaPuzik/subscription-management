import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_verified_user
from app.currency import money
from app.db import get_session
from app.errors import validation_error
from app.models import Payment, User
from app.schemas.common import Currency, LimitQuery, OffsetQuery, Page
from app.schemas.domain import (
    PaymentDetail,
    PaymentIn,
    PaymentListItem,
    PaymentOut,
    RefundBrief,
    RefundIn,
    ReverseIn,
    ReverseOut,
)
from app.services import payments as service
from app.services.common import local_start, utcnow
from app.services.idempotency import idempotency_key, request_hash, run_idempotent
from app.services.listing import ordering, page, paginate

router = APIRouter(prefix="/payments", tags=["payments"])

SORT_COLUMNS = {
    "occurred_at": Payment.occurred_at,
    "amount": Payment.amount,
    "created_at": Payment.created_at,
}


@router.post("", status_code=201, response_model=PaymentOut)
async def create_payment(
    body: PaymentIn,
    key: str = Depends(idempotency_key),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    async def handler():
        payment = await service.create_charge(session, user, body)
        return payment.id, service.to_out(payment)

    return await run_idempotent(session, user, key, "POST /api/v1/payments", request_hash(body, {}), 201, handler)


@router.get("", response_model=Page[PaymentListItem])
async def list_payments(
    date_from: date | None = None,
    date_to: date | None = None,
    subscription_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    currency: Currency | None = None,
    payment_type: Literal["charge", "refund"] | None = None,
    status: Literal["succeeded", "pending", "failed", "reversed"] | None = None,
    sort_by: Literal["occurred_at", "amount", "created_at"] = "occurred_at",
    sort_order: Literal["asc", "desc"] = "desc",
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if date_from and date_to and date_from >= date_to:
        raise validation_error("date_to", "date_to must be later than date_from")
    query = select(Payment).where(Payment.user_id == user.id)
    if date_from:
        query = query.where(Payment.occurred_at >= local_start(date_from, user.timezone))
    if date_to:
        query = query.where(Payment.occurred_at < local_start(date_to, user.timezone))
    filters = {
        Payment.subscription_id: subscription_id,
        Payment.category_id: category_id,
        Payment.currency: currency,
        Payment.payment_type: payment_type,
        Payment.status: status,
    }
    for column, value in filters.items():
        if value is not None:
            query = query.where(column == value)
    rows, total = await paginate(session, query, ordering(SORT_COLUMNS[sort_by], sort_order, Payment.id), limit, offset)
    items = [
        PaymentListItem(
            id=p.id,
            subscription_id=p.subscription_id,
            service_name=p.service_name_snapshot,
            amount=money(p.amount, p.currency),
            currency=p.currency,
            payment_type=p.payment_type,
            status=p.status,
            occurred_at=p.occurred_at,
            billing_period_start=p.billing_period_start,
            category_id=p.category_id,
            category_name=p.category_snapshot,
            original_payment_id=p.original_payment_id,
            source=p.source,
        )
        for (p,) in rows
    ]
    return page(items, total, limit, offset)


@router.get("/{payment_id}", response_model=PaymentDetail)
async def get_payment(
    payment_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentDetail:
    payment = await service.get_owned_payment(session, user, payment_id)
    detail = PaymentDetail(**service.to_out(payment).model_dump())
    if payment.payment_type == "charge":
        refunds = await session.scalars(
            select(Payment).where(Payment.original_payment_id == payment.id).order_by(Payment.occurred_at)
        )
        detail.refunds = [
            RefundBrief(
                id=r.id,
                amount=money(r.amount, r.currency),
                currency=r.currency,
                status=r.status,
                occurred_at=r.occurred_at,
            )
            for r in refunds
        ]
    return detail


@router.post("/{payment_id}/refunds", status_code=201, response_model=PaymentOut)
async def create_refund(
    payment_id: uuid.UUID,
    body: RefundIn,
    key: str = Depends(idempotency_key),
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    async def handler():
        refund = await service.create_refund(session, user, payment_id, body)
        return refund.id, service.to_out(refund)

    digest = request_hash(body, {"payment_id": payment_id})
    return await run_idempotent(session, user, key, "POST /api/v1/payments/{payment_id}/refunds", digest, 201, handler)


@router.post("/{payment_id}/reverse", response_model=ReverseOut)
async def reverse_payment(
    payment_id: uuid.UUID,
    body: ReverseIn,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> ReverseOut:
    payment = await service.reverse(session, user, payment_id, body.reason)
    await session.commit()
    return ReverseOut(id=payment.id, status=payment.status, reversed_at=utcnow(), reason=body.reason)
