"""Batches that are expired or about to be, with stock still on the shelf.

A batch carries its expiry date on the movements that brought it in. This reads
the ledger once for the whole programme, works out what each store still holds
of each dated batch, and lists those whose expiry falls within the product's
warning period (`Commodity.expiry_warning_days`, the programme's own number,
90 days to begin with).

Only stock still held counts: a batch fully issued, consumed or written off is
not a warning, and a batch whose balance is negative is a different finding
(stock_negative) so it is left to that one.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Max, Sum

from connect_labs.supply_chain.models import Commodity, Movement, SupplyPoint, scope_key


def _by_batch(movements, side):
    rows = (
        movements.exclude(**{f"{side}__isnull": True})
        .exclude(batch="")
        .order_by()
        .values(side, "item_id", "commodity_id", "batch", "quantity_unit")
        .annotate(total=Sum("quantity"))
    )
    return {(r[side], r["item_id"], r["commodity_id"], r["batch"], r["quantity_unit"]): r["total"] for r in rows}


def expiring_batches(program_id, *, today=None, opportunity_id=None) -> list[dict]:
    """[{supply_point, commodity, item_id, batch, expiry, days_left, quantity, unit, warn_days}], soonest first.

    `days_left` is negative once the batch has expired. Only batches with a
    recorded expiry and a positive balance at a store are listed.
    """
    today = today or date.today()
    moved = Movement.objects.for_program(program_id)
    if opportunity_id is not None:
        moved = moved.for_opportunity(opportunity_id)

    # A batch's expiry is whatever its movements say; the latest stated wins.
    expiry = {
        (r["item_id"], r["commodity_id"], r["batch"]): r["expiry"]
        for r in moved.exclude(batch="")
        .exclude(expiry=None)
        .order_by()
        .values("item_id", "commodity_id", "batch")
        .annotate(expiry=Max("expiry"))
    }
    if not expiry:
        return []

    held = _by_batch(moved, "to_supply_point_id")
    for key, total in _by_batch(moved, "from_supply_point_id").items():
        held[key] = held.get(key, Decimal("0")) - total

    commodities = {c.pk: c for c in Commodity.objects.filter(scope_key=scope_key(program_id=program_id))}
    points = {p.pk: p for p in SupplyPoint.objects.filter(program_id=program_id)}
    out = []
    for (point_id, item_id, commodity_id, batch, unit), quantity in held.items():
        ends = expiry.get((item_id, commodity_id, batch))
        commodity = commodities.get(commodity_id)
        if ends is None or commodity is None or quantity <= 0 or point_id not in points:
            continue
        warn = commodity.expiry_warning_days
        days_left = (ends - today).days
        if ends > today + timedelta(days=warn):
            continue
        out.append(
            {
                "supply_point": points[point_id],
                "commodity": commodity,
                "item_id": item_id,
                "batch": batch,
                "expiry": ends,
                "days_left": days_left,
                "quantity": quantity,
                "unit": unit,
                "warn_days": warn,
            }
        )
    out.sort(key=lambda r: (r["expiry"], r["supply_point"].name, r["batch"]))
    return out


def expiry_words(expiry, today=None) -> dict:
    """How a batch's expiry date is said everywhere (the chase list, the Overview's issues, Issues).

    {"expired": bool, "label": "Expired" | "Expiring soon", "days": days past (expired) or to go (soon),
    "when": "expired 3 days ago, on 2 Nov 2026" | "expires in 71 days, on 15 Jan 2027"}. Only a date in the
    past is "Expired"; today and later is still in date, so "Expiring soon" -- one rule, so two pages
    cannot call the same batch by two names or count its age from two different days.
    """
    from connect_labs.supply_chain.values import day_text

    today = today or date.today()
    days = (today - expiry).days
    if days > 0:
        return {
            "expired": True,
            "label": "Expired",
            "days": days,
            "when": f"expired {days} day{'' if days == 1 else 's'} ago, on {day_text(expiry)}",
        }
    ahead = -days
    return {
        "expired": False,
        "label": "Expiring soon",
        "days": ahead,
        "when": (
            "expires today"
            if ahead == 0
            else f"expires in {ahead} day{'' if ahead == 1 else 's'}, on {day_text(expiry)}"
        ),
    }
