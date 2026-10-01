"""Stock on hand: two answers, both kept.

A supply point has a *ledger balance* (what the movements say) and a *last
reported count* (what somebody says is actually there). These disagree
routinely, and the disagreement is the finding -- a system that silently
overwrites one with the other destroys the only signal it had.

So `stock_on_hand` returns both plus the variance, and says which basis a
consumer should plan on and why. The further down the network you go the more
the count matters: a worker's phone is not a warehouse system, and an
override is how reality gets in (see overrides in stock/services/counts.py).
"""

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import StockCount
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Quantity, Unconfirmed, unconfirmed


def last_count(program_id, supply_point, item=None, on_date=None):
    """The most recent on-hand count at this point, or None (a reported receipt is not one). Overrides do not win on
    kind -- only on recency, because an older override has been overtaken by
    a newer physical count as surely as by another override."""
    counts = StockCount.objects.filter(
        program_id=program_id, supply_point=supply_point, kind__in=records.ON_HAND_COUNT_KINDS
    )
    if item is not None:
        counts = counts.filter(item=item)
    if on_date:
        counts = counts.filter(counted_on__lte=on_date)
    return counts.order_by("-counted_on", "-id").first()


def stock_on_hand(program_id, supply_point, item=None, unit=None, on_date=None) -> dict:  # noqa: C901
    """{ledger, ledger_on_count_day, reported, variance, basis, as_of, reported_kind, reported_source}.

    `variance` is reported MINUS the ledger ON THE COUNT DAY (`ledger_on_count_day`,
    every movement dated on or before it), not today's ledger: stock that moved
    after the count is not a discrepancy in it. This is the one per-point
    definition; belief.py (the Workers page) and `counted_against_ledger`
    (the checks feed) compute the same figure grouped for many points, and
    parity tests pin both to it, so the pages and the checks cannot disagree.

    `basis` names which figure a planner should use, and it is never a silent
    choice: `ledger` when no count exists, `count` when a count is more
    recent than the last movement it could have reflected, and
    `disagreement` when both exist and differ -- in which case the caller is
    told rather than handed one of them.
    """
    # Resolve the point's own item when the caller did not name one, so a
    # variance between a pack balance and a base-unit count is computed
    # rather than refused. See ledger.sole_item.
    item = item or ledger.sole_item(program_id, supply_point)
    balance = ledger.balance(program_id, supply_point, item=item, unit=unit, on_date=on_date)
    count = last_count(program_id, supply_point, item=item, on_date=on_date)

    if count is None:
        return {
            "ledger": balance,
            "ledger_on_count_day": None,
            "reported": None,
            "variance": None,
            "basis": "ledger",
            "as_of": on_date,
            "reported_kind": None,
            "reported_source": None,
        }

    reported = Quantity(count.quantity, count.quantity_unit)
    on_count_day = ledger.balance(program_id, supply_point, item=item, unit=unit, on_date=count.counted_on)
    variance = _variance(on_count_day, reported, item)
    basis = "ledger"
    if isinstance(variance, Unconfirmed):
        basis = "disagreement"
    elif isinstance(variance, Quantity) and variance.amount != 0:
        basis = "disagreement"

    return {
        "ledger": balance,
        "ledger_on_count_day": on_count_day,
        "reported": reported,
        "variance": variance,
        "basis": basis,
        "as_of": count.counted_on,
        "reported_kind": count.kind,
        "reported_source": count.source,
    }


def counted_against_ledger(program_id, points_and_items) -> dict:
    """{point id: {reported, reported_kind, ledger_on_count_day, variance, as_of}} for the counted ones, grouped.

    `stock_on_hand`'s count-day figures for many points at once, in three
    queries per distinct item rather than three per point. Each pair is a
    point id and the item `stock_on_hand` would resolve for it
    (`ledger.sole_item`, which a live `network_stock` row carries as its
    `item`), or None to read across every item as it does then. Live only:
    the latest count is the latest ever, as `stock_on_hand` with no `on_date`.
    A point never counted is left out.
    """
    from django.db.models import Exists, OuterRef, Sum

    from connect_labs.supply_chain.models import Movement

    groups: dict = {}
    for point_id, item in points_and_items:
        groups.setdefault(item.pk if item is not None else None, (item, []))[1].append(point_id)

    found = {}
    for item, ids in groups.values():
        counts = StockCount.objects.filter(
            program_id=program_id, supply_point_id__in=ids, kind__in=records.ON_HAND_COUNT_KINDS
        )
        if item is not None:
            counts = counts.filter(item=item)
        latest = {
            c.supply_point_id: c
            for c in counts.order_by("supply_point_id", "-counted_on", "-id").distinct("supply_point_id")
        }
        if not latest:
            continue

        moves = Movement.objects.for_program(program_id)
        if item is not None:
            moves = moves.filter(item=item)

        def on_or_before_the_count(side):
            # Dated on or before some count at its point: on or before the latest.
            earlier = counts.filter(supply_point_id=OuterRef(side), counted_on__gte=OuterRef("occurred_on"))
            return Exists(earlier.order_by().values("pk"))

        by_point: dict = {pk: {} for pk in latest}
        for side, sign in (("to_supply_point_id", 1), ("from_supply_point_id", -1)):
            rows = (
                moves.filter(**{f"{side}__in": list(latest)})
                .filter(on_or_before_the_count(side))
                .values(side, "quantity_unit")
                .annotate(total=Sum("quantity"))
            )
            for row in rows:
                units = by_point[row[side]]
                units[row["quantity_unit"]] = units.get(row["quantity_unit"], 0) + sign * row["total"]

        for pk, count in latest.items():
            reported = Quantity(count.quantity, count.quantity_unit)
            on_count_day = ledger.collapse(by_point[pk], item, None)
            found[pk] = {
                "reported": reported,
                "reported_kind": count.kind,
                "ledger_on_count_day": on_count_day,
                "variance": _variance(on_count_day, reported, item),
                "as_of": count.counted_on,
            }
    return found


def _variance(balance, reported: Quantity, item):
    """reported - ledger, in the ledger's unit, or Unconfirmed.

    This is where the carton/base-unit bridge fails when a supplier never
    stated the pack specification: the store counted packs, the worker
    counted sachets, and without the factor the two numbers cannot be
    subtracted. Returning a number anyway would be inventing the factor.
    """
    if isinstance(balance, Unconfirmed):
        return balance
    restated = ledger.convert(reported.amount, reported.unit, balance.unit, item)
    if isinstance(restated, Unconfirmed):
        return unconfirmed(
            f"the ledger is in {balance.unit}s and the count is in {reported.unit}s",
            *restated.reasons,
        )
    return Quantity(restated.amount - balance.amount, balance.unit)
