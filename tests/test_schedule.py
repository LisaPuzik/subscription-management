from datetime import date

import pytest

from app.domain.schedule import (
    add_months,
    can_change_status,
    first_after,
    first_on_or_after,
    is_occurrence,
    occurrence,
    occurrences_between,
)


def test_month_end_keeps_anchor_day():
    anchor = date(2026, 1, 31)
    dates = [occurrence(anchor, "month", 1, n) for n in range(4)]
    assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_month_end_leap_year():
    assert occurrence(date(2028, 1, 31), "month", 1, 1) == date(2028, 2, 29)


def test_leap_day_yearly():
    anchor = date(2028, 2, 29)
    assert [occurrence(anchor, "year", 1, n) for n in range(5)] == [
        date(2028, 2, 29),
        date(2029, 2, 28),
        date(2030, 2, 28),
        date(2031, 2, 28),
        date(2032, 2, 29),
    ]


@pytest.mark.parametrize(
    ("unit", "count", "expected"),
    [
        ("day", 10, date(2026, 1, 11)),
        ("week", 1, date(2026, 1, 8)),
        ("week", 2, date(2026, 1, 15)),
        ("month", 3, date(2026, 4, 1)),
        ("year", 1, date(2027, 1, 1)),
    ],
)
def test_intervals(unit, count, expected):
    assert occurrence(date(2026, 1, 1), unit, count, 1) == expected


def test_first_on_or_after():
    anchor = date(2026, 1, 31)
    assert first_on_or_after(anchor, "month", 1, date(2025, 12, 1)) == anchor
    assert first_on_or_after(anchor, "month", 1, date(2026, 2, 28)) == date(2026, 2, 28)
    assert first_on_or_after(anchor, "month", 1, date(2026, 3, 1)) == date(2026, 3, 31)
    assert first_on_or_after(date(2026, 1, 1), "week", 1, date(2026, 1, 9)) == date(2026, 1, 15)
    assert first_on_or_after(date(2026, 1, 1), "month", 3, date(2026, 5, 1)) == date(2026, 7, 1)


def test_first_after():
    assert first_after(date(2026, 1, 31), "month", 1, date(2026, 2, 28)) == date(2026, 3, 31)
    assert first_after(date(2026, 1, 1), "day", 1, date(2026, 1, 1)) == date(2026, 1, 2)


def test_is_occurrence():
    anchor = date(2026, 1, 31)
    assert is_occurrence(anchor, "month", 1, date(2026, 2, 28))
    assert not is_occurrence(anchor, "month", 1, date(2026, 2, 27))
    assert not is_occurrence(anchor, "month", 1, date(2025, 12, 31))


def test_weekly_occurrences_in_month():
    dates = occurrences_between(date(2026, 1, 1), "week", 1, date(2026, 12, 1), date(2027, 1, 1))
    assert dates == [date(2026, 12, d) for d in (3, 10, 17, 24, 31)]


def test_add_months_wraps_year():
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_status_transitions():
    assert can_change_status("active", "paused")
    assert can_change_status("paused", "active")
    assert can_change_status("paused", "cancelled")
    assert can_change_status("active", "active")
    assert not can_change_status("cancelled", "active")
    assert not can_change_status("cancelled", "paused")
