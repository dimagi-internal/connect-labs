"""Balances and positions, derived from the movement ledger.

Nothing here stores a level. A stored level drifts from the events that
produced it, and then two screens disagree and nobody can say which is
right. Every figure below is a fresh aggregate over `Movement`.

The unit rule is the one that matters. A store counts packs, a field worker
counts base units, and the bridge between them is the pack specification --
which the supplier may never have stated. So a balance that spans units
returns `Unconfirmed` naming the missing pack spec rather than picking a
conversion factor. That refusal is the same one the pricing rules make, for
the same reason and over the same missing fact.
"""

from decimal import Decimal

from django.db.models import Sum

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import DistributionLine, Movement, ShipmentLine
from connect_labs.supply_chain.values import Quantity, unconfirmed

ZERO = Decimal("0")


def _sum_by_unit(lines) -> dict[str, Decimal]:
    """{unit: total} over any queryset with quantity and quantity_unit, summed in SQL."""
    return {
        row["quantity_unit"]: (row["total"] or ZERO)
        for row in lines.values("quantity_unit").annotate(total=Sum("quantity"))
    }


def _pack_spec(item):
    """(base_per_pack, base_unit, pack_unit) for an item, or (None, ..., ...)."""
    if item is None:
        return None, None, None
    base_per_pack = item.base_per_pack
    base_unit = item.base_unit or (item.commodity.base_unit if item.commodity_id else None)
    pack_unit = item.pack_unit or (item.commodity.pack_unit if item.commodity_id else None)
    return base_per_pack, base_unit, pack_unit


def convert(amount: Decimal, from_unit: str, to_unit: str, item):
    """`amount` restated in `to_unit`, or an Unconfirmed naming what is missing.

    Only the base/pack ladder is convertible, and only when the item states
    how many base units are in a pack. Anything else is refused rather than
    approximated -- there is no safe default for "how many sachets in a
    carton", and a wrong one silently misreports every balance that uses it.
    """
    if from_unit == to_unit:
        return Quantity(amount, to_unit)

    base_per_pack, base_unit, pack_unit = _pack_spec(item)
    label = item.name if item is not None else "this commodity"

    if base_per_pack in (None, 0):
        return unconfirmed(
            f"{label} does not state how many {from_unit}s are in a {to_unit}, "
            f"so a balance held in both cannot be added up"
        )
    factor = Decimal(base_per_pack)
    if from_unit == base_unit and to_unit == pack_unit:
        return Quantity(amount / factor, to_unit)
    if from_unit == pack_unit and to_unit == base_unit:
        return Quantity(amount * factor, to_unit)
    return unconfirmed(f"no stated relationship between {from_unit} and {to_unit} for {label}")


def collapse(by_unit: dict, item, unit: str | None):
    """One Quantity from a {unit: amount} map, or Unconfirmed if it cannot be done."""
    holdings = {u: amount for u, amount in by_unit.items() if amount != ZERO}

    if not holdings:
        # Nothing here. The unit is whatever the caller asked for, else the
        # item's pack unit, else genuinely unknown -- reported as None rather
        # than as the invented word "unit", which reads like a real unit of
        # measure and is not one.
        return Quantity(ZERO, unit or _pack_spec(item)[2] or next(iter(by_unit), None))

    if unit is None:
        if len(holdings) == 1:
            only_unit, amount = next(iter(holdings.items()))
            return Quantity(amount, only_unit)
        # No target asked for and several units held: choose the pack unit if
        # the item names one, so the common case (cartons in, sachets out)
        # answers in cartons rather than refusing.
        unit = _pack_spec(item)[2]
        if unit is None:
            return unconfirmed(
                "stock is held in " + " and ".join(sorted(holdings)) + ", and no pack specification "
                "says how to combine them"
            )

    total = ZERO
    reasons: list[str] = []
    for held_unit, amount in holdings.items():
        converted = convert(amount, held_unit, unit, item)
        if isinstance(converted, Quantity):
            total += converted.amount
        else:
            reasons.extend(converted.reasons)
    if reasons:
        return unconfirmed(*dict.fromkeys(reasons))
    return Quantity(total, unit)


