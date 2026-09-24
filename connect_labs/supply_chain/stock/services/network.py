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

from datetime import date, timedelta

from django.db.models import Sum

from connect_labs.supply_chain.models import Contract, Item, Movement, StockCount, SupplyPoint
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
    """{supply_point_id: ({unit: Decimal}, {item_id})} for every point, in two queries.

    One grouped aggregate for everything moving in and one for everything
    moving out, rather than a query per point. `MovementQuerySet` owns the
    sign convention; this only fans the result out by point.

    The item ids come back alongside the units because a balance spanning
    cartons and sachets is convertible when the point holds ONE item that
    states its pack size, and is genuinely not convertible when it holds
    several. Without that, every point holding one item plus a consumption
    record in base units reported "cannot be computed" -- alarming, and
    wrong.
    """
    movements = Movement.objects.for_program(program_id).as_of(on_date)
    if item is not None:
        movements = movements.filter(item=item)
    ids = [point.pk for point in points]

    inbound = (
        movements.filter(to_supply_point_id__in=ids)
        .values("to_supply_point_id", "quantity_unit", "item_id")
        .annotate(total=Sum("quantity"))
    )
    outbound = (
        movements.filter(from_supply_point_id__in=ids)
        .values("from_supply_point_id", "quantity_unit", "item_id")
        .annotate(total=Sum("quantity"))
    )

    by_point: dict[int, tuple[dict, set]] = {point_id: ({}, set()) for point_id in ids}
    for rows, key, sign in ((inbound, "to_supply_point_id", 1), (outbound, "from_supply_point_id", -1)):
        for row in rows:
            units, items = by_point[row[key]]
            unit = row["quantity_unit"]
            units[unit] = units.get(unit, 0) + sign * row["total"]
            if row["item_id"] is not None:
                items.add(row["item_id"])
    return by_point


# Nothing more is coming on an order in one of these: settled, or called off.
# A draft has not been placed, so nothing is coming on it yet either.
_NOT_EXPECTED = ("draft", "received", "closed", "cancelled")


def _expected_inbound(program_id, points, item=None, as_of=None):
    """{supply_point_id: [order still to arrive]} -- stock on its way, not cover.

    Months of stock is on-hand alone (resupply.py), and it stays that way: a
    consignment ninety days late has proved it is not cover. But a store
    whose donor still owes it 400 jerry cans is not in the same position as
    one nobody owes anything, and the page said the second about both.

    What is still to come is the order page's own "Still outstanding" -- the
    three-way match -- so the two screens cannot disagree about it. On an
    order paid in advance that is what is awaited, not what was refused on
    arrival: refused goods are owed back, not on their way. An order whose
    shortfall another order was placed to cover is not waited on.
    """
    from connect_labs.supply_chain.fulfilment.services.match import three_way_match

    contracts = (
        Contract.objects.filter(program_id=program_id, delivery_supply_point__in=points)
        .exclude(status__in=_NOT_EXPECTED)
        .select_related("supplier", "item")
        .order_by("signed_on", "pk")
    )
    if item is not None:
        contracts = contracts.filter(item=item)
    today = as_of or date.today()

    by_point: dict[int, list] = {}
    for contract in contracts:
        match = three_way_match(contract)
        outstanding = (
            match["awaiting_delivery"] if match.get("awaiting_delivery") is not None else match["outstanding"]
        )
        if outstanding is None or match["covered_by"]:
            continue
        if isinstance(outstanding, Quantity) and outstanding.amount <= 0:
            continue
        expected_on = None
        if contract.signed_on is not None and contract.promised_lead_time_days is not None:
            expected_on = contract.signed_on + timedelta(days=contract.promised_lead_time_days)
        by_point.setdefault(contract.delivery_supply_point_id, []).append(
            {
                "contract_id": contract.pk,
                "reference": contract.reference,
                "supplier": {"id": contract.supplier_id, "name": contract.supplier.name},
                # What is on its way, so an empty store still says what it is short of.
                "item_name": contract.item.name if contract.item_id else "",
                "outstanding": outstanding,
                "expected_on": expected_on,
                "overdue": expected_on is not None and expected_on < today,
            }
        )
    return by_point


