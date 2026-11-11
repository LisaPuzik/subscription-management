from calendar import monthrange
from datetime import date, timedelta


def add_months(anchor: date, months: int) -> date:
    year, month = divmod(anchor.month - 1 + months, 12)
    year += anchor.year
    month += 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _months_step(unit: str, count: int) -> int | None:
    if unit == "month":
        return count
    if unit == "year":
        return count * 12
    return None


def _days_step(unit: str, count: int) -> int:
    return count * (7 if unit == "week" else 1)


def occurrence(anchor: date, unit: str, count: int, n: int) -> date:
    step = _months_step(unit, count)
    if step is not None:
        return add_months(anchor, step * n)
    return anchor + timedelta(days=_days_step(unit, count) * n)


def first_on_or_after(anchor: date, unit: str, count: int, target: date) -> date:
    if target <= anchor:
        return anchor
    step = _months_step(unit, count)
    if step is None:
        days = _days_step(unit, count)
        n = -(-(target - anchor).days // days)
        return occurrence(anchor, unit, count, n)
    diff = (target.year - anchor.year) * 12 + target.month - anchor.month
    n = diff // step
    result = occurrence(anchor, unit, count, n)
    while result < target:
        n += 1
        result = occurrence(anchor, unit, count, n)
    return result


def first_after(anchor: date, unit: str, count: int, target: date) -> date:
    return first_on_or_after(anchor, unit, count, target + timedelta(days=1))


def is_occurrence(anchor: date, unit: str, count: int, day: date) -> bool:
    return day >= anchor and first_on_or_after(anchor, unit, count, day) == day


def occurrences_between(anchor: date, unit: str, count: int, start: date, end: date) -> list[date]:
    result = []
    current = first_on_or_after(anchor, unit, count, start)
    while current < end:
        result.append(current)
        current = first_after(anchor, unit, count, current)
    return result


STATUS_TRANSITIONS = {
    "active": {"paused", "cancelled"},
    "paused": {"active", "cancelled"},
    "cancelled": set(),
}


def can_change_status(current: str, new: str) -> bool:
    return current == new or new in STATUS_TRANSITIONS[current]
