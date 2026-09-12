"""Render the text a human sends to a supplier.

Phase 1a produces text to paste into a mail client: a supplier who ignores
email will ignore a portal too, and the credibility is in the sender.
Phase 1c sends the same text over SES.
"""

from connect_labs.supply_chain.models import CommodityRecord, QuoteRecord, RoundRecord, SupplierRecord
from connect_labs.supply_chain.procurement.services.questions import initial_request_facts, missing_facts
from connect_labs.supply_chain.values import format_quantity, plural_unit


def _numbered(facts) -> str:
    return "\n".join(f"{index}. {fact.question}" for index, fact in enumerate(facts, start=1))


def _destination(round_: RoundRecord) -> str:
    point = round_.delivery_point or {}
    # Prefer the human-typed country name over the bare ISO code — see
    # questions.py's _context(), which applies the same preference so a
    # destination doesn't read as a code in one place and a name in another.
    country = point.get("country_name") or point.get("country")
    parts = [point.get("name"), point.get("city"), country]
    return ", ".join(part for part in parts if part) or "the delivery point"


def render_initial_request(
    commodity: CommodityRecord,
    round_: RoundRecord,
    supplier: SupplierRecord,
) -> str:
    quantity = round_.quantity_for(commodity.slug)
    quantity_text = (
        f"{format_quantity(quantity[0])} {plural_unit(quantity[1], quantity[0])}" if quantity else "the quantity below"
    )
    incoterm = (round_.delivery_point or {}).get("incoterm_requested")

    lines = [
        f"Dear {supplier.name},",
        "",
        f"We are seeking a quotation for {quantity_text} of "
        f"{commodity.name or commodity.slug}, delivered to {_destination(round_)}"
        + (f" on {incoterm} terms" if incoterm else "")
        + ".",
    ]
    if round_.notes_to_supplier:
        lines += ["", round_.notes_to_supplier]
    lines += [
        "",
        "So that we can compare offers on the same basis, please answer each of the following:",
        "",
        _numbered(initial_request_facts(commodity, round_)),
    ]
    if round_.response_deadline:
        lines += ["", f"We would be grateful for a reply by {round_.response_deadline}."]
    lines += ["", "With thanks,"]
    return "\n".join(lines)


def render_followup(
    quote: QuoteRecord,
    commodity: CommodityRecord,
    round_: RoundRecord,
    supplier: SupplierRecord,
    item=None,
) -> str:
    """Ask only for what is still missing.

    `item` is threaded to missing_facts so a supplier who already identified
    their trade item is not asked for its pack configuration again.
    """
    facts = [fact for fact in missing_facts(quote, commodity, round_, item=item) if fact.audience == "supplier"]
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
