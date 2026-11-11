from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def paginate(session: AsyncSession, query: Select, order, limit: int, offset: int) -> tuple[list, int]:
    total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = (await session.execute(query.order_by(*order).limit(limit).offset(offset))).all()
    return rows, total


def page(items: list, total: int, limit: int, offset: int) -> dict:
    return {"items": items, "pagination": {"total": total, "limit": limit, "offset": offset}}


def ordering(column, direction: str, tiebreaker):
    primary = column.desc() if direction == "desc" else column.asc()
    return [primary, tiebreaker.desc() if direction == "desc" else tiebreaker.asc()]