def _scope(program_id, opportunity_id=None, on_date=None):
    qs = Movement.objects.for_program(program_id)
    if opportunity_id is not None:
        qs = qs.for_opportunity(opportunity_id)
    return qs.as_of(on_date)


def balance(program_id, supply_point, item=None, unit=None, on_date=None):
    """What is at this point now (or on `on_date`), as a Quantity or Unconfirmed."""
    qs = _scope(program_id, on_date=on_date)
    return collapse(qs.balance_by_unit(supply_point, item=item), item, unit)


def balance_by_batch(program_id, supply_point, item=None, on_date=None):
    """[{batch, expiry, quantity, unit}] held at this point, soonest expiry first.

    The order is the first-expired-first-out issue order. Expiry comes from
    the movements that brought each batch in, so a batch with no stated
    expiry sorts last rather than sorting as if it never expires.
    """
    qs = _scope(program_id, on_date=on_date)
    totals = qs.balance_by_batch(supply_point, item=item)
    expiries = {
        row["batch"]: row["expiry"]
        for row in qs.filter(to_supply_point=supply_point).values("batch", "expiry")
        if row["expiry"]
    }
    rows = [
        {
            "batch": batch,
            "expiry": expiries.get(batch),
            "quantity": amount,
            "unit": held_unit,
        }
        for (batch, held_unit), amount in totals.items()
        if amount != ZERO
    ]
    return sorted(rows, key=lambda row: (row["expiry"] is None, row["expiry"] or "", row["batch"]))


def in_transit(program_id, supply_point=None, item=None):
    """Dispatched and not yet received -- a real position, and never stock.

    Kept apart from on-hand deliberately (design doc section 19.1). Counted
    as stock, a network reads months of cover it does not have, nobody
    reorders, and the stores run dry while the goods sit at a border.
    """
    lines = ShipmentLine.objects.filter(
        shipment__status__in=records.IN_TRANSIT_STATUSES,
        shipment__contract__program_id=program_id,
    )
    if supply_point is not None:
        lines = lines.filter(shipment__contract__delivery_supply_point=supply_point)
    if item is not None:
        lines = lines.filter(item=item)
    return collapse(_sum_by_unit(lines), item, None)


def committed(program_id, supply_point, item=None):
    """Allocated to somebody but not yet moved -- promised, not gone.

    A distribution line whose movement is still null is a commitment: the
    stock is physically present but is not available to promise twice.
    """
    lines = DistributionLine.objects.filter(
        distribution__program_id=program_id,
        distribution__supply_point=supply_point,
        movement__isnull=True,
    )
    if item is not None:
        lines = lines.filter(item=item)
    return collapse(_sum_by_unit(lines), item, None)


def position(program_id, supply_point, item=None, unit=None, on_date=None):
    """The four figures that are not the same figure.

    on_hand, in_transit, committed and available are reported separately
    because conflating any two of them produces a confident wrong answer:
    in_transit inflates cover, committed lets the same carton be promised
    twice.
    """
    on_hand = balance(program_id, supply_point, item=item, unit=unit, on_date=on_date)
    promised = committed(program_id, supply_point, item=item)
    available = on_hand
    if isinstance(on_hand, Quantity) and isinstance(promised, Quantity):
        restated = convert(promised.amount, promised.unit, on_hand.unit, item)
        available = (
            Quantity(on_hand.amount - restated.amount, on_hand.unit) if isinstance(restated, Quantity) else restated
        )
    return {
        "on_hand": on_hand,
        "in_transit": in_transit(program_id, supply_point=supply_point, item=item),
        "committed": promised,
        "available": available,
    }
