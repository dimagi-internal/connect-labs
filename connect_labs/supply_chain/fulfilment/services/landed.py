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
not pedantry -- on the tender this was designed against, the entire price
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
        item = {
            "id": charge.pk,
            "shipment_id": charge.shipment_id,
            "kind": charge.kind,
            "payee": {"id": charge.payee_org_id, "name": charge.payee_org.name},
            "amount": Money(charge.amount, charge.currency),
            "paid_on": charge.paid_on,
            # Set only when the charge was restated into the contract's
            # currency, so a reader can check the total line by line.
            "fx_rate_to_usd": None,
            "restated": None,
        }
        items.append(item)
        if charge.currency == contract.currency:
            total += charge.amount
        elif contract.currency == "USD" and charge.fx_rate_to_usd:
            restated = charge.amount * charge.fx_rate_to_usd
            item["fx_rate_to_usd"] = charge.fx_rate_to_usd
            item["restated"] = Money(restated, contract.currency)
            total += restated
        else:
            reasons.append(
                f"a {charge.kind.replace('_', ' ')} charge of {charge.amount} {charge.currency} cannot be "
                f"added to a {contract.currency} total without an exchange rate"
            )
    return items, (unconfirmed(*reasons) if reasons else Money(total, contract.currency))


def _we_import(contract) -> bool:
    """Whether clearing & forwarding is ours: a buyer of ours imports, by the order's Incoterm.

    The same rule the tender comparison prices quotes by (pricing.clearing_applies),
    so an order cannot cost the import differently from the quote it came from.
    An agency imports under its own status, inside its catalogue price.
    """
    from connect_labs.supply_chain.procurement.services.pricing import clearing_applies

    if contract.buyer_of_record not in ("programme_org", "partner_org"):
        return False
    return clearing_applies(contract)


def clearing(contract, charge_items):
    """Clearing & forwarding on an order we import, and where the figure came from, or None.

    Three sources, in order: what was paid (`clearing` charges -- already in
    the charges total, so not added again); else the source tender's clearing
    estimate times the order's quantity; else nothing recorded, said as such
    rather than as a zero. {"source": "paid" | "estimate" | "not_recorded",
    "amount": Money | Unconfirmed}.
    """
    if not _we_import(contract):
        return None
    paid = [c for c in charge_items if c["kind"] == "clearing"]
    if paid:
        total = ZERO
        for c in paid:
            figure = c.get("restated") or c["amount"]
            if figure.currency != contract.currency:
                return {"source": "paid", "amount": unconfirmed("a clearing charge cannot be restated")}
            total += figure.amount
        return {"source": "paid", "amount": Money(total, contract.currency)}
    tender = contract.tender if contract.tender_id else None
    per_unit = getattr(tender, "clearing_estimate_per_unit", None)
    if per_unit is None:
        return {"source": "not_recorded", "amount": unconfirmed("no clearing & forwarding estimate is recorded")}
    line = tender.quantity_for(contract.commodity.slug) if contract.commodity_id else None
    unit = line[1] if line else None
    if contract.quantity is None or (unit and contract.quantity_unit and unit != contract.quantity_unit):
        return {
            "source": "estimate",
            "amount": unconfirmed(f"the clearing estimate is per {unit or 'unit'} and the order is not counted in it"),
        }
    return {"source": "estimate", "amount": Money(Decimal(per_unit) * contract.quantity, contract.currency)}


def rests_on_relief(contract) -> bool:
    """Whether a priced order's import duty is nil by a relief rather than by its price.

    Claimed on the order, or entered as excluded at 0 (a waiver). Not duty
    inside the price, nor an agency's catalogue price, where nil is no relief.
    """
    if contract.consideration != "priced" or contract.buyer_of_record == "agency":
        return False
    if contract.duties_basis == "included":
        return False
    if contract.duty_relief_claimed:
        return True
    return contract.duties_basis == "excluded" and contract.duties_amount is not None and contract.duties_amount == 0


def relief_on_file(contract) -> bool:
    """Whether a duty exemption is on file: named on the order, or attached to it or a shipment of it."""
    from connect_labs.supply_chain.procurement.services.pricing import duty_exemption_on_file

    return duty_exemption_on_file(contract=contract)


def relief_unevidenced(contract) -> bool:
    """The order's duty rests on a relief that no document on file shows (pricing.relief_unevidenced)."""
    from connect_labs.supply_chain.procurement.services import pricing

    return pricing.relief_unevidenced(rests_on_relief(contract), contract=contract)


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
            "clearing": None,
        }

    goods = _line_total(contract)
    freight = _extra(contract.freight_basis, contract.freight_amount, "freight", contract.currency)
    duty = _tax_line(contract, contract.duties_basis, contract.duties_amount, "import duty")
    vat = _tax_line(contract, contract.vat_basis, contract.vat_amount, "VAT")

    blocked = merge(goods, freight, duty, vat, charges_total)
    clearing_line = clearing(contract, charge_items)
    # An estimate is added; a paid clearing charge is already in the charges.
    # One not recorded (or not countable) adds nothing and is marked beside the
    # total, as the comparison marks "excl. clearing" -- never a silent zero.
    estimate = (
        clearing_line["amount"].amount
        if clearing_line and clearing_line["source"] == "estimate" and isinstance(clearing_line["amount"], Money)
        else ZERO
    )
    total = blocked or Money(
        goods.amount + freight.amount + duty.amount + vat.amount + charges_total.amount + estimate,
        contract.currency,
    )
    return {
        "clearing": clearing_line,
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
