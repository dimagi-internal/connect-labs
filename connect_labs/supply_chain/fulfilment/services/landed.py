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

from connect_labs.supply_chain.values import Money, NotCosted, Unconfirmed, merge, unconfirmed

ZERO = Decimal("0")

# What a landed cost says instead of a number when nothing was bought. The
# same words on every surface -- the order page, the API, an agent's answer --
# so "not purchased" cannot drift into three phrasings of one fact.
NOT_COSTED = {
    "in_kind": "not purchased (in kind)",
    "bundled": "bundled in setup fee",
}


def costing_exclusion(contract) -> str | None:
    """Why this contract has no cost, or None when it is a purchase.

    The one question anything aggregating cost has to ask first: a cost
    library, a per-unit average or a comparison that counted a donation as a
    purchase would either divide by a price that does not exist or quietly
    drop the row. Leaving it out is right; leaving it out WITHOUT saying why
    is how a total silently stops meaning what it says.
    """
    return NOT_COSTED.get(contract.consideration)


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


def _extra(basis, amount, label, currency):
    """A cost line stated as a basis plus, sometimes, an amount.

    `included` means it is already inside the price, so it adds nothing.
    `excluded` with an amount adds it. `excluded` with no amount, or
    `not_specified`, is the honest gap -- and saying so is what turns it into
    a question somebody can answer.

    Every line is in the contract's currency. Money defaults to USD, so a
    line built without one read "Freight 0 USD" under "Goods 612000 NGN".
    """
    if basis == "included":
        return Money(ZERO, currency)
    if basis == "excluded":
        if amount is None:
            return unconfirmed(f"{label} is excluded but no amount was given")
        return Money(amount, currency)
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
        return Money(ZERO, contract.currency)

    if buyer == "partner_org" and contract.duty_relief_claimed:
        if not contract.duty_relief_document_id:
            return unconfirmed(
                f"{label} is claimed to be relieved because the buyer is resident, but no "
                "exemption document is attached -- the relief is asserted, not evidenced"
            )
        return Money(ZERO, contract.currency)

    return _extra(basis, amount, label, contract.currency)


def charges(contract):
    """What was paid to land this contract's shipments, itemised, and its total.

    Paid to customs, a clearing agent or a haulier -- never to the supplier --
    so it is neither the price nor the freight line. Each charge is kept in
    the currency it was paid in; the total is in the contract's currency, and
    a charge that cannot be restated into it (a naira fee with no rate
    against a dollar contract) makes the total Unconfirmed, naming the
    charge, rather than adding naira to dollars.
    """
    from connect_labs.supply_chain.models import Charge

    items = []
    total = ZERO
    reasons = []
    for charge in Charge.objects.filter(shipment__contract=contract).select_related("payee_org"):
        items.append(
            {
                "id": charge.pk,
                "shipment_id": charge.shipment_id,
                "kind": charge.kind,
                "payee": {"id": charge.payee_org_id, "name": charge.payee_org.name},
                "amount": Money(charge.amount, charge.currency),
                "paid_on": charge.paid_on,
            }
        )
        if charge.currency == contract.currency:
            total += charge.amount
        elif contract.currency == "USD" and charge.fx_rate_to_usd:
            total += charge.amount * charge.fx_rate_to_usd
        else:
            reasons.append(
                f"a {charge.kind.replace('_', ' ')} charge of {charge.amount} {charge.currency} cannot be "
                f"added to a {contract.currency} total without an exchange rate"
            )
    return items, (unconfirmed(*reasons) if reasons else Money(total, contract.currency))


def landed_total(contract):
    """The all-in cost of this contract, or why it cannot be computed.

    Every component is returned alongside the total so a reader can see which
    line blocked it, and `buyer_of_record` travels with the answer because
    the answer is only true for that buyer.

    A contract that is not a purchase -- a donation, or goods paid for out of
    a setup fee -- has no landed cost to compute, and says so as a
    `NotCosted` rather than an `Unconfirmed`: nothing is missing, so there is
    nothing for anyone to chase.
    """
    charge_items, charges_total = charges(contract)
    excluded = costing_exclusion(contract)
    if excluded is not None:
        # The goods have no cost, but landing them did: customs on a donated
        # dispenser is real money we paid. Itemised beside the statement, and
        # deliberately NOT turned into a landed total, which would read as
        # the price of the goods.
        nothing = NotCosted(excluded)
        return {
            "charges": charge_items,
            "charges_total": charges_total,
            "buyer_of_record": contract.buyer_of_record,
            "currency": contract.currency,
            "consideration": contract.consideration,
            "goods": nothing,
            "freight": nothing,
            "duty": nothing,
            "vat": nothing,
            "landed_total": nothing,
            "duty_relief_claimed": contract.duty_relief_claimed,
            "duty_relief_evidenced": contract.duty_relief_evidenced,
        }

    goods = _line_total(contract)
    freight = _extra(contract.freight_basis, contract.freight_amount, "freight", contract.currency)
    duty = _tax_line(contract, contract.duties_basis, contract.duties_amount, "import duty")
    vat = _tax_line(contract, contract.vat_basis, contract.vat_amount, "VAT")

    blocked = merge(goods, freight, duty, vat, charges_total)
    total = blocked or Money(
        goods.amount + freight.amount + duty.amount + vat.amount + charges_total.amount, contract.currency
    )
    return {
        "charges": charge_items,
        "charges_total": charges_total,
        "buyer_of_record": contract.buyer_of_record,
        "currency": contract.currency,
        "consideration": contract.consideration,
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
    return not isinstance(figure, (Unconfirmed, NotCosted))
