"""An LLO buys a phone for every adult in three villages, and hands each one over at a visit.

The second shape a local organisation brings to Supply: not a consumable it
gives out a ration at a time, but one durable thing per person. The
organisation runs its own procurement (#2458) -- it tenders to local phone
distributors, compares, awards, orders, pays and receives with inspection --
then issues the phones to its field workers, and each handover is a Connect
visit that takes one phone off that worker's stock:

- a labs-only programme run by an invented Kenyan LLO, declared before
  anything else is written;
- a tender for 1,200 phones (one per adult registered in the three
  villages), three distributors asked, two quotes, one silent; awarded to the
  quote delivered to the office with the specification stated;
- the order, paid half in advance, delivered, received with six phones
  rejected at inspection, invoiced, balance still to pay;
- six field workers, two per village, issued phones in two rounds;
- a handover visit per adult, four weeks of them, each giving one phone and
  recording the recipient and the handset's IMEI, read into the ledger by the
  real visit reader (`visit_consumption_ingest`) week by week;
- one worker's app counts one phone fewer than the ledger says they hold.

Invented, all of it: the organisation, villages, distributors, people,
prices, IMEIs and dates. THIS REPOSITORY IS PUBLIC. The handover form is
invented too: it is the shape such a form would take, not a released app.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

NOTE = "Invented for the demo: an LLO buying and handing over a phone to every adult; nothing here is real."

SPEC = {
    "label": "Tumaini phones for every adult (demo)",
    "org_name": "Tumaini Community Network (demo)",
    "program_name": "Tumaini phones for every adult (demo)",
    "domain": "@tumaini.example",
    "admin": ("dev-tumaini-admin", "admin@tumaini.example", "Neema", "Wekesa"),
}
KEY = "phones"

SKU = "TUM-PHONE-A13"
SLUG = "smartphone"
ITEM_NAME = "Android smartphone, 4G dual-SIM, with charger"
SPEC_TEXT = (
    "Android 13 or later, 4G, dual SIM, battery 3,000 mAh or more, charger in the box, "
    "12-month warranty, delivered to our Kilifi office."
)

# The office store, and the three villages: name, adults registered, a point near the village centre.
STORE = ("tumaini-office", "Tumaini office store, Kilifi", -3.6305, 39.8499)
VILLAGES = (
    ("kibaoni", "Kibaoni", 420, -3.5560, 39.7810),
    ("mwembeni", "Mwembeni", 380, -3.7020, 39.7430),
    ("majengo", "Majengo", 400, -3.6010, 39.9210),
)
ADULTS = sum(v[2] for v in VILLAGES)  # 1,200: the tender's quantity
# Two field workers per village, invented usernames.
WORKERS = {
    "chw-amani": "kibaoni",
    "chw-baraka": "kibaoni",
    "chw-dalila": "mwembeni",
    "chw-faraji": "mwembeni",
    "chw-imani": "majengo",
    "chw-jabari": "majengo",
}
# The worker whose phone app counts one fewer than the ledger: a handset unaccounted for.
ONE_SHORT = "chw-faraji"
REJECTED = 6  # dead on arrival at inspection
VAT_RATE = Decimal("0.16")  # Kenya's standard rate; the quotes are per phone with VAT shown separately
FIRST_ISSUE = 100  # to each worker in week 0
WEEKS = 4
HANDOVERS_PER_DAY = 7  # a worker's working day, Monday to Saturday

# key, name, city, contact, email -- invented.
SUPPLIERS = [
    ("pwani", "Pwani Mobile Distributors", "Mombasa", "Halima Said", "sales@pwani-mobile.example"),
    ("bahari", "Bahari Electronics Wholesale", "Mombasa", "Kevin Otieno", "kevin@bahari-elec.example"),
    ("lakeview", "Lakeview Devices", "Malindi", "Ruth Kahindi", "orders@lakeview-devices.example"),
]

# The handover form (invented): its name, identifier and answer paths.
FORM = "Phone handover"
XMLNS = "http://openrosa.org/formdesigner/0F3B1C2E-7A44-4C1D-9E21-5D7A0C1B6E90"
PATHS = {
    "given": "form.handover.phones_given",
    "imei": "form.handover.imei",
    "village": "form.recipient.village",
    "age": "form.recipient.age",
    "consent": "form.recipient.consent",
    "balance": "form.var.phones_remaining",
}


@dataclass
class World:
    start: date
    visits: list = field(default_factory=list)
    issues: list = field(default_factory=list)  # [{"on": date, "lines": {username: int}}]
    uuids: dict = field(default_factory=dict)


def _location(rng, village_key) -> str:
    _key, _name, _adults, lat, lon = next(v for v in VILLAGES if v[0] == village_key)
    return (
        f"{lat + rng.uniform(-0.012, 0.012):.6f} {lon + rng.uniform(-0.012, 0.012):.6f} 12.0 {rng.uniform(3, 9):.1f}"
    )


def _imei(rng) -> str:
    """A 15-digit, Luhn-valid number in a reporting-body prefix no maker uses ('99'): invented, and checkable."""
    body = "99" + "".join(str(rng.randrange(10)) for _ in range(12))
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return body + str((10 - total % 10) % 10)


def _visit(visit_id, username, user_id, on, answers, location):
    xform = str(uuid.UUID(int=visit_id))
    body: dict = {"@name": FORM, "@xmlns": XMLNS}
    for path, value in answers.items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {
        "id": visit_id,
        "xform_id": xform,
        "username": username,
        "user_id": user_id,
        "deliver_unit": "",
        "deliver_unit_id": None,
        "entity_id": f"adult-{visit_id}",
        "entity_name": "",
        "visit_date": on.isoformat(),
        "status": "approved",
        "reason": None,
        "location": location,
        "flagged": False,
        "flag_reason": {},
        "form_json": {"id": xform, "form": body},
        "completed_work": "",
        "status_modified_date": f"{on.isoformat()}T15:00:00Z",
        "review_status": "",
        "review_created_on": None,
        "justification": None,
        "date_created": f"{on.isoformat()}T09:00:00Z",
        "completed_work_id": None,
        "images": [],
    }


def build_world(start: date, *, weeks: int = WEEKS, seed: int = 11) -> World:
    """Every issue and handover visit, deterministically. Pure: no database.

    Week 0's first day each worker is issued FIRST_ISSUE phones; in week 2 they
    are topped up to their village's share. Each working day (Monday to
    Saturday from `start`) a worker hands over HANDOVERS_PER_DAY phones until
    their share of the village's adults is reached or their bag is empty.
    """
    rng = random.Random(seed)
    world = World(start=start, uuids={w: str(uuid.UUID(int=rng.getrandbits(128))) for w in WORKERS})
    share = {}
    for w, village in WORKERS.items():
        adults = next(v[2] for v in VILLAGES if v[0] == village)
        share[w] = adults // 2
    # The phones rejected at inspection were Majengo's last six: those adults wait for the replacements.
    share["chw-jabari"] -= REJECTED
    world.issues.append({"on": start, "lines": dict.fromkeys(WORKERS, FIRST_ISSUE)})
    top_up_day = start + timedelta(weeks=2)
    held = dict.fromkeys(WORKERS, FIRST_ISSUE)
    given = dict.fromkeys(WORKERS, 0)
    next_id = 810_000_001
    issued_top_up = False
    for day_n in range(weeks * 7):
        on = start + timedelta(days=day_n)
        if on == top_up_day and not issued_top_up:
            lines = {w: share[w] - FIRST_ISSUE for w in WORKERS}
            world.issues.append({"on": on, "lines": lines})
            for w, q in lines.items():
                held[w] += q
            issued_top_up = True
        if on.weekday() == 6:  # Sunday
            continue
        for w, village in WORKERS.items():
            for _ in range(HANDOVERS_PER_DAY):
                if held[w] <= 0 or given[w] >= share[w]:
                    break
                held[w] -= 1
                given[w] += 1
                balance = held[w] - (1 if w == ONE_SHORT and given[w] > 60 else 0)
                answers = {
                    PATHS["given"]: "1",
                    PATHS["imei"]: _imei(rng),
                    PATHS["village"]: village,
                    PATHS["age"]: str(rng.randint(18, 78)),
                    PATHS["consent"]: "yes",
                    PATHS["balance"]: str(balance),
                }
                world.visits.append(_visit(next_id, w, world.uuids[w], on, answers, _location(rng, village)))
                next_id += 1
    return world


def fixtures(world: World, opportunity_id: int) -> dict:
    return {
        "opportunity": {"id": opportunity_id, "name": SPEC["label"]},
        "user_visits": [{**v, "opportunity_id": opportunity_id} for v in world.visits],
        "user_data": [{"username": w, "name": w} for w in WORKERS],
        "completed_works": [],
        "completed_module": [],
    }


def rule_data(item_id: int, resupply_point_id: int, start: date, *, opportunity_id: int) -> dict:
    """One phone per handover, stated by the form; the app's own count of what the worker still holds."""
    return {
        "opportunity_id": opportunity_id,
        "item_id": item_id,
        "resupply_point_id": resupply_point_id,
        "active_from": start.isoformat(),
        "forms": [XMLNS],
        "lines": [{"kind": "stated", "paths": [PATHS["given"]], "unit": "phone"}],
        "reports": {"balance_paths": [PATHS["balance"]]},
    }


