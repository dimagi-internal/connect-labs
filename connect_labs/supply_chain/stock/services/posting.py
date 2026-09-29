"""Posting events to the ledger.

A receipt, a distribution run and a stock override are all *events* a user
records. Each one implies movements, and this module is the only place that
turns one into the other. Two reasons it is not spread across the operation
handlers:

  - the ledger is append-only, so a half-posted event is not something a
    later edit can tidy up. Each function below posts inside a transaction
    with its parent event.
  - what a rejected carton does, which side of a movement a distribution
    sets, and how an override's delta is computed are all decisions that
    must have exactly one answer.
"""

from django.db import transaction

from connect_labs.supply_chain.models import Movement
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Quantity, Unconfirmed


def _movement(
    event,
    *,
    kind,
    quantity,
    unit,
    item,
    commodity,
    batch="",
    expiry=None,
    frm=None,
    to=None,
    occurred_on,
    program_id,
    opportunity_id=None,
    **links,
):
    return Movement(
        program_id=program_id,
        opportunity_id=opportunity_id,
        kind=kind,
        occurred_on=occurred_on,
        from_supply_point=frm,
        to_supply_point=to,
        item=item,
        commodity=commodity,
        batch=batch,
        expiry=expiry,
        quantity=quantity,
        quantity_unit=unit,
        # The event and its movements agree about who said so by
        # construction, rather than by a caller remembering to pass it twice.
        source=event.source,
        recorded_by_org=event.recorded_by_org,
        **links,
    )


def post_consignment_leg(consignment, *, frm, to, quantity, occurred_on, kind="transfer") -> Movement:
    """One leg of a consignment on the ledger: onto the road, off it, or lost on it."""
    movement = _movement(
        consignment,
        kind=kind,
        quantity=quantity,
        unit=consignment.quantity_unit,
        item=consignment.item,
        commodity=consignment.commodity,
        batch=consignment.batch,
        frm=frm,
        to=to,
        occurred_on=occurred_on,
        program_id=consignment.program_id,
        opportunity_id=consignment.opportunity_id,
        reference=consignment.reference,
    )
    movement.save()
    return movement


@transaction.atomic
def post_receipt(receipt, commodity, program_id) -> list[Movement]:
    """One movement per accepted receipt line.

    Rejected quantity is recorded on the line but never posted: goods refused
    at the door were never stock, and posting them and then adjusting them
    out would leave a ledger that says they briefly existed.
    """
    movements = [
        _movement(
            receipt,
            kind="receipt",
            quantity=line.quantity_accepted,
            unit=line.quantity_unit,
            item=line.item,
            commodity=commodity,
            batch=line.batch,
            expiry=line.expiry,
            to=receipt.supply_point,
            occurred_on=receipt.received_on,
            program_id=program_id,
            opportunity_id=receipt.supply_point.opportunity_id,
            receipt=receipt,
        )
        for line in receipt.lines.all()
        if line.quantity_accepted and line.quantity_accepted > 0
    ]
    # One save each, not `bulk_create`: a bulk insert sends no `post_save`,
    # so the movements would never reach history and an as-of rewind past the
    # receipt could not delete the receipt they PROTECT.
    for movement in movements:
        movement.save()
    return movements


@transaction.atomic
def post_distribution(distribution, commodity) -> list[Movement]:
    """One movement per line, each from the store to one worker's holding.

    Because a worker is a supply point, this is the same primitive as a
    store-to-store transfer -- there is no separate arithmetic for
    "stock with a user", which is the whole reason kind="user_held" exists.
    """
    created = []
    for line in distribution.lines.select_related("to_supply_point").all():
        movement = _movement(
            distribution,
            kind="distribution",
            quantity=line.quantity,
            unit=line.quantity_unit,
            item=line.item,
            commodity=commodity,
            batch=line.batch,
            frm=distribution.supply_point,
            to=line.to_supply_point,
            occurred_on=distribution.distributed_on,
            program_id=distribution.program_id,
            opportunity_id=distribution.opportunity_id,
            distribution=distribution,
        )
        movement.save()
        line.movement = movement
        line.save(update_fields=["movement"])
        created.append(movement)
    return created


