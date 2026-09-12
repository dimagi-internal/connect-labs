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

from connect_labs.supply_chain.models import Payment, ReceiptLine
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Money, Quantity, Unconfirmed, unconfirmed

ZERO = Decimal("0")


def _received(contract):
    """Accepted quantity against this contract, per unit, summed in SQL."""
    rows = (
        ReceiptLine.objects.filter(receipt__contract=contract)
        .values("quantity_unit")
        .annotate(total=Sum("quantity_accepted"))
    )
    return {row["quantity_unit"]: (row["total"] or ZERO) for row in rows}


def _invoiced(contract):
    rows = contract.invoices.exclude(status="rejected").values("quantity_unit").annotate(total=Sum("quantity_billed"))
    return {row["quantity_unit"]: (row["total"] or ZERO) for row in rows if row["quantity_unit"]}


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
    received = ledger.collapse(_received(contract), contract.item, None)
    invoiced = ledger.collapse(_invoiced(contract), contract.item, None)

    billed = contract.invoices.exclude(status="rejected").aggregate(total=Sum("amount"))["total"] or ZERO
    paid = Payment.objects.filter(invoice__contract=contract).aggregate(total=Sum("amount"))["total"] or ZERO

    shortfall = None
    status = "unknown"
    if not isinstance(ordered, Unconfirmed) and not isinstance(received, Unconfirmed):
        restated = ledger.convert(received.amount, received.unit, ordered.unit, contract.item)
        if isinstance(restated, Unconfirmed):
            shortfall = restated
        else:
            outstanding = ordered.amount - restated.amount
            shortfall = Quantity(outstanding, ordered.unit)
            if outstanding > 0:
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

    return {
        "contract_id": contract.pk,
        "currency": contract.currency,
        "ordered": ordered,
        "received": received,
        "invoiced": invoiced,
        "outstanding": shortfall,
        "over_invoiced": over_invoiced,
        "billed_amount": Money(billed, contract.currency),
        "paid_amount": Money(paid, contract.currency),
        "payable_now": payable,
        "status": status,
        "matches": status == "fully_received" and _is_zero(over_invoiced),
    }


def _is_zero(figure) -> bool:
    return isinstance(figure, Quantity) and figure.amount == 0


def _payable_now(contract, received, billed, paid):
    """The value of what arrived, less what has already been paid.

    Refuses rather than guessing when the unit price cannot be applied to the
    received quantity: paying against a number nobody can derive is the
    failure this whole control exists to prevent.
    """
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