def seed(*, drive, reset: bool = False, today: date | None = None, admin=None) -> dict:
    """Seed (or, with reset, rebuild) the phones programme. Server-side only.

    A second run without `reset` changes nothing. `reset` purges the programme's
    supply data (`purge` refuses any programme that is not a registered labs-only
    one) and seeds it again with a fresh fixture folder, dated from today.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.labs.synthetic import registry
    from connect_labs.labs.synthetic.generator.io.uploader import upload_and_register
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.demo.org_procurement import ensure_programme
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import Tender
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services.visit_source import SYNTHETIC_TOKEN
    from connect_labs.supply_chain.stock.services.workers import worker_slug

    today = today or timezone.localdate()
    start = today - timedelta(weeks=WEEKS)
    world = build_world(start)

    with transaction.atomic():
        program_id, org = ensure_programme(KEY, SPEC, admin=admin)
    opp_id = program_id
    access = SupplyDataAccess(access_token=SYNTHETIC_TOKEN, program_id=opp_id, opportunity_id=opp_id, caller=SYSTEM)
    if Tender.objects.filter(program_id=opp_id).exists():
        if not reset:
            return {"program_id": opp_id, "seeded": False, "reason": "already seeded; pass --reset to rebuild it"}
        access.purge()

    upload_and_register(
        drive=drive, opportunity_id=opp_id, opportunity_name=SPEC["label"], fixtures=fixtures(world, opp_id)
    )
    registry.invalidate_cache()

    def op(day, hour, name, **payload):
        at = timezone.make_aware(datetime.combine(day, time(hour)))
        with seed_overrides(opp_id, channel="command", recorded_at=at):
            return call_operation(name, access, payload, acting_org_id=org.pk)

    with transaction.atomic():
        chain = _procure(op, org, start)
    store_id, item_id = chain["store"], chain["item"]
    op(chain["received_on"], 12, "dispensing_rule_upsert",
       data=rule_data(item_id, store_id, start, opportunity_id=opp_id))  # fmt: skip
    for username, village in WORKERS.items():
        op(chain["received_on"], 13, "supply_point_upsert", data={
            "slug": worker_slug(opp_id, username), "name": username, "kind": "user_held",
            "opportunity_id": opp_id, "connect_username": username, "connect_user_uuid": world.uuids[username],
            "parent_supply_point_id": store_id, "source": "connect_visit", "note": f"{NOTE} Village: {village}.",
        })  # fmt: skip

    weeks = []
    for week in range(WEEKS):
        first_day = start + timedelta(weeks=week)
        for issue in world.issues:
            if first_day <= issue["on"] < first_day + timedelta(weeks=1):
                op(issue["on"], 8, "distribution_record", data={
                    "supply_point_id": store_id, "opportunity_id": opp_id, "commodity_slug": SLUG,
                    "distributed_on": issue["on"].isoformat(), "source": "we_recorded",
                    "lines": [
                        {"connect_username": u, "item_id": item_id, "quantity": str(q), "quantity_unit": "phone"}
                        for u, q in issue["lines"].items() if q
                    ],
                })  # fmt: skip
                if issue is world.issues[-1]:
                    # The last phones out of the office: the store counted empty that evening, so the
                    # store is a counted place and not one that "never reported stock".
                    op(issue["on"], 17, "stock_count_record", data={
                        "supply_point_id": store_id, "item_id": item_id, "commodity_slug": SLUG,
                        "kind": "physical_count", "counted_on": issue["on"].isoformat(), "quantity": "0",
                        "quantity_unit": "phone", "source": "we_recorded", "note": NOTE,
                    })  # fmt: skip
        last_day = first_day + timedelta(days=6)
        report = op(last_day, 20, "visit_consumption_ingest", opportunity_id=opp_id, until=last_day.isoformat())
        weeks.append({"week_of": first_day.isoformat(), "posted": report["posted"]})

    return {
        "program_id": opp_id,
        "org": org.name,
        "org_slug": org.slug,
        "seeded": True,
        "start": start.isoformat(),
        "adults": ADULTS,
        "handovers": len(world.visits),
        "tenders": chain["tenders"],
        "orders": chain["orders"],
        "weeks": weeks,
    }


def _attach(op, day, kind, shipment_id, title, source):
    """A document on the shipment: a short text file that says it is invented."""
    import base64

    op(day, 15, "document_attach", data={
        "kind": kind, "shipment_id": shipment_id, "title": f"{title} (invented for the demo)",
        "filename": f"{kind.replace('_', '-')}-demo.txt", "content_type": "text/plain",
        "content_base64": base64.b64encode(NOTE.encode()).decode(), "source": source, "note": NOTE,
    })  # fmt: skip


def _procure(op, org, start):
    """The LLO's own procurement: tender, two quotes and a silence, award, order, delivery, inspection, invoice."""
    opened = start - timedelta(days=35)
    op(opened, 9, "commodity_upsert", data={
        "slug": SLUG, "name": "Smartphone", "category": "equipment",
        "base_unit": "phone", "pack_unit": "box", "base_per_pack": 1,
    })  # fmt: skip
    item = op(opened, 9, "item_upsert", data={
        "sku": SKU, "name": ITEM_NAME, "commodity_slug": SLUG,
        "base_unit": "phone", "pack_unit": "box", "base_per_pack": 1,
    })  # fmt: skip
    slug, name, lat, lon = STORE
    store = op(opened, 9, "supply_point_upsert", data={
        "slug": slug, "name": name, "kind": "central_store", "source": "we_recorded",
        "latitude": lat, "longitude": lon, "note": NOTE,
    })  # fmt: skip
    suppliers = {
        key: op(opened, 9, "supplier_create", data={
            "name": sname, "country": "KE", "city": city, "type": "distributor", "status": "contacted",
            "contacts": [{"name": contact, "email": email}],
        })["id"]  # fmt: skip
        for key, sname, city, contact, email in SUPPLIERS
    }
    delivery = {"key": "kilifi", "name": "Tumaini office store", "city": "Kilifi", "country": "KE",
                "country_name": "Kenya"}  # fmt: skip
    tender = op(opened, 10, "tender_create", data={
        "label": f"Smartphones for three villages: {ADULTS:,} phones",
        "lines": [{"commodity_slug": SLUG, "quantity": ADULTS, "quantity_unit": "phone"}],
        "delivery_points": [delivery], "incoterm_requested": "DAP Kilifi", "reminder_interval_days": 5,
        "response_deadline": (opened + timedelta(days=10)).isoformat(), "visibility": "private",
        "notes_to_supplier": f"One phone for every adult in Kibaoni, Mwembeni and Majengo. {SPEC_TEXT} "
                             "Please quote per phone, VAT shown separately.",
    })["id"]  # fmt: skip
    op(opened, 10, "tender_open", tender_id=tender)
    asked = {
        key: op(opened, 10, "outreach_log", data={
            "tender_id": tender, "supplier_id": sid, "channel": "manual", "sent_on": opened.isoformat(),
        })["id"]  # fmt: skip
        for key, sid in suppliers.items()
    }

    def quote(day, key, price, ref, **extra):
        q = op(day, 11, "quote_record", data={
            "tender_id": tender, "supplier_id": suppliers[key], "commodity_slug": SLUG,
            "as_quoted_amount": price, "as_quoted_unit": "per_pack", "as_quoted_currency": "KES",
            "quantity_basis": ADULTS, "quantity_basis_unit": "phone", "received_on": day.isoformat(),
            "supplier_reference": ref, "notes": NOTE, **extra,
        })  # fmt: skip
        op(day, 11, "outreach_update", outreach_id=asked[key], data={
            "responded": True, "response_kind": "quote", "responded_on": day.isoformat(),
        })  # fmt: skip
        return q

    win = quote(opened + timedelta(days=3), "pwani", "7400", "PMD-Q-2207", freight_basis="included",
                lead_time_days=14, incoterm="DAP Kilifi", pack_spec_source="stated_on_quote", base_per_pack_stated=1,
                validity_until=(opened + timedelta(days=30)).isoformat())  # fmt: skip
    quote(opened + timedelta(days=6), "bahari", "7150", "BEW-1184", freight_basis="not_specified",
          lead_time_days=21, incoterm="EXW Mombasa", pack_spec_source="stated_on_quote", base_per_pack_stated=1,
          validity_until=(opened + timedelta(days=30)).isoformat())  # fmt: skip
    op(opened + timedelta(days=5), 10, "outreach_update", outreach_id=asked["lakeview"],
       data={"last_reminder_on": (opened + timedelta(days=5)).isoformat()})  # fmt: skip

    awarded = opened + timedelta(days=11)
    award = op(awarded, 12, "award_create", tender_id=tender, quote_id=win["id"],
               rationale="Delivered to our office with the specification and warranty stated; the cheaper quote "
                         "is ex-works Mombasa with freight left open.",
               decided_by="Programme lead", decided_on=awarded.isoformat())  # fmt: skip
    price = Decimal("7400")
    goods = price * ADULTS
    order = op(awarded, 12, "contract_create", data={
        "tender_id": tender, "award_id": award["id"], "supplier_id": suppliers["pwani"], "commodity_slug": SLUG,
        "item_id": item["id"], "buyer_of_record": "programme_org", "buyer_org_id": org.pk,
        "reference": "TCN-PO-0001", "status": "placed", "currency": "KES", "quantity": ADULTS,
        "quantity_unit": "phone", "unit_price": str(price), "unit_price_unit": "per_pack", "freight_basis": "included",
        "duties_basis": "included", "vat_basis": "excluded", "vat_amount": str(goods * VAT_RATE),
        "incoterm": "DAP Kilifi",
        "delivery_supply_point_id": store["id"], "promised_lead_time_days": 14, "payment_terms": "advance",
        "source": "we_recorded", "signed_on": awarded.isoformat(), "note": NOTE,
    })["id"]  # fmt: skip
    advance = op(awarded, 13, "payment_record", data={
        "contract_id": order, "paid_on": awarded.isoformat(), "amount": str(goods / 2), "currency": "KES",
        "reference": "advance on PMD-Q-2207", "source": "we_recorded", "note": NOTE,
    })["id"]  # fmt: skip
    dispatched = awarded + timedelta(days=12)
    shipment = op(dispatched, 14, "shipment_record", data={
        "contract_id": order, "reference": "TCN-PO-0001", "status": "dispatched",
        "dispatched_on": dispatched.isoformat(), "expected_on": (dispatched + timedelta(days=2)).isoformat(),
        "carrier": "Coast Couriers", "source": "supplier_reported", "note": NOTE,
        "lines": [{"item_id": item["id"], "batch": "PMD-A13-2609", "quantity": ADULTS, "quantity_unit": "phone"}],
    })["id"]  # fmt: skip
    # The paperwork a tidy delivery carries, so the checks read only what the story means them to:
    # the distributor's certificate of conformity with the consignment, and our signed delivery note.
    _attach(op, dispatched, "certificate_of_conformity", shipment, "Certificate of conformity, PMD-A13-2609",
            "supplier_reported")  # fmt: skip
    received_on = start - timedelta(days=2)
    op(received_on, 9, "shipment_update", shipment_id=shipment, data={"status": "delivered"})
    _attach(op, received_on, "proof_of_delivery", shipment, "Delivery note signed at the Kilifi office",
            "we_recorded")  # fmt: skip
    op(received_on, 10, "receipt_record", data={
        "contract_id": order, "shipment_id": shipment, "supply_point_id": store["id"],
        "received_on": received_on.isoformat(), "reference": "TCN-GRN-0001", "source": "we_recorded",
        "lines": [{"item_id": item["id"], "batch": "PMD-A13-2609", "quantity_accepted": ADULTS - REJECTED,
                   "quantity_rejected": REJECTED, "rejection_reason": "Would not power on at inspection",
                   "quantity_unit": "phone"}],
    })  # fmt: skip
    invoiced = received_on + timedelta(days=1)
    op(invoiced, 11, "invoice_record", data={
        "contract_id": order, "reference": "PMD-INV-0931", "issued_on": invoiced.isoformat(), "status": "received",
        "currency": "KES", "amount": str(goods), "quantity_billed": ADULTS, "quantity_unit": "phone",
        "unit_price": str(price), "acknowledges_payment_ids": [advance], "source": "supplier_reported", "note": NOTE,
    })  # fmt: skip
    return {"store": store["id"], "item": item["id"], "tenders": [tender], "orders": [order],
            "received_on": received_on}  # fmt: skip
