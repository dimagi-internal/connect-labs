"""Render the text a human sends to a supplier.

Phase 1a produces text to paste into a mail client: a supplier who ignores
email will ignore a portal too, and the credibility is in the sender.
Phase 1c sends the same text over SES.

A draft is an email, so it has what any email has: a subject, a greeting to a
person where we know one, a reply-by date where the tender set one (and no
invented date where it did not), and a signature. When the product does not
know who is sending, the signature is a bracketed placeholder -- "[your
name]" -- so the gap is seen before the message goes, rather than a bare
"With thanks," that reads as finished.
"""

import re
from dataclasses import dataclass
from datetime import date

from connect_labs.supply_chain.models import Commodity, Quote, Supplier, Tender
from connect_labs.supply_chain.procurement.services.questions import SUPPLIER, initial_request_facts, missing_facts
from connect_labs.supply_chain.values import (
    day_text,
    destination_phrase,
    money_digits,
    quantity_phrase,
    unit_noun,
)

NAME_PLACEHOLDER = "[your name]"
ORGANISATION_PLACEHOLDER = "[your organisation]"


@dataclass(frozen=True)
class Sender:
    """Who signs the message: the person asking, and the organisation they ask for."""

    name: str = ""
    organisation: str = ""


@dataclass(frozen=True)
class Draft:
    subject: str
    text: str
    # The address to send to, when the supplier's record holds one. None is
    # an answer: there is no address on file, and the page says so.
    to: str | None = None

    def as_dict(self) -> dict:
        return {"subject": self.subject, "text": self.text, "to": self.to}


# ---- the parts every draft shares ----------------------------------------


def _numbered(facts) -> str:
    return "\n".join(f"{index}. {fact.text}" for index, fact in enumerate(facts, start=1))


def _named_contact(supplier: Supplier) -> dict | None:
    contacts = [c for c in (supplier.contacts or []) if isinstance(c, dict)]
    return next((c for c in contacts if (c.get("name") or "").strip()), None)


def _address(supplier: Supplier) -> str | None:
    """The named contact's address, else the first address on file."""
    contacts = [c for c in (supplier.contacts or []) if isinstance(c, dict)]
    named = _named_contact(supplier)
    if named and (named.get("email") or "").strip():
        return named["email"].strip()
    return next(((c.get("email") or "").strip() for c in contacts if (c.get("email") or "").strip()), None)


def _greeting(supplier: Supplier) -> str:
    contact = _named_contact(supplier)
    return f"Dear {contact['name'].strip()}," if contact else f"Dear {supplier.name},"


def _sign_off(sender: Sender | None) -> list[str]:
    sender = sender or Sender()
    return [
        "",
        "With thanks,",
        "",
        (sender.name or "").strip() or NAME_PLACEHOLDER,
        (sender.organisation or "").strip() or ORGANISATION_PLACEHOLDER,
    ]


def _reply_by(tender: Tender, today: date | None) -> list[str]:
    """The tender's own deadline, said once -- and nothing at all without one."""
    deadline = tender.response_deadline
    if not deadline:
        return []
    today = today or date.today()
    if deadline >= today:
        return ["", f"Please reply by {day_text(deadline)}."]
    # Past: say what was asked, and do not move the date on the tender's behalf.
    return [
        "",
        f"We asked for replies by {day_text(deadline)}; if you can still send a quotation, we would welcome it.",
    ]


def _about(tender: Tender, commodity: Commodity) -> str:
    """What the email is about, as the supplier knows it: what we asked for and where.

    Never the tender's own label ("RUTF round 2: ..."): that is our internal
    numbering, and a supplier read it as such.
    """
    return f" — {_quantity_text(commodity, tender)} of {_product(commodity)}, {_where_short(tender)}"


def _quantity_text(commodity: Commodity, tender: Tender) -> str:
    quantity = tender.quantity_for(commodity.slug)
    return quantity_phrase(quantity[0], quantity[1]) if quantity else "the quantity below"


def _product(commodity: Commodity) -> str:
    return commodity.name or commodity.slug


def _where(tender: Tender) -> str:
    """Where the goods go, as the opening sentence says it."""
    incoterm = tender.incoterm_requested
    places = tender.delivery_points or []
    if not places:
        return "for us to collect from you"
    where = f"delivered to {destination_phrase(places)}" + (f" on {incoterm} terms" if incoterm else "")
    if tender.pickup_accepted:
        where += ", or for us to collect from you"
    return where


def _where_short(tender: Tender) -> str:
    """Where the goods go, as a subject line has room for: "delivered Kano"."""
    places = tender.delivery_points or []
    if not places:
        return "for collection"
    towns = list(dict.fromkeys(p.get("city") or p.get("name") or "" for p in places if isinstance(p, dict)))
    towns = [t for t in towns if t]
    where = f"delivered {' or '.join(towns)}" if towns else "delivered"
    return where + (", or collected" if tender.pickup_accepted else "")


# ---- a buyer's note that only restates the questions ----------------------

# Words that carry no fact of their own in a note to a supplier: "please
# state the price" asks nothing the question "What is the price?" does not.
_FILLER = frozenset("""
    a an the and or of to for in on at by with from as is are be we us our you your it its this that
    these those please kindly also state tell confirm include including provide give advise let know
    would will can could should may must any all each every per if not no do does
    """.split())