@transaction.atomic
def post_override(count, program_id):
    """Move the ledger to agree with an asserted figure.

    An override is a person saying "whatever the ledger thinks, there are N
    here". Honouring it means posting the difference as an `adjustment`, which
    is the one movement kind allowed to be negative -- so the ledger stays
    the authority while reality gets into it.

    Refused when the balance cannot be expressed in the count's unit: the
    delta would be a guess, and an append-only ledger is the wrong place to
    put a guess. The error names the missing pack specification, which is the
    thing that would make it computable.
    """
    # The count may not name a trade item -- the form leaves it optional -- but a
    # point that has only ever held one item can still be added up across its
    # packs and single units. Resolve it the way every other reader does.
    # Only an item of the counted commodity: a store that has only ever held
    # zinc says nothing about which RUTF was counted.
    item = count.item
    if item is None:
        sole = ledger.sole_item(program_id, count.supply_point)
        item = sole if sole is not None and sole.commodity_id == count.commodity_id else None
    balance = ledger.balance(program_id, count.supply_point, item=item, unit=count.quantity_unit)
    if isinstance(balance, Unconfirmed):
        raise ValueError("cannot override stock on hand here: " + "; ".join(balance.reasons))

    delta = count.quantity - balance.amount
    if delta == 0:
        return None

    movement = _movement(
        count,
        kind="adjustment",
        quantity=delta,
        unit=count.quantity_unit,
        item=item,
        commodity=count.commodity,
        batch=count.batch,
        to=count.supply_point,
        occurred_on=count.counted_on,
        program_id=program_id,
        opportunity_id=count.opportunity_id,
        stock_count=count,
    )
    movement.reference = f"override of {balance.amount} {balance.unit}"
    movement.save()
    count.adjustment_movement = movement
    count.save(update_fields=["adjustment_movement"])
    return movement


def expected_from_ledger(program_id, supply_point, item=None) -> Quantity | Unconfirmed:
    """What the ledger says should be here -- the figure an override overrides."""
    return ledger.balance(program_id, supply_point, item=item)


# Fields only the stock reader sets. A movement typed by a person or an agent
# through movement_record must never claim to be a visit or to reverse one.
VISIT_ONLY_FIELDS = frozenset({"visit_id", "reverses", "reverses_id", "estimated"})


def post_visit_consumption(
    *, program_id, opportunity_id, point, item, quantity, unit, occurred_on, visit_id, estimated
) -> Movement:
    """What one visit gave out of one item, leaving the worker's own stock."""
    movement = Movement(
        program_id=program_id,
        opportunity_id=opportunity_id,
        kind="consumption",
        occurred_on=occurred_on,
        from_supply_point=point,
        item=item,
        commodity=item.commodity,
        quantity=quantity,
        quantity_unit=unit,
        visit_id=str(visit_id),
        estimated=estimated,
        reference=f"visit {visit_id}"[:64],
        source="connect_visit",
    )
    movement.save()
    return movement


def post_visit_reversal(original: Movement, *, reason: str) -> Movement:
    """Cancel a visit's consumption: the same quantity back into the same point.

    Dated with the visit, not with the day the rejection was read, so a
    monthly rate over any window loses the pair exactly. What the page showed
    on a past day is reproduced by the as-of rewind (history/rewind.py), which
    removes this row for any date before it was recorded.
    """
    if original.kind != "consumption" or original.from_supply_point_id is None or not original.visit_id:
        raise ValueError("only a visit's consumption can be reversed; correct anything else with an adjustment")
    movement = Movement(
        program_id=original.program_id,
        opportunity_id=original.opportunity_id,
        kind="consumption",
        occurred_on=original.occurred_on,
        to_supply_point_id=original.from_supply_point_id,
        item_id=original.item_id,
        commodity_id=original.commodity_id,
        quantity=original.quantity,
        quantity_unit=original.quantity_unit,
        visit_id=original.visit_id,
        estimated=original.estimated,
        reverses=original,
        reference=f"reverses visit {original.visit_id}"[:64],
        note=reason,
        source="connect_visit",
    )
    movement.save()
    return movement
