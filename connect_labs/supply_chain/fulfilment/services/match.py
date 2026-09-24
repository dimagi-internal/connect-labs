"""The three-way match: ordered against received against invoiced.

The standard control on paying for goods, and the one that matters most when
somebody else is the buyer of record -- because then the purchase order, the
receipt and the invoice all reach us second-hand, and the only thing that can
catch a discrepancy is arithmetic we do ourselves.

It is computed, never stored. Record a receipt and the match recomputes; a
stored verdict would go stale the moment a shipment cleared customs and
nobody would know which figure to trust.
"""

from decimal import Decimal

from django.db.models import Sum

from connect_labs.supply_chain.fulfilment.services.landed import costing_exclusion
from connect_labs.supply_chain.models import Payment, ReceiptLine
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Money, NotCosted, Quantity, Unconfirmed, decimal_string, unconfirmed

ZERO = Decimal("0")


def _received(contract):
    """Accepted quantity against this contract, per unit, summed in SQL."""
    rows = (
        ReceiptLine.objects.filter(receipt__contract=contract)
        .values("quantity_unit")
        .annotate(total=Sum("quantity_accepted"))
    )
    return {row["quantity_unit"]: (row["total"] or ZERO) for row in rows}


def _rejected(contract):
    """Refused quantity against this contract, per unit."""
    rows = (
        ReceiptLine.objects.filter(receipt__contract=contract)
        .values("quantity_unit")
        .annotate(total=Sum("quantity_rejected"))
    )
    return {row["quantity_unit"]: (row["total"] or ZERO) for row in rows}


# An order settled: nothing more is coming, so what was paid for and never
# delivered is owed back rather than awaited.
_CLOSED_OUT = ("received", "closed", "cancelled")


def _value_of(contract, quantity):
    """quantity at the contract's unit price, as Money, or Unconfirmed."""
    if contract.unit_price is None or not contract.unit_price_unit or contract.unit_price_unit == "per_lot_total":
        return unconfirmed("the contract's unit price cannot be applied to a quantity")
    restated = ledger.convert(quantity.amount, quantity.unit, contract.quantity_unit or quantity.unit, contract.item)
    if isinstance(restated, Unconfirmed):
        return restated
    return Money(contract.unit_price * restated.amount, contract.currency)


def _advance(contract, ordered, received, rejected, paid):
    """Awaiting delivery and recoverable, for an order paid in advance.

    Paid before the goods, the question is no longer "what is safe to pay"
    but "what is still coming, and what is owed back". Refused goods are owed
    back at once -- they were paid for and will not be accepted. Anything not
    delivered is awaited while the order is open, and owed back once it is
    closed out.
    """
    zero = Quantity(ZERO, ordered.unit)
    delivered = ledger.convert(received.amount, received.unit, ordered.unit, contract.item)
    refused = ledger.convert(rejected.amount, rejected.unit, ordered.unit, contract.item)
    if isinstance(delivered, Unconfirmed) or isinstance(refused, Unconfirmed):
        reason = delivered if isinstance(delivered, Unconfirmed) else refused
        return reason, reason
    not_delivered = max(ordered.amount - delivered.amount - refused.amount, ZERO)
    awaiting = Quantity(not_delivered, ordered.unit)
    owed_back = refused.amount
    if contract.status in _CLOSED_OUT:
        owed_back += not_delivered
        awaiting = zero
    recoverable = _value_of(contract, Quantity(owed_back, ordered.unit))
    if isinstance(recoverable, Money):
        # Never more than was paid: an order paid in part cannot be owed back in full.
        recoverable = Money(min(recoverable.amount, paid), contract.currency)
    return awaiting, recoverable


def _advance_state(contract, ordered, billed, paid):
    """unpaid | part_paid | paid -- what the payments recorded say, never the terms alone.

    Advance terms say when the order is to be paid, not that it was: an order
    on advance terms with nothing paid read as "paid in advance". Measured
    against the order's value where the price allows it, else against what
    has been billed.
    """
    if paid <= 0:
        return "unpaid"
    due = billed
    if isinstance(ordered, Quantity):
        value = _value_of(contract, ordered)
        if isinstance(value, Money):
            due = max(value.amount, billed)
    return "paid" if paid >= due else "part_paid"


def _invoiced(contract):
    rows = contract.invoices.exclude(status="rejected").values("quantity_unit").annotate(total=Sum("quantity_billed"))
    return {row["quantity_unit"]: (row["total"] or ZERO) for row in rows if row["quantity_unit"]}


