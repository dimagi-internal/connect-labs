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
from connect_labs.supply_chain.values import day_text, money_digits, quantity_phrase


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
    if splits:
        lines += [
            "",
            f"The goods are for {len(splits)} partner organisation{'s' if len(splits) != 1 else ''}, "
            "each with its own "
            "quantity and delivery address. Please coordinate with each directly (they are copied here):",
        ]
        lines += [_partner_line(tender, s) for s in splits]
        for split in splits:
            address = clean_email(split.get("receiver_contact"))
            if address and address not in cc and address != recipients(supplier).to:
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
    who = split.get("receiver_name") or f"{split['org_name']} team"
    quantity = quantity_phrase(split["quantity"], split.get("quantity_unit"))
    total = _line_total(quote, commodity, split)
    lines = [
        f"Dear {who},",
        "",
        f"{supplier.name} has been selected to supply {commodity.name}. This is what {split['org_name']} needs.",
        "",
        f"Supplier: {supplier.name}" + (f"; main contact {contact}" if contact else ""),
        f"Winning {_quote_name(quote, commodity, quote.item)}"
        + (f", {quote.incoterm}" if quote.incoterm else "")
        + (f", lead time {quote.lead_time_days} days" if quote.lead_time_days else "")
        + (f", valid until {day_text(quote.validity_until)}" if quote.validity_until else "")
        + (f", supplier reference {quote.supplier_reference}" if quote.supplier_reference else ""),
        f"Your part: {quantity}" + (f" at that price, {total}" if total else ""),
        "Delivery: "
        + (_place(tender, split) or "to be confirmed")
        + (f"; receiver {split['receiver_name']}" if split.get("receiver_name") else ""),
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
    lines += _sign_off(sender)
    return Draft(
        subject=f"{commodity.name}: supplier selected, your part is {quantity}",
        text="\n".join(lines),
        to=clean_email(split.get("receiver_contact")) or None,
        cc=(),
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
