"""Stock across a whole network, not one point at a time.

This is the network manager's view: every worker on an opportunity, what each
holds, how long it lasts, and which are about to run out. It is a separate
module from `soh.py` because the shape of the problem is different -- one
pass over the ledger for many points, rather than one point's figure -- and
because doing it naively is an N+1 over however many workers an opportunity
has.

The rule it must not break: a point whose figure is `Unconfirmed` appears in
the list carrying its reason, never as a zero and never omitted. A network
view that silently drops the points it could not compute is how a stockout
goes unnoticed.
"""

from django.db.models import Sum

from connect_labs.supply_chain.models import Movement, StockCount, SupplyPoint
from connect_labs.supply_chain.stock.services import ledger, resupply
from connect_labs.supply_chain.values import Quantity, Unconfirmed


def _latest_counts(program_id, points, item=None):
    """{supply_point_id: StockCount} -- the most recent count per point, in one query."""
    counts = StockCount.objects.filter(program_id=program_id, supply_point__in=points).order_by(
        "supply_point_id", "-counted_on", "-id"
    )
    if item is not None:
        counts = counts.filter(item=item)
    latest: dict[int, StockCount] = {}
    for count in counts:
        latest.setdefault(count.supply_point_id, count)
    return latest


def _balances(program_id, points, item=None, on_date=None):
    """{supply_point_id: {unit: Decimal}} for every point, in two queries.

    One grouped aggregate for everything moving in and one for everything
    moving out, rather than a query per point. `MovementQuerySet` owns the
    sign convention; this only fans the result out by point.
    """
    movements = Movement.objects.for_program(program_id).as_of(on_date)
    if item is not None:
        movements = movements.filter(item=item)
    ids = [point.pk for point in points]

    inbound = (
        movements.filter(to_supply_point_id__in=ids)
        .values("to_supply_point_id", "quantity_unit")
        .annotate(total=Sum("quantity"))
    )
    outbound = (
        movements.filter(from_supply_point_id__in=ids)
        .values("from_supply_point_id", "quantity_unit")
        .annotate(total=Sum("quantity"))
    )

    by_point: dict[int, dict[str, object]] = {point_id: {} for point_id in ids}
    for row in inbound:
        bucket = by_point[row["to_supply_point_id"]]
        unit = row["quantity_unit"]
        bucket[unit] = bucket.get(unit, 0) + row["total"]
    for row in outbound:
        bucket = by_point[row["from_supply_point_id"]]
        unit = row["quantity_unit"]
        bucket[unit] = bucket.get(unit, 0) - row["total"]
    return by_point


def network_stock(
    program_id,
    opportunity_id=None,
    item=None,
    kind=None,
    on_date=None,
    window_days=resupply.DEFAULT_WINDOW_DAYS,
) -> list[dict]:
    """One row per supply point: what it holds, and how long that lasts.

    Rows carry the point's own min/max band, so "below minimum" means below
    the band this point is managed to rather than a number chosen here.
    """
    points = SupplyPoint.objects.filter(program_id=program_id, status="active")
    if opportunity_id is not None:
        points = points.filter(opportunity_id=opportunity_id)
    if kind is not None:
        points = points.filter(kind=kind)
    points = list(points.order_by("kind", "name"))
    if not points:
        return []

    balances = _balances(program_id, points, item=item, on_date=on_date)
    counts = _latest_counts(program_id, points, item=item)

    rows = []
    for point in points:
        on_hand = ledger.collapse(balances.get(point.pk, {}), item, None)
        count = counts.get(point.pk)
        plan = resupply.plan(program_id, point, item=item, as_of=on_date, window_days=window_days)
        rows.append(
            {
                "supply_point_id": point.pk,
                "name": point.name,
                "kind": point.kind,
                "connect_username": point.connect_username,
                "admin_area": point.admin_area,
                "opportunity_id": point.opportunity_id,
                "on_hand": on_hand,
                "reported": Quantity(count.quantity, count.quantity_unit) if count else None,
                "reported_on": count.counted_on if count else None,
                "reported_kind": count.kind if count else None,
                "amc": plan["amc"],
                "months_of_stock": plan["months_of_stock"],
                "days_to_stockout": plan["days_to_stockout"],
                "resupply_quantity": plan["resupply_quantity"],
                "status": plan["status"],
                "min_months_of_stock": point.min_months_of_stock,
                "max_months_of_stock": point.max_months_of_stock,
            }
        )
    return rows


def summarise(rows: list[dict]) -> dict:
    """Counts by status, plus how many rows could not be computed at all.

    `unknown` is reported as its own number rather than folded into "ok",
    because "we cannot tell" and "it is fine" are different answers and only
    one of them needs a human.
    """
    tally: dict[str, int] = {}
    for row in rows:
        tally[row["status"]] = tally.get(row["status"], 0) + 1
    return {
        "points": len(rows),
        "by_status": tally,
        "unconfirmed_on_hand": sum(1 for row in rows if isinstance(row["on_hand"], Unconfirmed)),
        "never_reported": sum(1 for row in rows if row["reported"] is None),
    }