def _restated(amc, unit, item):
    if not isinstance(amc, Quantity) or not unit or amc.unit == unit:
        return None
    converted = ledger.convert(amc.amount, amc.unit, unit, item)
    return converted if isinstance(converted, Quantity) else None


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
    expected = _expected_inbound(program_id, points, item=item, as_of=on_date)
    counts = _latest_counts(program_id, points, item=item)
    # One fetch for every item any of these points has held, so resolving a
    # point's sole item costs no extra query per point.
    items_by_id = (
        {}
        if item is not None
        else {
            i.pk: i
            for i in Item.objects.filter(pk__in={pk for _, held in balances.values() for pk in held}).select_related(
                "commodity"
            )
        }
    )

    rows = []
    for point in points:
        units, item_ids = balances.get(point.pk, ({}, set()))
        # Convert using the point's own item when it holds exactly one. With
        # several, a single figure spanning them is not a quantity anyone can
        # act on, and `collapse` says so.
        for_conversion = item
        if for_conversion is None and len(item_ids) == 1:
            for_conversion = items_by_id.get(next(iter(item_ids)))

        # Report the whole column in one unit where the item states a pack
        # size: a table mixing cartons and sachets row by row is not
        # comparable by eye, which is the only thing a network view is for.
        # The pack unit as the ledger resolves it -- the item's own, else its
        # product's. Reading `item.pack_unit` alone gave "" for an item that
        # inherits its units, and a balance cannot be converted into "".
        display_unit = ledger.pack_unit_of(for_conversion)
        on_hand = ledger.collapse(units, for_conversion, display_unit)
        # The same balance in single units beside it. The pack keeps the column
        # comparable; the unit is what gets counted out -- for a kit that is one
        # course, "14 carton" hides the answer "700 courses" behind arithmetic.
        on_hand_in_base = None
        _, base_unit, _ = ledger._pack_spec(for_conversion)
        if isinstance(on_hand, Quantity) and base_unit and on_hand.unit != base_unit:
            restated = ledger.convert(on_hand.amount, on_hand.unit, base_unit, for_conversion)
            on_hand_in_base = restated if isinstance(restated, Quantity) else None
        count = counts.get(point.pk)
        # The RESOLVED item, not the caller's: cover divides a carton balance
        # by a sachet consumption rate, which needs the pack size. Without it
        # every point reported "unknown" for a reason that was not about the
        # data at all.
        plan = resupply.plan(program_id, point, item=for_conversion, as_of=on_date, window_days=window_days)
        rows.append(
            {
                "supply_point_id": point.pk,
                "name": point.name,
                # What the figures are of, when the point holds one item: a
                # check that says "below its minimum" should say of what.
                "item": for_conversion,
                # Where the point is restocked from: another point in this
                # programme sends to it; a point with none above it reorders
                # from its supplier.
                "restocked_from": resupply.restocked_from(point),
                "kind": point.kind,
                "connect_username": point.connect_username,
                "admin_area": point.admin_area,
                "opportunity_id": point.opportunity_id,
                "on_hand": on_hand,
                "on_hand_in_base": on_hand_in_base,
                # Counted whole in its single unit -- a kit, or an item one of
                # which is a course -- so the single unit is the figure to lead
                # with: "700 packets" is the answer, "14 cartons" the packing.
                "counted_in_base": bool(
                    for_conversion is not None
                    and (
                        (for_conversion.is_kit and for_conversion.components_per == "base")
                        or for_conversion.one_course_is == "base_unit"
                    )
                ),
                # The one item behind the balance, when there is one, so the
                # figure can link to the movements that make it up.
                "item_id": for_conversion.pk if for_conversion is not None else None,
                "reported": Quantity(count.quantity, count.quantity_unit) if count else None,
                "reported_on": count.counted_on if count else None,
                "reported_kind": count.kind if count else None,
                "amc": plan["amc"],
                "amc_basis": plan["amc_basis"],
                # The same rate in the unit the balance is shown in, so "170
                # carton" and "3,033 co-pack a month" can be compared by eye.
                "amc_in_display_unit": _restated(plan["amc"], display_unit, for_conversion),
                "months_of_stock": plan["months_of_stock"],
                "days_to_stockout": plan["days_to_stockout"],
                "resupply_quantity": plan["resupply_quantity"],
                "status": plan["status"],
                # Shown beside the figures above, never netted into them.
                "expected_inbound": expected.get(point.pk, []),
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
