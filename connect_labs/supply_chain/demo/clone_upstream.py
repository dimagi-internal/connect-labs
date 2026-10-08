"""Where a synthetic clone's RUTF came from: the sourcing and fulfilment upstream of its stores.

Without this a clone's stock simply appears in the central store ("received, no
order linked") and every upstream screen -- Sourcing, Orders, a shipment, an
invoice -- is empty. `seed_upstream` records, dated relative to the clone's
first visit, everything a buyer of record does before stock reaches a store:

- four suppliers;
- a past tender: all four asked; one quotes in full, one without freight, one
  late, one never answers; awarded to the complete quote;
- order 1 from that award: advance paid, shipped, delivered -- its receipt IS
  the central store's opening stock -- invoiced and settled;
- order 2, a repeat order from the same award, on the road now;
- a second tender, open now: two quotes in (one without its pack size), two
  suppliers silent and reminded.

Invented, all of it: every supplier, contact, price, reference and date. Each
record that takes a note says so (`NOTE`); suppliers carry it in their profile.
THIS REPOSITORY IS PUBLIC: no real company or person is named here.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

NOTE = (
    "Invented for the demo: the clone's supply chain upstream of its stores -- suppliers, quotes, "
    "orders, shipments, invoices and payments -- is made up; no real supplier or order is named."
)
CARTON = 150  # sachets per carton
BUYER_SLUG = "dimagi"
DELIVERY = {
    "key": "central",
    "name": "Programme central store",
    "city": "Maiduguri",
    "country": "NG",
    "country_name": "Nigeria",
}

# key, name, country, city, type, contact, email -- all invented.
SUPPLIERS = [
    ("sahel", "Sahel Nutrition Works", "NG", "Kano", "manufacturer", "Amina Bello", "amina@sahel-nutrition.example"),
    ("lakeside", "Lakeside Foods", "CM", "Garoua", "manufacturer", "Paul Nkem", "p.nkem@lakeside-foods.example"),
    ("meridian", "Meridian Medical Supply", "GH", "Tema", "distributor", "Efua Owusu", "efua@meridian-med.example"),
    ("dunes", "Dunes Trading Co.", "NG", "Abuja", "distributor", "Musa Garba", "musa@dunes-trading.example"),
]


def _buyer(op, day) -> int:
    for org in op(day, 9, "org_list"):
        if org.get("slug") == BUYER_SLUG:
            return org["id"]
    return op(day, 9, "org_upsert", data={"slug": BUYER_SLUG, "name": "Dimagi"})["id"]


def _cartons(sachets) -> int:
    return int((Decimal(sachets) + CARTON - 1) // CARTON)


def _quote(op, day, *, tender_id, supplier_id, price, cartons, ref, **extra):
    data = {
        "tender_id": tender_id,
        "supplier_id": supplier_id,
        "commodity_slug": "rutf",
        "as_quoted_amount": price,
        "as_quoted_unit": "per_pack",
        "as_quoted_currency": "USD",
        "quantity_basis": cartons,
        "quantity_basis_unit": "carton",
        "shelf_life_months_stated": 24,
        "moq": 200,
        "moq_unit": "carton",
        "received_on": day.isoformat(),
        "supplier_reference": ref,
        "notes": NOTE,
        **extra,
    }
    return op(day, 11, "quote_record", data=data)


def _tender(op, *, label, opened, deadline, cartons):
    tender = op(opened, 10, "tender_create", data={
        "label": label,
        "lines": [{"commodity_slug": "rutf", "quantity": cartons, "quantity_unit": "carton"}],
        "delivery_points": [DELIVERY],
        "incoterm_requested": "CPT Maiduguri",
        "reminder_interval_days": 7,
        "response_deadline": deadline.isoformat(),
        "visibility": "private",
        "notes_to_supplier": "Please quote per carton of 150 sachets, delivered Maiduguri, with freight and duties.",
    })  # fmt: skip
    op(opened, 10, "tender_open", tender_id=tender["id"])
    op(
        opened,
        10,
        "tender_set_duty_terms",
        tender_id=tender["id"],
        duty_terms="buyer_waiver",
        set_on=opened.isoformat(),
    )
    return tender["id"]


def _ask(op, day, tender_id, suppliers) -> dict:
    return {
        key: op(day, 10, "outreach_log", data={
            "tender_id": tender_id, "supplier_id": sid, "channel": "manual", "sent_on": day.isoformat(),
        })["id"]  # fmt: skip
        for key, sid in suppliers.items()
    }


def _answered(op, day, outreach_id, kind="quote"):
    op(day, 11, "outreach_update", outreach_id=outreach_id, data={
        "responded": True, "response_kind": kind, "responded_on": day.isoformat(),
    })  # fmt: skip


def seed_upstream(op, *, program_id: int, central_id: int, item, opening_sachets, setup: date, today: date) -> dict:
    """Record the clone's sourcing and fulfilment; returns ids. `op(day, hour, name, **payload)` writes one, dated."""
    cartons = _cartons(opening_sachets)
    start = setup - timedelta(days=70)
    buyer = _buyer(op, start)
    suppliers = {}
    for key, name, country, city, kind, contact, email in SUPPLIERS:
        suppliers[key] = op(start, 9, "supplier_create", data={
            "name": name, "country": country, "city": city, "type": kind, "status": "contacted",
            "contacts": [{"name": contact, "email": email}],
        })["id"]  # fmt: skip

    # ---- Tender 1: asked ten weeks before the first delivery, awarded to the complete quote ----
    t1_open, t1_deadline = start, start + timedelta(days=14)
    t1 = _tender(
        op, label=f"RUTF tender 1: {cartons:,} cartons", opened=t1_open, deadline=t1_deadline, cartons=cartons
    )
    op(t1_open, 10, "tender_set_import_estimates", tender_id=t1, clearing_estimate_per_unit="1.10",
       set_on=t1_open.isoformat())  # fmt: skip
    asked = _ask(op, t1_open, t1, suppliers)
    winner = _quote(
        op, t1_open + timedelta(days=4), tender_id=t1, supplier_id=suppliers["sahel"], price="48.60", cartons=cartons,
        ref="SNW-PFI-0207", pack_spec_source="stated_on_quote", base_per_pack_stated=CARTON,
        base_unit_grams_stated=92, freight_basis="excluded", freight_amount=str(Decimal(cartons) * Decimal("2.40")),
        duties_basis="excluded", duties_amount="0.00", lead_time_days=35, incoterm="CPT Maiduguri",
        validity_until=(t1_open + timedelta(days=34)).isoformat(), payment_terms="50% with order, 50% on delivery",
    )  # fmt: skip
    _answered(op, t1_open + timedelta(days=4), asked["sahel"])
    _quote(
        op, t1_open + timedelta(days=6), tender_id=t1, supplier_id=suppliers["meridian"], price="47.90",
        cartons=cartons, ref="MMS-Q-1188", pack_spec_source="stated_on_quote", base_per_pack_stated=CARTON,
        base_unit_grams_stated=92, freight_basis="not_specified", lead_time_days=42, incoterm="FCA Tema",
        validity_until=(t1_open + timedelta(days=36)).isoformat(),
    )  # fmt: skip
    _answered(op, t1_open + timedelta(days=6), asked["meridian"])
    _quote(
        op, t1_deadline + timedelta(days=2), tender_id=t1, supplier_id=suppliers["lakeside"], price="51.00",
        cartons=cartons, ref="LF-2207", pack_spec_source="stated_on_quote", base_per_pack_stated=CARTON,
        base_unit_grams_stated=92, freight_basis="included", duties_basis="included", lead_time_days=49,
        incoterm="DDP Maiduguri", validity_until=(t1_deadline + timedelta(days=32)).isoformat(),
    )  # fmt: skip
    _answered(op, t1_deadline + timedelta(days=2), asked["lakeside"])
    awarded_on = t1_deadline + timedelta(days=4)
    award = op(awarded_on, 12, "award_create", tender_id=t1, quote_id=winner["id"],
               rationale="The only quote with pack, quantity, freight and duties all stated.",
               decided_by="Programme manager", decided_on=awarded_on.isoformat())  # fmt: skip

    # ---- Order 1: delivered; its receipt is the central store's opening stock ----
    price, freight = Decimal("48.60"), Decimal(cartons) * Decimal("2.40")
    order1 = op(awarded_on, 12, "contract_create", data={
        "tender_id": t1, "award_id": award["id"], "supplier_id": suppliers["sahel"], "commodity_slug": "rutf",
        "item_id": item.pk, "buyer_of_record": "programme_org", "buyer_org_id": buyer, "reference": "PO-RUTF-0001",
        "status": "placed", "currency": "USD", "quantity": cartons, "quantity_unit": "carton",
        "unit_price": str(price), "unit_price_unit": "per_pack", "freight_basis": "excluded",
        "freight_amount": str(freight), "duties_basis": "excluded", "duties_amount": "0.00", "vat_basis": "included",
        "incoterm": "CPT Maiduguri", "delivery_supply_point_id": central_id, "promised_lead_time_days": 35,
        "payment_terms": "advance", "source": "we_recorded", "signed_on": awarded_on.isoformat(), "note": NOTE,
    })["id"]  # fmt: skip
    goods = price * cartons
    advance = op(awarded_on, 13, "payment_record", data={
        "contract_id": order1, "paid_on": awarded_on.isoformat(), "amount": str(goods / 2), "currency": "USD",
        "reference": "advance on SNW-PFI-0207", "source": "we_recorded", "note": NOTE,
    })["id"]  # fmt: skip
    dispatched = setup - timedelta(days=21)
    shipment1 = op(dispatched, 14, "shipment_record", data={
        "contract_id": order1, "reference": "PO-RUTF-0001", "status": "dispatched",
        "dispatched_on": dispatched.isoformat(), "expected_on": (setup - timedelta(days=1)).isoformat(),
        "carrier": "Northern Haulage", "source": "supplier_reported", "note": NOTE,
        "lines": [{"item_id": item.pk, "batch": "SNW2607A", "quantity": cartons, "quantity_unit": "carton"}],
    })["id"]  # fmt: skip
    op(setup, 9, "shipment_update", shipment_id=shipment1, data={"status": "delivered"})
    op(setup, 10, "receipt_record", data={
        "contract_id": order1, "shipment_id": shipment1, "supply_point_id": central_id,
        "received_on": setup.isoformat(), "reference": "GRN-0001", "source": "we_recorded",
        "lines": [{"item_id": item.pk, "batch": "SNW2607A", "expiry": (setup + timedelta(days=700)).isoformat(),
                   "quantity_accepted": cartons, "quantity_unit": "carton"}],
    })  # fmt: skip
    invoiced = setup + timedelta(days=4)
    total = goods + freight
    invoice = None
    if invoiced <= today:
        invoice = op(invoiced, 11, "invoice_record", data={
            "contract_id": order1, "reference": "SNW-INV-0412", "issued_on": invoiced.isoformat(),
            "status": "received", "currency": "USD", "amount": str(total), "quantity_billed": cartons,
            "quantity_unit": "carton", "unit_price": str(price), "freight_amount": str(freight),
            "acknowledges_payment_ids": [advance], "source": "supplier_reported", "note": NOTE,
        })["id"]  # fmt: skip
        settled = invoiced + timedelta(days=10)
        if settled <= today:
            op(settled, 15, "payment_record", data={
                "invoice_id": invoice, "contract_id": order1, "paid_on": settled.isoformat(),
                "amount": str(total - goods / 2), "currency": "USD", "reference": "balance on SNW-INV-0412",
                "source": "we_recorded", "note": NOTE,
            })  # fmt: skip

    # ---- Order 2: a repeat order from the same award, on the road now ----
    order2 = shipment2 = None
    placed = today - timedelta(days=21)
    if placed > setup:
        repeat = max(_cartons(Decimal(opening_sachets) / 3), 1)
        order2 = op(placed, 12, "contract_create", data={
            "tender_id": t1, "award_id": award["id"], "supplier_id": suppliers["sahel"], "commodity_slug": "rutf",
            "item_id": item.pk, "buyer_of_record": "programme_org", "buyer_org_id": buyer,
            "reference": "PO-RUTF-0002", "status": "placed", "currency": "USD", "quantity": repeat,
            "quantity_unit": "carton", "unit_price": str(price), "unit_price_unit": "per_pack",
            "freight_basis": "excluded", "freight_amount": str(Decimal(repeat) * Decimal("2.40")),
            "duties_basis": "excluded", "duties_amount": "0.00", "vat_basis": "included",
            "incoterm": "CPT Maiduguri", "delivery_supply_point_id": central_id, "promised_lead_time_days": 28,
            "payment_terms": "advance", "source": "we_recorded", "signed_on": placed.isoformat(), "note": NOTE,
        })["id"]  # fmt: skip
        out = today - timedelta(days=7)
        shipment2 = op(out, 14, "shipment_record", data={
            "contract_id": order2, "reference": "PO-RUTF-0002", "status": "in_transit",
            "dispatched_on": out.isoformat(), "expected_on": (today + timedelta(days=6)).isoformat(),
            "carrier": "Northern Haulage", "source": "supplier_reported", "note": NOTE,
            "lines": [{"item_id": item.pk, "batch": "SNW2609B", "quantity": repeat, "quantity_unit": "carton"}],
        })["id"]  # fmt: skip

    # ---- Tender 2: open now; two quotes in, two suppliers silent and reminded ----
    t2_open = max(today - timedelta(days=10), setup + timedelta(days=1))
    t2 = _tender(op, label=f"RUTF tender 2: {cartons:,} cartons", opened=t2_open,
                 deadline=today + timedelta(days=7), cartons=cartons)  # fmt: skip
    op(t2_open, 10, "tender_set_import_estimates", tender_id=t2, clearing_estimate_per_unit="1.10",
       set_on=t2_open.isoformat())  # fmt: skip
    asked2 = _ask(op, t2_open, t2, suppliers)
    q_day = min(t2_open + timedelta(days=2), today)
    _quote(
        op, q_day, tender_id=t2, supplier_id=suppliers["sahel"], price="49.20", cartons=cartons, ref="SNW-PFI-0311",
        pack_spec_source="stated_on_quote", base_per_pack_stated=CARTON, base_unit_grams_stated=92,
        freight_basis="excluded", freight_amount=str(Decimal(cartons) * Decimal("2.40")), duties_basis="excluded",
        duties_amount="0.00", lead_time_days=35, incoterm="CPT Maiduguri",
        validity_until=(q_day + timedelta(days=30)).isoformat(), payment_terms="50% with order, 50% on delivery",
    )  # fmt: skip
    _answered(op, q_day, asked2["sahel"])
    q2_day = min(t2_open + timedelta(days=4), today)
    _quote(
        op, q2_day, tender_id=t2, supplier_id=suppliers["lakeside"], price="50.40", cartons=cartons, ref="LF-2311",
        pack_spec_source="not_stated", freight_basis="included", duties_basis="included", lead_time_days=42,
        incoterm="DDP Maiduguri", validity_until=(q2_day + timedelta(days=45)).isoformat(),
    )  # fmt: skip
    _answered(op, q2_day, asked2["lakeside"])
    reminded = min(t2_open + timedelta(days=7), today)
    for key in ("meridian", "dunes"):
        op(reminded, 10, "outreach_update", outreach_id=asked2[key], data={"last_reminder_on": reminded.isoformat()})

    return {
        "suppliers": len(suppliers),
        "tenders": [t1, t2],
        "orders": [o for o in (order1, order2) if o],
        "shipments": [s for s in (shipment1, shipment2) if s],
        "invoice": invoice,
        "opening_cartons": cartons,
    }
