import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_verified_user
from app.db import get_session
from app.errors import AppError
from app.models import Category, User
from app.schemas.common import LimitQuery, OffsetQuery, Page
from app.schemas.domain import CategoryIn, CategoryOut, CategoryPatch
from app.services import categories as service
from app.services.common import audit, snapshot
from app.services.listing import ordering, page, paginate

router = APIRouter(prefix="/categories", tags=["categories"])

FIELDS = ["name", "description", "color", "is_active"]


def name_conflict() -> AppError:
    return AppError(409, "CATEGORY_ALREADY_EXISTS", "Category name is already used", {"field": "name"})


async def commit_unique(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise name_conflict() from None


@router.get("", response_model=Page[CategoryOut])
async def list_categories(
    include_inactive: bool = False,
    sort_by: Literal["name", "created_at"] = "name",
    sort_order: Literal["asc", "desc"] = "asc",
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(Category).where(service.visible_to(user))
    if not include_inactive:
        query = query.where(Category.is_active.is_(True))
    column = func.lower(Category.name) if sort_by == "name" else Category.created_at
    rows, total = await paginate(session, query, ordering(column, sort_order, Category.id), limit, offset)
    return page([r[0] for r in rows], total, limit, offset)


@router.get("/{category_id}", response_model=CategoryOut)
async def get_category(
    category_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> Category:
    return await service.get_visible(session, user, category_id)


@router.post("", status_code=201, response_model=CategoryOut)
async def create_category(
    body: CategoryIn,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> Category:
    if await service.name_taken(session, user, body.name):
        raise name_conflict()
    category = Category(user_id=user.id, is_system=False, is_active=True, **body.model_dump())
    session.add(category)
    await session.flush()
    audit(session, user.id, "category", category.id, "created", None, snapshot(category, FIELDS))
    await commit_unique(session)
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
async def patch_category(
    category_id: uuid.UUID,
    body: CategoryPatch,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> Category:
    category = await service.get_own(session, user, category_id)
    changes = body.changes()
    if "name" in changes and await service.name_taken(session, user, changes["name"], category.id):
        raise name_conflict()
    old = snapshot(category, FIELDS)
    if changes.pop("is_active", None) is False:
        await service.deactivate(session, user, category)
    elif "is_active" in body.model_fields_set:
        category.is_active = True
    for key, value in changes.items():
        setattr(category, key, value)
    audit(session, user.id, "category", category.id, "updated", old, snapshot(category, FIELDS))
    await commit_unique(session)
    return category


@router.delete("/{category_id}", status_code=204, response_class=Response)
async def delete_category(
    category_id: uuid.UUID,
    user: User = Depends(current_verified_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    category = await service.get_own(session, user, category_id)
    await service.deactivate(session, user, category)
    await session.commit()
    return Response(status_code=204)
