"""How a tender reads as a card on the supplier marketplace.

The same visual language as the implementing partners' marketplace
(marketplace/programs.html): one colour per card, a big number, a bar. Here the
colour is the kind of product, the number is how much is being asked for, and
the bar is how much of the time to reply has gone.

Nothing here counts bids. The partners' cards show how many applied; on a
tender that number is the competition a supplier is bidding against, which is
exactly what sealed bids keep from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from connect_labs.supply_chain import records

# One hue per kind of product, from the same family of saturated mid-tones the
# partners' marketplace uses, so the two pages read as one place.
CATEGORY_HUES = {
    "therapeutic_food": "#d9480f",
    "supplementary_food": "#e8590c",
    "oral_rehydration": "#1c7ed6",
    "micronutrient": "#7048e8",
    "antibiotic": "#c2255c",
    "antimalarial": "#0c8599",
    "anthelmintic": "#5c940d",
    "diagnostic": "#1098ad",
    "equipment": "#495057",
    "consumable": "#868e96",
}
DEFAULT_HUE = "#3843d0"

# A tender whose replies are due within this many days is "closing soon".
CLOSING_SOON_DAYS = 14


@dataclass
class TenderCard:
    listed: object
    hue: str
    kind: str
    headline_quantity: object
    headline_unit: str
    headline_product: str
    more_products: int
    days_left: int | None
    elapsed_pct: int | None

    @property
    def tender(self):
        return self.listed.tender

    @property
    def deadline_words(self) -> str:
        if self.days_left is None:
            return "No reply-by date"
        if self.days_left < 0:
            return "Past its reply-by date"
        if self.days_left == 0:
            return "Replies due today"
        if self.days_left == 1:
            return "1 day left"
        return f"{self.days_left} days left"

    @property
    def where(self) -> str:
        """Where the goods can go: "to Kano or Sokoto, or collected", "collected only"."""
        from connect_labs.supply_chain.values import destination_phrase

        tender = self.listed.tender
        places = tender.delivery_points or []
        if not places:
            return "collected from the supplier"
        text = f"to {destination_phrase(places)}"
        return f"{text}, or collected" if tender.pickup_accepted else text

    @property
    def closing_soon(self) -> bool:
        return self.days_left is not None and 0 <= self.days_left <= CLOSING_SOON_DAYS


def card_for(listed, today: date | None = None) -> TenderCard:
    today = today or date.today()
    lines = listed.lines
    first = lines[0] if lines else None
    category = first.commodity.category if first and first.commodity else ""
    tender = listed.tender
    deadline = tender.response_deadline
    days_left = (deadline - today).days if deadline else None
    elapsed = None
    if deadline and tender.opened_at:
        opened = tender.opened_at.date()
        span = (deadline - opened).days
        if span > 0:
            elapsed = max(0, min(100, round((today - opened).days * 100 / span)))
    return TenderCard(
        listed=listed,
        hue=CATEGORY_HUES.get(category, DEFAULT_HUE),
        kind=dict(records.COMMODITY_CATEGORIES).get(category, "Product"),
        headline_quantity=first.quantity if first else None,
        headline_unit=first.quantity_unit if first else "",
        headline_product=first.name if first else "",
        more_products=max(0, len(lines) - 1),
        days_left=days_left,
        elapsed_pct=elapsed,
    )


@dataclass
class Section:
    title: str
    why: str
    cards: list


def sections(listed_tenders, today: date | None = None) -> list[Section]:
    """Invited first (they were asked by name), then closing soon, then the rest."""
    cards = [card_for(listed, today) for listed in listed_tenders]
    invited = [c for c in cards if c.listed.invited]
    rest = [c for c in cards if not c.listed.invited]
    soon = sorted((c for c in rest if c.closing_soon), key=lambda c: c.days_left)
    open_ = [c for c in rest if not c.closing_soon]
    out = []
    if invited:
        out.append(Section("Invited to you", "A buyer asked your organisation by name.", invited))
    if soon:
        out.append(Section("Closing soon", f"Replies due within {CLOSING_SOON_DAYS} days.", soon))
    if open_:
        out.append(Section("Open", "Taking bids now.", open_))
    return out


def headline(listed_tenders, supplier_count: int, today: date | None = None) -> dict:
    """The numbers across the top. Quantities are never summed -- cartons and
    tins do not add -- so the page counts products, not units."""
    today = today or date.today()
    deadlines = [
        r.tender.response_deadline
        for r in listed_tenders
        if r.tender.response_deadline and r.tender.response_deadline >= today
    ]
    return {
        "tenders": len(listed_tenders),
        "products": sum(len(r.lines) for r in listed_tenders),
        "next_deadline": min(deadlines) if deadlines else None,
        "suppliers": supplier_count,
    }
