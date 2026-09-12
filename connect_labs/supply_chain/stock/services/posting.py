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
        recorded_by_party=event.recorded_by_party,
        **links,
    )


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
    return Movement.objects.bulk_create(movements)


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
    balance = ledger.balance(program_id, count.supply_point, item=count.item, unit=count.quantity_unit)
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
        item=count.item,
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
