"""The "you've been selected" email, a handover for each partner, and the trade item the winning quote implies.

Sophie Feintuch's RUTF feedback, "Selection and handover" and the Catalogue note on trade items: at award
the programme tells the winning supplier it has been chosen (naming the partners it will coordinate
with, copying them), gives each partner what it needs to buy or receive (the supplier's main contact,
the winning quotation, that partner's quantity and price, the delivery details), and offers to add the
manufacturer's trade item from what the supplier stated. Nothing is sent: these are drafts a person
copies into their own mailbox, like every other email here.
"""

from __future__ import annotations

from decimal import Decimal

from connect_labs.supply_chain.procurement.services.contacts import clean_email, greeting, recipients, split_names
from connect_labs.supply_chain.procurement.services.render import Draft, Sender, _priced_per, _sign_off
from connect_labs.supply_chain.values import day_text, money_digits, possessive, quantity_phrase


def _place(tender, split) -> str:
    place = next((p for p in tender.delivery_points or [] if p.get("key") == split.get("delivery_point_key")), {})
    return ", ".join(x for x in (place.get("name"), place.get("city")) if x)


def _quote_name(quote, commodity, item=None) -> str:
    """ "your quotation of 10 Sep 2026, USD 52.40 per carton" -- the offer as the supplier made it."""
    dated = f" of {day_text(quote.received_on)}" if quote.received_on else ""
    price = ""
    if quote.as_quoted_amount is not None:
        per = _priced_per(quote, commodity, item)
        price = f", {quote.as_quoted_currency or 'USD'} {money_digits(quote.as_quoted_amount)}" + (
            f" {per}" if per else ""
        )
    return f"quotation{dated}{price}"


def _line_total(quote, commodity, split) -> str:
    """ "USD 20,960.00" when the price is per pack or per unit and the split is counted in that same unit."""
    if quote.as_quoted_amount is None:
        return ""
    counted = {"per_pack": commodity.pack_unit, "per_base_unit": commodity.base_unit}.get(quote.as_quoted_unit)
    if not counted or (split.get("quantity_unit") or "") != counted:
        return ""
    total = Decimal(str(split["quantity"])) * Decimal(str(quote.as_quoted_amount))
    return f"{quote.as_quoted_currency or 'USD'} {money_digits(total)}"


def _partner_lead(tender, split) -> dict:
    """The partner's lead -- {"name", "email", "copy_to": [addresses]} -- from its Partners card, else nobody.

    Sophie Feintuch's round-2 walk, "Award emails": every handover said "To: no address on file" while the
    selection email claimed the partners were copied. The main contact lives on the Partners card
    (`partners.services.main_contact`); a partner with none recorded has no lead, and nothing is claimed.
    """
    try:
        from connect_labs.supply_chain.partners.services import main_contact
    except ImportError:  # the partner profile is not installed on this build
        return {"name": "", "email": "", "copy_to": []}
    found = main_contact(tender.program_id, split.get("org_id"))
    return {
        "name": (found.get("name") or "").strip(),
        "email": clean_email(found.get("email")),
        "copy_to": [
            a for a in (clean_email(c.get("email")) for c in found.get("copy_to") or [] if isinstance(c, dict)) if a
        ],
    }


def _partner_addresses(tender, split) -> list[str]:
    """Everyone at the partner an email should reach: its lead, the people to copy, and the receiver."""
    lead = _partner_lead(tender, split)
    out = []
    for address in (lead["email"], *lead["copy_to"], clean_email(split.get("receiver_contact"))):
        if address and address not in out:
            out.append(address)
    return out


def _expected_on(award, tender, split, quote):
    """(day, firm): when this partner's goods are expected -- the order's shipment if one is recorded, else
    the decision day plus the quote's lead time (not firm)."""
    from datetime import timedelta

    from connect_labs.supply_chain.models import Contract, Shipment

    contract = (
        Contract.objects.filter(tender=tender, award=award)
        .filter(partner_org_id=split.get("org_id"))
        .exclude(status="cancelled")
        .first()
    )
    if contract is not None:
        shipment = (
            Shipment.objects.filter(contract=contract, expected_on__isnull=False).order_by("-expected_on").first()
        )
        if shipment is not None:
            return shipment.expected_on, True
    if award.decided_on and quote.lead_time_days:
        return award.decided_on + timedelta(days=quote.lead_time_days), False
    return None, False


# What a programme asks of the goods unless its tender says otherwise (Sophie Feintuch, round 2).
MIN_SHELF_LIFE_MONTHS = 18


def _copied_phrase(splits, reached) -> str:
    """" (they are copied here)" only for partners with an address in play; the rest are named, not claimed."""
    copied = [s["org_name"] for s in splits if reached[id(s)]]
    if len(copied) == len(splits):
        return " (they are copied here)"
    if not copied:
        return " (we will introduce you to each by email)"
    return f" ({', '.join(copied)} {'is' if len(copied) == 1 else 'are'} copied here; we will introduce you to the others)"


def _partner_line(tender, split) -> str:
    where = _place(tender, split)
    who = split.get("receiver_name") or ""
    contact = split.get("receiver_contact") or ""
    line = f"- {split['org_name']}: {quantity_phrase(split['quantity'], split.get('quantity_unit'))}"
    if where:
        line += f", delivered to {where}"
    if who or contact:
        line += f" (receiver: {', '.join(x for x in (who, contact) if x)})"
    line += (
        ", buying on our quotation and invoicing us"
        if split.get("buying_model") == "partner_buys"
        else ", ordered and paid by us"
    )
    return line


