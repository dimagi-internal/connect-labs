"""Landed cost of a contract, which depends on who is buying.

The rule this module exists to enforce: **a landed total is never presented
without naming the buyer it assumed.** Import duty and VAT fall on the
importer, so the same goods at the same price from the same supplier cost
different amounts depending on whether we, a local partner, or an agency is
the buyer of record. A number computed against a default buyer carries an
invisible assumption, and an award justified by it can invert when the
assumption turns out to be wrong.

The second rule: **a claimed relief is not a relief.** `duty_relief_claimed`
with no exemption document attached yields `Unconfirmed`, not zero. That is
not pedantry -- on the round this was designed against, the entire price
advantage of routing an order through a local partner rested on a relief
nobody had evidenced.
"""

from decimal import Decimal

from connect_labs.supply_chain.values import Money, Unconfirmed, merge, unconfirmed

ZERO = Decimal("0")


def _line_total(contract):
    """Goods value: unit price times quantity, in the price's own unit.

    Refused rather than approximated when the two disagree about units --
    a per-sachet price against a carton quantity needs the pack
    specification, and inventing one here is how a 4% error looks
    authoritative.
    """
    if contract.unit_price is None:
        return unconfirmed("the contract does not state a unit price")
    if contract.quantity is None:
        return unconfirmed("the contract does not state a quantity")

    unit = contract.unit_price_unit or ""
    if unit == "per_lot_total":
        return Money(contract.unit_price, contract.currency)
    if unit in ("per_pack", "per_base_unit", "per_metric_tonne"):
        return Money(contract.unit_price * contract.quantity, contract.currency)
    return unconfirmed(
        "the contract does not say what its unit price is per, so it cannot be " "multiplied out to a total"
    )


def _extra(basis, amount, label):
    """A cost line stated as a basis plus, sometimes, an amount.

    `included` means it is already inside the price, so it adds nothing.
    `excluded` with an amount adds it. `excluded` with no amount, or
    `not_specified`, is the honest gap -- and saying so is what turns it into
    a question somebody can answer.
    """
    if basis == "included":
        return Money(ZERO)
    if basis == "excluded":
        if amount is None:
            return unconfirmed(f"{label} is excluded but no amount was given")
        return Money(amount)
    return unconfirmed(f"the contract does not say whether {label} is included")


def _tax_line(contract, basis, amount, label):
    """Duty or VAT, resolved against the buyer of record.

    Three cases, and none of them is a default:

      programme_org  a foreign buyer. Duty and VAT fall due on arrival and
                     cannot be reclaimed, so the stated basis governs.
      partner_org    a resident buyer, which is usually WHY this route was
                     chosen. A claimed relief counts only with a document.
      agency         the agency imports under its own status and quotes a
                     catalogue price with everything inside it.
    """
    buyer = contract.buyer_of_record
    if not buyer:
        return unconfirmed(
            f"{label} cannot be resolved because the contract does not say who the buyer of "
            "record is -- and it falls on whoever imports"
        )

    if buyer == "agency":
        return Money(ZERO)

    if buyer == "partner_org" and contract.duty_relief_claimed:
        if not contract.duty_relief_document_id:
            return unconfirmed(
                f"{label} is claimed to be relieved because the buyer is resident, but no "
                "exemption document is attached -- the relief is asserted, not evidenced"
            )
        return Money(ZERO)

    return _extra(basis, amount, label)


def landed_total(contract):
    """The all-in cost of this contract, or why it cannot be computed.

    Every component is returned alongside the total so a reader can see which
    line blocked it, and `buyer_of_record` travels with the answer because
    the answer is only true for that buyer.
    """
    goods = _line_total(contract)
    freight = _extra(contract.freight_basis, contract.freight_amount, "freight")
    duty = _tax_line(contract, contract.duties_basis, contract.duties_amount, "import duty")
    vat = _tax_line(contract, contract.vat_basis, contract.vat_amount, "VAT")

    blocked = merge(goods, freight, duty, vat)
    total = blocked or Money(goods.amount + freight.amount + duty.amount + vat.amount, contract.currency)
    return {
        "buyer_of_record": contract.buyer_of_record,
        "currency": contract.currency,
        "goods": goods,
        "freight": freight,
        "duty": duty,
        "vat": vat,
        "landed_total": total,
        "duty_relief_claimed": contract.duty_relief_claimed,
        "duty_relief_evidenced": contract.duty_relief_evidenced,
    }


def compare_buyers(contract):
    """The same contract costed under each buyer of record.

    What the choice is actually worth, and what it rests on. Produced by
    costing copies rather than by a second formula, so the comparison cannot
    drift from the real calculation.
    """
    out = {}
    for buyer in ("programme_org", "partner_org", "agency"):
        # A shallow in-memory copy: never saved, so the stored contract keeps
        # the buyer it actually has.
        contract.buyer_of_record, original = buyer, contract.buyer_of_record
        try:
            costed = landed_total(contract)
        finally:
            contract.buyer_of_record = original
        out[buyer] = costed["landed_total"]
    return out


def is_confirmed(figure) -> bool:
    return not isinstance(figure, Unconfirmed)
