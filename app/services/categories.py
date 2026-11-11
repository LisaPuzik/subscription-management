import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, not_found
from app.models import Category, Subscription, User
from app.services.common import audit


def visible_to(user: User):
    return or_(Category.user_id == user.id, Category.is_system.is_(True))


async def get_visible(session: AsyncSession, user: User, category_id: uuid.UUID) -> Category:
    category = await session.scalar(select(Category).where(Category.id == category_id, visible_to(user)))
    if category is None:
        raise not_found()
    return category


async def get_own(session: AsyncSession, user: User, category_id: uuid.UUID) -> Category:
    category = await session.scalar(
        select(Category).where(Category.id == category_id, visible_to(user)).with_for_update()
    )
    if category is None:
        raise not_found()
    if category.is_system:
        raise AppError(403, "SYSTEM_CATEGORY_READ_ONLY", "System category is read-only")
    return category


async def assignable(session: AsyncSession, user: User, category_id: uuid.UUID) -> Category:
    category = await session.scalar(
        select(Category).where(Category.id == category_id, visible_to(user), Category.is_active.is_(True))
    )
    if category is None:
        raise AppError(409, "CATEGORY_NOT_AVAILABLE", "Category is not available", {"field": "category_id"})
    return category


async def name_taken(session: AsyncSession, user: User, name: str, exclude: uuid.UUID | None = None) -> bool:
    query = select(Category.id).where(Category.user_id == user.id, func.lower(Category.name) == name.lower())
    if exclude is not None:
        query = query.where(Category.id != exclude)
    return await session.scalar(query.limit(1)) is not None


async def deactivate(session: AsyncSession, user: User, category: Category) -> None:
    if not category.is_active:
        return
    category.is_active = False
    await session.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id, Subscription.category_id == category.id)
        .values(category_id=None)
    )
    audit(session, user.id, "category", category.id, "deactivated", {"is_active": True}, {"is_active": False})