def _in_order_unit(by_unit, item, unit):
    """One figure in the order's unit, or -- if that cannot be done -- in whatever it can."""
    if unit is not None:
        stated = ledger.collapse(by_unit, item, unit)
        if isinstance(stated, Quantity):
            return stated
    return ledger.collapse(by_unit, item, None)


def three_way_match(contract) -> dict:
    """Ordered / received / invoiced, and what is safe to pay.

    `payable_now` is the honest answer to "how much of this invoice should we
    release": the value of what actually arrived, never the value of what was
    billed. On the round this was designed against, a supplier invoiced for
    500 cartons while 200 of them sat at customs.
    """
    ordered = (
        Quantity(contract.quantity, contract.quantity_unit)
        if contract.quantity is not None and contract.quantity_unit
        else unconfirmed("the contract does not state a quantity and a unit")
    )
    # All three in the unit the order was placed in wherever the pack
    # specification allows it: "700 packet ordered, 9 carton received" is
    # arithmetic the reader should not have to do, and an empty receipt read as
    # "0 carton" beside an order in packets.
    in_order_unit = ordered.unit if not isinstance(ordered, Unconfirmed) else None
    received_by_unit = _received(contract)
    received = _in_unit(ledger.collapse(received_by_unit, contract.item, None), in_order_unit, contract.item)
    invoiced = _in_unit(ledger.collapse(_invoiced(contract), contract.item, None), in_order_unit, contract.item)
    unit = in_order_unit

    billed = contract.invoices.exclude(status="rejected").aggregate(total=Sum("amount"))["total"] or ZERO
    paid = Payment.objects.filter(invoice__contract=contract).aggregate(total=Sum("amount"))["total"] or ZERO

    shortfall = None
    status = "unknown"
    if not isinstance(ordered, Unconfirmed) and not any(received_by_unit.values()):
        # Nothing has arrived. Not "part received" -- which is what an empty
        # receipt list read as whenever the item named a unit, and "unknown"
        # whenever it did not -- and nothing to convert: all of it is still
        # to come.
        received = Quantity(ZERO, ordered.unit)
        shortfall = ordered
        status = "not_received"
    elif not isinstance(ordered, Unconfirmed) and not isinstance(received, Unconfirmed):
        restated = ledger.convert(received.amount, received.unit, ordered.unit, contract.item)
        if isinstance(restated, Unconfirmed):
            shortfall = restated
        else:
            outstanding = ordered.amount - restated.amount
            shortfall = Quantity(outstanding, ordered.unit)
            if outstanding > 0 and restated.amount == 0:
                # Nothing has arrived. "Part received" said something had.
                status = "not_received"
            elif outstanding > 0:
                status = "part_received"
            elif outstanding == 0:
                status = "fully_received"
            else:
                status = "over_received"

    over_invoiced = None
    if not isinstance(received, Unconfirmed) and not isinstance(invoiced, Unconfirmed):
        restated = ledger.convert(invoiced.amount, invoiced.unit, received.unit, contract.item)
        if isinstance(restated, Unconfirmed):
            over_invoiced = restated
        else:
            gap = restated.amount - received.amount
            over_invoiced = Quantity(gap, received.unit)
            if gap > 0:
                status = "over_invoiced"

    payable = _payable_now(contract, received, billed, paid)

    # What was refused on arrival, in the order's unit where it converts. Its
    # own figure because on an order paid in advance it is neither received
    # nor still to come -- it is owed back.
    rejected_by_unit = _rejected(contract)
    refused = _in_order_unit(rejected_by_unit, contract.item, unit) if any(rejected_by_unit.values()) else None
    if refused is None and unit is not None:
        refused = Quantity(ZERO, unit)

    awaiting = recoverable = advance_state = None
    if contract.payment_terms == "advance":
        advance_state = _advance_state(contract, ordered, billed, paid)
    if contract.payment_terms == "advance" and isinstance(ordered, Quantity) and isinstance(received, Quantity):
        # Billed and paid ahead of the goods is the agreement, not a
        # discrepancy: never "over invoiced" on quantity, and never "safe to
        # pay 0" -- what the reader needs is what is still coming and what
        # is owed back.
        rejected = refused
        if isinstance(rejected, Unconfirmed) or not any(rejected_by_unit.values()):
            rejected = Quantity(ZERO, ordered.unit)
        awaiting, recoverable = _advance(contract, ordered, received, rejected, paid)
        over_invoiced = None
        if not any(received_by_unit.values()):
            status = "paid_in_advance" if paid > 0 else "not_received"
        elif status == "over_invoiced":
            status = "part_received" if isinstance(shortfall, Quantity) and shortfall.amount > 0 else "fully_received"

    # A shortfall another order was placed to buy. Still stated -- covered is
    # not received, and the arithmetic stays visible -- but no longer open:
    # the status names the orders that cover it, so the short contract stops
    # reading as waiting on goods its supplier is never going to send. A
    # cancelled covering order covers nothing.
    covered_by = [
        {
            "contract_id": cover.pk,
            "reference": cover.reference,
            "supplier": {"id": cover.supplier_id, "name": cover.supplier.name},
            "quantity": decimal_string(cover.quantity) if cover.quantity is not None else None,
            "quantity_unit": cover.quantity_unit,
        }
        for cover in contract.shortfall_covered_by.exclude(status="cancelled")
        .select_related("supplier")
        .order_by("pk")
    ]
    if status == "part_received" and covered_by:
        status = "shortfall_covered"

    still_to_arrive = _still_to_arrive(contract, ordered, received, refused)

    return {
        "contract_id": contract.pk,
        "currency": contract.currency,
        "ordered": ordered,
        "received": received,
        "invoiced": invoiced,
        "outstanding": shortfall,
        "still_to_arrive": still_to_arrive,
        "over_invoiced": over_invoiced,
        "billed_amount": Money(billed, contract.currency),
        "paid_amount": Money(paid, contract.currency),
        "payable_now": payable,
        "payment_terms": contract.payment_terms,
        "awaiting_delivery": awaiting,
        "advance_state": advance_state,
        "refused": refused,
        "recoverable": recoverable,
        "covered_by": covered_by,
        "status": status,
        "matches": status == "fully_received" and _is_zero(over_invoiced),
    }


