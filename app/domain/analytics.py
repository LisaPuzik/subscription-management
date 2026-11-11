import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.currency import money
from app.domain.schedule import add_months, occurrences_between

ZERO = Decimal(0)
NO_CATEGORY = "Без категории"


@dataclass(frozen=True)
class Row:
    currency: str
    payment_type: str
    total: Decimal
    count: int
    month: str | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = None


@dataclass
class Totals:
    charges: Decimal = ZERO
    refunds: Decimal = ZERO
    charges_count: int = 0
    refunds_count: int = 0

    def add(self, row: Row) -> None:
        if row.payment_type == "charge":
            self.charges += row.total
            self.charges_count += row.count
        else:
            self.refunds += row.total
            self.refunds_count += row.count

    def as_dict(self, currency: str, with_total_count: bool = True) -> dict:
        result = {
            "gross_charges": money(self.charges, currency),
            "refunds": money(self.refunds, currency),
            "net_expenses": money(self.charges - self.refunds, currency),
            "charges_count": self.charges_count,
            "refunds_count": self.refunds_count,
        }
        if with_total_count:
            result["payments_count"] = self.charges_count + self.refunds_count
        return result


def summary(rows: list[Row]) -> list[dict]:
    totals: dict[str, Totals] = defaultdict(Totals)
    for row in rows:
        totals[row.currency].add(row)
    return [{"currency": c, **totals[c].as_dict(c)} for c in sorted(totals)]


def month_keys(date_from: date, date_to: date) -> list[str]:
    last = date_to - timedelta(days=1)
    current = date_from.replace(day=1)
    keys = []
    while current <= last:
        keys.append(f"{current.year:04d}-{current.month:02d}")
        current = add_months(current, 1)
    return keys


def monthly(rows: list[Row], months: list[str], currencies: set[str]) -> list[dict]:
    grid: dict[str, dict[str, Totals]] = defaultdict(lambda: defaultdict(Totals))
    for row in rows:
        grid[row.currency][row.month].add(row)
    result = []
    for currency in sorted(currencies | grid.keys()):
        series = [{"month": m, **grid[currency][m].as_dict(currency, with_total_count=False)} for m in months]
        result.append({"currency": currency, "months": series})
    return result


def by_category(rows: list[Row]) -> list[dict]:
    groups: dict[str, dict[tuple, Totals]] = defaultdict(lambda: defaultdict(Totals))
    for row in rows:
        groups[row.currency][(row.category_id, row.category_name)].add(row)
    result = []
    for currency in sorted(groups):
        items = []
        for (category_id, name), totals in groups[currency].items():
            item = {"category_id": category_id, "category_name": name}
            if category_id is None:
                item["display_name"] = NO_CATEGORY
            items.append({**item, **totals.as_dict(currency)})
        items.sort(key=lambda i: (i["category_id"] is None, (i["category_name"] or "").lower()))
        result.append({"currency": currency, "categories": items})
    return result


@dataclass(frozen=True)
class PlannedSubscription:
    id: uuid.UUID
    service_name: str
    amount: Decimal
    currency: str
    unit: str
    count: int
    anchor: date
    next_payment_date: date
    category_id: uuid.UUID | None
    category_name: str | None


def forecast(
    subscriptions: list[PlannedSubscription],
    month_start: date,
    today: date,
    paid: set[tuple[uuid.UUID, date]],
) -> tuple[list[dict], list[dict]]:
    month_end = add_months(month_start, 1)
    items = []
    for sub in subscriptions:
        start = max(month_start, sub.next_payment_date, today)
        for day in occurrences_between(sub.anchor, sub.unit, sub.count, start, month_end):
            if (sub.id, day) in paid:
                continue
            items.append((day, sub))
    items.sort(key=lambda pair: (pair[0], pair[1].service_name.lower()))

    totals: dict[str, Decimal] = defaultdict(Decimal)
    categories: dict[str, dict[tuple, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for _, sub in items:
        totals[sub.currency] += sub.amount
        categories[sub.currency][(sub.category_id, sub.category_name)] += sub.amount

    totals_out = []
    for currency in sorted(totals):
        groups = []
        for (category_id, name), amount in categories[currency].items():
            group = {"category_id": category_id, "category_name": name}
            if category_id is None:
                group["display_name"] = NO_CATEGORY
            group["forecast_amount"] = money(amount, currency)
            groups.append(group)
        groups.sort(key=lambda g: (g["category_id"] is None, (g["category_name"] or "").lower()))
        totals_out.append(
            {
                "currency": currency,
                "forecast_amount": money(totals[currency], currency),
                "categories": groups,
            }
        )

    items_out = [
        {
            "subscription_id": sub.id,
            "service_name": sub.service_name,
            "amount": money(sub.amount, sub.currency),
            "currency": sub.currency,
            "expected_payment_date": day,
            "category_id": sub.category_id,
            "category_name": sub.category_name,
            "billing_interval_unit": sub.unit,
            "interval_count": sub.count,
        }
        for day, sub in items
    ]
    return totals_out, items_out