def _words(text: str) -> set[str]:
    words = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9,.\-']*", text.lower()):
        word = raw.strip(",.-'").replace(",", "")
        if not word or word in _FILLER:
            continue
        words.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return words


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _note_worth_saying(note: str, rest_of_email: str) -> str:
    """The buyer's note, less any sentence that says nothing the email does not already say.

    The note was printed, and then the numbered questions asked the same
    things again: "please state your price per carton, shelf life and MOQ",
    followed by questions 1, 6 and 7. A sentence goes only when EVERY word it
    carries is already in the email, so anything the buyer adds -- a pilot's
    small volumes, a specification number -- stays, in the buyer's words.
    """
    said = _words(rest_of_email)
    kept = []
    for paragraph in (note or "").strip().split("\n"):
        sentences = [s for s in _sentences(paragraph) if not _words(s) <= said]
        kept.append(" ".join(sentences))
    return "\n".join(kept).strip()


# ---- the three drafts -----------------------------------------------------


def render_initial_request(
    commodity: Commodity,
    tender: Tender,
    supplier: Supplier,
    *,
    sender: Sender | None = None,
    today: date | None = None,
) -> Draft:
    quantity_text = _quantity_text(commodity, tender)
    places = tender.delivery_points or []
    facts = initial_request_facts(commodity, tender)
    opening = f"We are seeking a quotation for {quantity_text} of {_product(commodity)}, {_where(tender)}."
    lines = [_greeting(supplier), "", opening]
    if len(places) > 1:
        lines += ["", "Please say which of these places your price covers; you may quote a different price for each."]
    note = _note_worth_saying(tender.notes_to_supplier, "\n".join([opening, _numbered(facts)]))
    if note:
        lines += ["", note]
    lines += [
        "",
        "So that we can compare offers on the same basis, please answer each of the following:",
        "",
        _numbered(facts),
    ]
    lines += _reply_by(tender, today)
    lines += _sign_off(sender)
    subject = f"Quotation request: {quantity_text} of {_product(commodity)}, {_where_short(tender)}"
    return Draft(subject=subject, text="\n".join(lines), to=_address(supplier))


def _priced_per(quote: Quote, commodity: Commodity, item=None) -> str:
    unit = quote.as_quoted_unit
    if unit in ("per_pack", "per_base_unit"):
        field = "pack_unit" if unit == "per_pack" else "base_unit"
        noun = getattr(item, field, "") if item is not None else ""
        noun = noun or getattr(commodity, field, "")
        return f"per {unit_noun(noun)}" if noun else ""
    return {"per_metric_tonne": "per tonne", "per_lot_total": "for the lot"}.get(unit, "")


def _quote_name(quote: Quote, commodity: Commodity, item=None) -> tuple[str, str]:
    """("your quotation of 10 Sep 2026, USD 50.00 per carton", "of 10 Sep 2026")."""
    dated = f" of {day_text(quote.received_on)}" if quote.received_on else ""
    price = ""
    if quote.as_quoted_amount is not None:
        price = f"{quote.as_quoted_currency or 'USD'} {money_digits(quote.as_quoted_amount)}"
        per = _priced_per(quote, commodity, item)
        price = f", {price} {per}".rstrip() if per else f", {price}"
    return f"your quotation{dated}{price}", dated


def render_followup(
    quote: Quote,
    commodity: Commodity,
    tender: Tender,
    supplier: Supplier,
    item=None,
    *,
    sender: Sender | None = None,
    today: date | None = None,
) -> Draft:
    """Ask only for what is still missing, about the quote by its date and price.

    `item` is threaded to missing_facts so a supplier who already identified
    their trade item is not asked for its pack configuration again.
    """
    facts = [fact for fact in missing_facts(quote, commodity, tender, item=item) if fact.audience == SUPPLIER]
    named, dated = _quote_name(quote, commodity, item)
    if not facts:
        lines = [
            _greeting(supplier),
            "",
            f"Thank you for {named}. It is complete, and there is nothing outstanding.",
            *_sign_off(sender),
        ]
        return Draft(
            subject=f"Thank you for your quotation{dated}{_about(tender, commodity)}",
            text="\n".join(lines),
            to=_address(supplier),
        )
    lines = [
        _greeting(supplier),
        "",
        f"Thank you for {named}. To compare it against the other offers we need a little more detail:",
        "",
        _numbered(facts),
    ]
    lines += _reply_by(tender, today)
    lines += _sign_off(sender)
    return Draft(
        subject=f"Follow-up on your quotation{dated}{_about(tender, commodity)}",
        text="\n".join(lines),
        to=_address(supplier),
    )


def render_reminder(
    commodity: Commodity,
    tender: Tender,
    supplier: Supplier,
    *,
    sent_on: date,
    last_reminder_on: date | None = None,
    sender: Sender | None = None,
    today: date | None = None,
) -> Draft:
    """A polite chase to a supplier who has not answered a request.

    Names the day we asked and what we asked for, and repeats the questions:
    the commonest reason for silence is that the first message never reached
    the person who prices.
    """
    asked = day_text(sent_on)
    lines = [
        _greeting(supplier),
        "",
        f"On {asked} we asked for a quotation for {_quantity_text(commodity, tender)} of {_product(commodity)}, "
        f"{_where(tender)}. We have not yet received a reply, and we would still very much like to consider "
        "an offer from you.",
    ]
    if last_reminder_on:
        lines += ["", f"We last wrote about this on {day_text(last_reminder_on)}."]
    lines += _reply_by(tender, today)
    lines += [
        "",
        "In case our first message did not reach you, these are the questions we asked:",
        "",
        _numbered(initial_request_facts(commodity, tender)),
        "",
        "If you are not able to quote this time, a short reply saying so would help us plan.",
    ]
    lines += _sign_off(sender)
    return Draft(
        subject=f"Reminder: quotation request of {asked}{_about(tender, commodity)}",
        text="\n".join(lines),
        to=_address(supplier),
    )
