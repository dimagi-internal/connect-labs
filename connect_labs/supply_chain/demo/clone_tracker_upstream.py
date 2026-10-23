"""A clone's procurement from the programme's own tracker, and the flows it implies.

`clone_upstream` invents every supplier and price. This is the other source: the
programme's procurement tracker (`tracker_import`, read from Drive -- no supplier,
contact or price is written into this public repository). The tracker states the
suppliers, who was asked when, and what each quoted, for two rounds:

- round 1 (the sheet's first tender): ORDERED, it says, but not from whom, when it
  shipped, or what was paid;
- round 2 (the second): re-quotes requested and still coming in -- open today.

So the import records what the sheet states, and `seed_tracker_upstream` backs out
what round 1 most likely went through, each record saying it is inferred:

- the award, to the one round-1 quote whose landed total the sheet reconciles
  (a per-carton price AND a stated freight figure) -- the order the sheet says
  was placed, at that quote's price and freight, for the round's quantity;
- delivered to the warehouse in one shipment, invoiced on delivery and paid a
  fortnight later -- dated from that quote, at a lead time no record states.

Round 2 is left as the sheet has it: open, with whatever re-quotes have arrived.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

NOTE = (
    "Inferred for the demo from the programme's procurement tracker: the tracker records round 1 as ordered, "
    "from its one quote with a complete landed total. The award, order, shipment, receipt, invoice and "
    "payment dates are inferred; quantities, price and freight are the tracker's."
)
CARTON = 150
# Days from award to the stock reaching the warehouse, and from delivery to payment: neither is stated.
LEAD_TIME_DAYS, AWARD_AFTER_QUOTE_DAYS, PAY_AFTER_DAYS = 35, 7, 14
EXPIRY_DAYS = 730  # RUTF's usual two-year shelf life; the batch's own date is not known
# When the tracker's rows are recorded in the clone's history. Its round-2 re-quotes are
# later than this, so a page read "as of" a day before them shows them early -- a known
# limit of importing a whole sheet at once.
IMPORTED_ON = date(2026, 5, 19)


def _complete_quote(quotes):
    """The round-1 quote the sheet reconciles: priced per carton, freight stated as a figure."""
    complete = [
        q
        for q in quotes
        if q.get("as_quoted_unit") == "per_pack"
        and q.get("freight_basis") == "excluded"
        and q.get("freight_amount") not in (None, "")
        and not q.get("voided")
    ]
    return complete[0] if len(complete) == 1 else None


def import_tracker_dated(access, *, program_id: int, on: date) -> dict:
    """Run the tracker import with every write dated `on` (synthetic programmes only)."""
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.procurement.services.tracker_import import import_tracker

    at = timezone.make_aware(datetime.combine(on, time(9)))
    with seed_overrides(program_id, channel="command", recorded_at=at):
        return import_tracker(access)


def seed_tracker_upstream(op, access, *, program_id: int, warehouse_id: int, item, today: date) -> dict:
    """Import the tracker, then record round 1's award, order, delivery, invoice and payment. Returns ids.

    `op(day, hour, name, **payload)` writes one operation, dated. Raises ValueError
    when the sheet does not single out one complete round-1 quote: guessing which
    supplier was ordered from would put a real company's name on an order nobody
    recorded.
    """
    from connect_labs.supply_chain.runner import running_org

    # Dated the day after round 1's complete quote arrived (18 May 2026 on the sheet), so the
    # award that follows it never precedes the quote it awards in the as-of history.
    report = import_tracker_dated(access, program_id=program_id, on=IMPORTED_ON)
    tenders = sorted(op(today, 9, "tender_list"), key=lambda t: t["id"])
    if len(tenders) < 2:
        raise ValueError(f"the tracker import made {len(tenders)} tenders, expected its two rounds")
    round1, round2 = tenders[0], tenders[1]
    quote = _complete_quote(op(today, 9, "quote_list", tender_id=round1["id"]))
    if quote is None:
        raise ValueError("the tracker's round 1 has no single quote with a per-carton price and stated freight")
    first_quote_day = date.fromisoformat(quote["received_on"]) if quote.get("received_on") else date(2026, 5, 18)

    cartons = int(Decimal(str(round1["lines"][0]["quantity"])))
    price = Decimal(str(quote["as_quoted_amount"]))
    freight = Decimal(str(quote["freight_amount"]))
    awarded_on = first_quote_day + timedelta(days=AWARD_AFTER_QUOTE_DAYS)
    award = op(awarded_on, 12, "award_create", tender_id=round1["id"], quote_id=quote["id"],
               rationale="The one round-1 quote with a per-carton price and freight both stated; "
                         "the tracker records round 1 as ordered. " + NOTE,
               decided_by="Programme manager", decided_on=awarded_on.isoformat())  # fmt: skip
    order = op(awarded_on, 12, "contract_create", data={
        "tender_id": round1["id"], "award_id": award["id"], "supplier_id": quote["supplier_id"],
        "commodity_slug": "rutf", "item_id": item.pk, "buyer_of_record": "programme_org",
        "buyer_org_id": running_org(program_id).pk, "reference": "RUTF round 1", "status": "placed",
        "currency": "USD", "quantity": cartons, "quantity_unit": "carton", "unit_price": str(price),
        "unit_price_unit": "per_pack", "freight_basis": "excluded", "freight_amount": str(freight),
        "duties_basis": "not_specified", "delivery_supply_point_id": warehouse_id,
        "promised_lead_time_days": LEAD_TIME_DAYS, "source": "we_recorded",
        "signed_on": awarded_on.isoformat(), "note": NOTE,
    })["id"]  # fmt: skip
    delivered = awarded_on + timedelta(days=LEAD_TIME_DAYS)
    dispatched = delivered - timedelta(days=10)
    shipment = op(dispatched, 14, "shipment_record", data={
        "contract_id": order, "reference": "RUTF round 1", "status": "dispatched",
        "dispatched_on": dispatched.isoformat(), "expected_on": delivered.isoformat(),
        "source": "supplier_reported", "note": NOTE,
        "lines": [{"item_id": item.pk, "batch": "R1", "quantity": cartons, "quantity_unit": "carton"}],
    })["id"]  # fmt: skip
    op(delivered, 9, "shipment_update", shipment_id=shipment, data={"status": "delivered"})
    op(delivered, 10, "receipt_record", data={
        "contract_id": order, "shipment_id": shipment, "supply_point_id": warehouse_id,
        "received_on": delivered.isoformat(), "reference": "GRN round 1", "source": "we_recorded",
        "lines": [{"item_id": item.pk, "batch": "R1", "expiry": (delivered + timedelta(days=EXPIRY_DAYS)).isoformat(),
                   "quantity_accepted": cartons, "quantity_unit": "carton"}],
    })  # fmt: skip
    total = price * cartons + freight
    invoice = op(delivered, 11, "invoice_record", data={
        "contract_id": order, "reference": "Round 1 invoice", "issued_on": delivered.isoformat(),
        "status": "received", "currency": "USD", "amount": str(total), "quantity_billed": cartons,
        "quantity_unit": "carton", "unit_price": str(price), "freight_amount": str(freight),
        "source": "supplier_reported", "note": NOTE,
    })["id"]  # fmt: skip
    paid = delivered + timedelta(days=PAY_AFTER_DAYS)
    payment = None
    if paid <= today:
        payment = op(paid, 15, "payment_record", data={
            "invoice_id": invoice, "contract_id": order, "paid_on": paid.isoformat(), "amount": str(total),
            "currency": "USD", "reference": "Round 1 payment", "source": "we_recorded", "note": NOTE,
        })["id"]  # fmt: skip
    return {
        "import": {k: report[k] for k in ("rows", "imported", "refused")},
        "tenders": [round1["id"], round2["id"]],
        "award": award["id"],
        "orders": [order],
        "shipments": [shipment],
        "invoice": invoice,
        "payment": payment,
        "received_on": delivered.isoformat(),
        "opening_cartons": cartons,
    }