def render_selection(award, tender, quote, supplier, commodity, splits, *, sender: Sender | None = None) -> Draft:
    """The email telling the winning supplier it was selected; copies the partners it will coordinate with."""
    named = _quote_name(quote, commodity, quote.item)
    lines = [
        greeting(supplier),
        "",
        f"Thank you for your {named}. We are pleased to tell you that it has been selected for {commodity.name}.",
    ]
    cc = list(recipients(supplier).cc)
    months = tender.shelf_life_months_minimum or MIN_SHELF_LIFE_MONTHS
    lines += [
        "",
        "Please send us a certificate of analysis for each batch, and confirm that the shelf life of the goods "
        f"will be at least {months} months when they arrive.",
    ]
    if splits:
        reached = {id(s): _partner_addresses(tender, s) for s in splits}
        lines += [
            "",
            f"The goods are for {len(splits)} partner organisation{'s' if len(splits) != 1 else ''}, "
            "each with its own quantity and delivery address. Please coordinate with each directly"
            + _copied_phrase(splits, reached)
            + ":",
        ]
        lines += [_partner_line(tender, s) for s in splits]
        for split in splits:
            for address in reached[id(split)]:
                if address not in cc and address != recipients(supplier).to:
                    cc.append(address)
    lines += [
        "",
        "Please confirm that the quoted price and"
        + (f" the lead time of {quote.lead_time_days} days" if quote.lead_time_days else " the lead time")
        + (" hold for these deliveries" if splits else " hold")
        + ", and send the pro-forma invoice"
        + (" for each partner" if len(splits) > 1 else "")
        + ".",
    ]
    lines += _sign_off(sender)
    subject = f"Your {named.split(',')[0]} has been selected: {commodity.name}"
    return Draft(subject=subject, text="\n".join(lines), to=recipients(supplier).to, cc=tuple(cc))


def render_handover(award, tender, quote, supplier, commodity, split, *, sender: Sender | None = None) -> Draft:
    """What one partner needs: the supplier's main contact, the winning quotation, its quantity and price, delivery."""
    people = recipients(supplier)
    main = next(
        (c for c in supplier.contacts or [] if isinstance(c, dict) and clean_email(c.get("email")) == people.to), {}
    )
    contact = ", ".join(
        x for x in (", ".join(split_names(main.get("name"))), people.to, (main.get("phone") or "").strip()) if x
    )
    lead = _partner_lead(tender, split)
    receiver = clean_email(split.get("receiver_contact"))
    # Addressed to the partner's lead, with the receiver copied; with no lead on file, to the receiver alone.
    to = lead["email"] or receiver or None
    cc = [a for a in (receiver, *lead["copy_to"]) if a and a != to]
    who = lead["name"] or split.get("receiver_name") or f"{split['org_name']} team"
    quantity = quantity_phrase(split["quantity"], split.get("quantity_unit"))
    total = _line_total(quote, commodity, split)
    expected, firm = _expected_on(award, tender, split, quote)
    phone = (main.get("phone") or "").strip()
    lines = [
        f"Dear {who},",
        "",
        f"{supplier.name} has been selected to supply {commodity.name}. {possessive(split['org_name'])} share is below.",
        "",
        f"Supplier: {supplier.name}" + (f"; main contact {contact}" if contact else ""),
        *([f"Supplier phone: {phone}"] if phone else []),
        f"Winning {_quote_name(quote, commodity, quote.item)}"
        + (f", {quote.incoterm}" if quote.incoterm else "")
        + (f", lead time {quote.lead_time_days} days" if quote.lead_time_days else "")
        + (f", valid until {day_text(quote.validity_until)}" if quote.validity_until else "")
        + (f", supplier reference {quote.supplier_reference}" if quote.supplier_reference else ""),
        f"Your part: {quantity}" + (f" at that price, {total}" if total else ""),
        "Delivery: "
        + (_place(tender, split) or "to be confirmed")
        + (f"; receiver {split['receiver_name']}" if split.get("receiver_name") else ""),
        *(
            [f"Expected: {day_text(expected)}" if firm else f"Expected: about {day_text(expected)} (the quoted lead time)"]
            if expected
            else []
        ),
        "",
    ]
    if split.get("buying_model") == "partner_buys":
        lines.append(
            "Please place your order with the supplier at this quotation, quoting the reference above, and send us "
            "your invoice for the reimbursement once you have paid. Tell us when the order is placed."
        )
    else:
        lines.append(
            "We will place and pay for this order and tell you when it ships. "
            "The supplier will contact you about delivery."
        )
    lines += [
        "",
        "When it arrives: count the cartons, check each batch number and expiry date against the delivery note, "
        "and confirm receipt to us.",
    ]
    lines += _sign_off(sender)
    return Draft(
        subject=f"{commodity.name}: supplier selected, your part is {quantity}",
        text="\n".join(lines),
        to=to,
        cc=tuple(cc),
    )


def item_from_quote(quote, supplier, commodity) -> dict:
    """The trade item the winning quote describes, pre-filled with what the supplier stated and nothing it did not.

    Only stated facts are carried: pack size and sachet weight when the quote states them, its shelf life,
    its stated specification. The supplier's name is offered as the manufacturer -- check it: a distributor
    is not the maker. The code (sku) is left for the person, who has the manufacturer's own.
    """
    out = {"commodity": commodity.pk, "manufacturer": supplier.name, "name": f"{commodity.name}, {supplier.name}"}
    if quote.base_per_pack_stated:
        out["base_per_pack"] = quote.base_per_pack_stated
    if quote.base_unit_grams_stated:
        out["base_unit_grams"] = quote.base_unit_grams_stated
    if quote.shelf_life_months_stated:
        out["shelf_life_months"] = quote.shelf_life_months_stated
    return out
