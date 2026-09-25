"""Render the text a human sends to a supplier.

Phase 1a produces text to paste into a mail client: a supplier who ignores
email will ignore a portal too, and the credibility is in the sender.
Phase 1c sends the same text over SES.
"""

from connect_labs.supply_chain.models import Commodity, Quote, Supplier, Tender
from connect_labs.supply_chain.procurement.services.questions import SUPPLIER, initial_request_facts, missing_facts
from connect_labs.supply_chain.values import destination_phrase, quantity_phrase


def _numbered(facts) -> str:
    return "\n".join(f"{index}. {fact.question}" for index, fact in enumerate(facts, start=1))


def render_initial_request(
    commodity: Commodity,
    tender: Tender,
    supplier: Supplier,
) -> str:
    quantity = tender.quantity_for(commodity.slug)
    quantity_text = quantity_phrase(quantity[0], quantity[1]) if quantity else "the quantity below"
    incoterm = (tender.delivery_point or {}).get("incoterm_requested")

    lines = [
        f"Dear {supplier.name},",
        "",
        f"We are seeking a quotation for {quantity_text} of "
        f"{commodity.name or commodity.slug}, delivered to {destination_phrase(tender.delivery_point)}"
        + (f" on {incoterm} terms" if incoterm else "")
        + ".",
    ]
    if tender.notes_to_supplier:
        lines += ["", tender.notes_to_supplier]
    lines += [
        "",
        "So that we can compare offers on the same basis, please answer each of the following:",
        "",
        _numbered(initial_request_facts(commodity, tender)),
    ]
    if tender.response_deadline:
        lines += ["", f"We would be grateful for a reply by {tender.response_deadline}."]
    lines += ["", "With thanks,"]
    return "\n".join(lines)


def render_followup(
    quote: Quote,
    commodity: Commodity,
    tender: Tender,
    supplier: Supplier,
    item=None,
) -> str:
    """Ask only for what is still missing.

    `item` is threaded to missing_facts so a supplier who already identified
    their trade item is not asked for its pack configuration again.
    """
    facts = [fact for fact in missing_facts(quote, commodity, tender, item=item) if fact.audience == SUPPLIER]
    if not facts:
        return (
            f"Dear {supplier.name},\n\n" "Thank you — your quotation is complete and there is nothing outstanding.\n"
        )
    return "\n".join(
        [
            f"Dear {supplier.name},",
            "",
            "Thank you for your quotation. To compare it against the other offers we " "need a little more detail:",
            "",
            _numbered(facts),
            "",
            "With thanks,",
        ]
    )