def _still_to_arrive(contract, ordered, received, refused):
    """What has not arrived at all, once refused goods are set apart.

    `outstanding` is ordered less accepted, and so counts refused goods as if
    they were still on their way: 118 accepted and 2 refused of 120 read
    "Still outstanding 2" beside a goods-received note refusing exactly those
    two (the dispenser import). Refused goods DID arrive; whether they are
    replaced is a separate question the record does not answer. None when
    nothing was refused, or when the figures cannot be stated in one unit --
    the card then shows `outstanding` as it always has.
    """
    if not all(isinstance(q, Quantity) for q in (ordered, received, refused)) or refused.amount == 0:
        return None
    accepted = ledger.convert(received.amount, received.unit, ordered.unit, contract.item)
    set_apart = ledger.convert(refused.amount, refused.unit, ordered.unit, contract.item)
    if isinstance(accepted, Unconfirmed) or isinstance(set_apart, Unconfirmed):
        return None
    return Quantity(max(ordered.amount - accepted.amount - set_apart.amount, ZERO), ordered.unit)


def _in_unit(figure, unit, item):
    """`figure` restated in `unit` when that can be done exactly, else unchanged.

    Unchanged rather than Unconfirmed: a quantity in its own unit is still a
    true figure, and whether the order and receipt units reconcile is the
    match's question to answer below, not this helper's.
    """
    if unit is None or not isinstance(figure, Quantity) or figure.unit == unit:
        return figure
    if figure.amount == ZERO:
        return Quantity(ZERO, unit)
    restated = ledger.convert(figure.amount, figure.unit, unit, item)
    return restated if isinstance(restated, Quantity) else figure


def _is_zero(figure) -> bool:
    return isinstance(figure, Quantity) and figure.amount == 0


def _payable_now(contract, received, billed, paid):
    """The value of what arrived, less what has already been paid.

    Refuses rather than guessing when the unit price cannot be applied to the
    received quantity: paying against a number nobody can derive is the
    failure this whole control exists to prevent.

    Nothing is payable on goods nobody bought, and that is a statement rather
    than a missing price -- so a donation says why, not "unconfirmed".
    """
    excluded = costing_exclusion(contract)
    if excluded is not None:
        return NotCosted(excluded)
    if isinstance(received, Unconfirmed):
        return received
    if contract.unit_price is None or not contract.unit_price_unit:
        return unconfirmed("the contract does not state a unit price, so what arrived cannot be valued")
    if contract.unit_price_unit == "per_lot_total":
        return unconfirmed(
            "the contract is priced as a lot total, so a part delivery cannot be valued "
            "line by line -- settle it against the whole lot"
        )

    target_unit = contract.quantity_unit or received.unit
    restated = ledger.convert(received.amount, received.unit, target_unit, contract.item)
    if isinstance(restated, Unconfirmed):
        return restated
    value = contract.unit_price * restated.amount
    return Money(max(value - paid, ZERO), contract.currency)
