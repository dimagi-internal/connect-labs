"""Two organisations that run their own procurement, neither of them Dimagi (#2458).

Each is a labs-only programme with its running organisation DECLARED before
anything else is written, so every record below belongs to that organisation:
it is the buyer of record, "we" on its pages, and its admins hold programme
standing there. Dimagi plays no part in either.

- `oes`: Operation End Starvation as a pooled buyer. OES does not deliver; it
  contracts implementers, and its stated procurement principle is
  cost-effectiveness and transparency across a diverse range of producers
  (docs/superpowers/specs/2026-09-24-oes-demo-environment-design.md §0). So its
  programme is a pooled RUTF procurement: tender to producers, compare landed
  cost, award, order as buyer of record, deliver into the store its
  implementers draw from. The RUTF chain is `clone_upstream.seed_upstream`,
  the same chain every synthetic clone carries, now bought by its runner.
- `llo`: an invented Kenyan community-health organisation delivering a
  diarrhoea and safe-water opportunity, buying its own chlorine tablets and
  ORS/zinc co-packs from local distributors -- the product owner's "the orgs
  themselves ... get bids for opps they are doing". Different in kind from
  OES on every axis: an implementer not a funder, small not pooled, local
  distributors not international producers, another country and basket.

Invented, all of it: every organisation (OES's name aside), supplier, contact,
price, reference and date. THIS REPOSITORY IS PUBLIC: no real person, price or
supplier is named here.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

NOTE = "Invented for the demo: an organisation running its own procurement on labs; nothing here is real."

SPECS = {
    "oes": {
        "label": "OES pooled RUTF procurement (demo)",
        "org_name": "Operation End Starvation (demo)",
        "program_name": "OES pooled RUTF procurement (demo)",
        "domain": "@oes.example",
        "admin": ("dev-oes-admin", "oes-admin@oes.example", "Amara", "Okafor"),
    },
    "llo": {
        "label": "Kilele safe water and diarrhoea (demo)",
        "org_name": "Kilele Community Health (demo)",
        "program_name": "Kilele safe water and diarrhoea (demo)",
        "domain": "@kilele.example",
        "admin": ("dev-kilele-admin", "admin@kilele.example", "Wanjiru", "Mutua"),
    },
}

# key, name, country, city, type, contact, email -- all invented.
LLO_SUPPLIERS = [
    ("pwani", "Pwani Medical Distributors", "KE", "Mombasa", "distributor", "Hassan Omar", "orders@pwani-med.example"),
    ("rift", "Rift Valley Pharma Supplies", "KE", "Nakuru", "distributor", "Grace Chebet", "grace@rvps.example"),
    ("lakeview", "Lakeview Health Traders", "KE", "Kisumu", "trader", "Otieno Ochieng", "sales@lakeview.example"),
]


def ensure_admin(spec):
    """The seeded admin, a LOCAL user only: `.example` address, `dev-` handle, no usable password."""
    from django.contrib.auth import get_user_model

    username, email, first, last = spec["admin"]
    user, _ = get_user_model().objects.get_or_create(
        username=username, defaults={"email": email, "name": f"{first} {last}"}
    )
    user.view_synthetic_opps = True
    user.set_unusable_password()
    user.save()
    return user


def _domains(spec):
    """Who may see it: the organisation's own domain, and Dimagi's -- so platform support can help.

    Seeing is not acting: a Dimagi address reads it as a partner, and only
    staff act there, for the organisation (#2458).
    """
    return [spec["domain"], "@dimagi.com"]


def ensure_org(key, spec):
    """The example organisation: a labs-only row under a fixed key this seeder owns.

    Not the registry's name-bucket filing, which can hand back another person's
    organisation of the same name -- and whatever is declared here runs a programme.
    """
    from connect_labs.labs.models import LABS_ONLY_ORG_PREFIX, LabsOrg

    org, _ = LabsOrg.objects.get_or_create(
        slug=f"{LABS_ONLY_ORG_PREFIX}demo-{key}", defaults={"name": spec["org_name"], "labs_only": True}
    )
    if not org.labs_only or org.connect_organization_id is not None:
        raise ValueError(f"{org.slug} is not a labs-only organisation this seeder made; refusing to run it")
    return org


def ensure_programme(key, spec, *, admin=None):
    """Register (or find) the labs-only programme under the example organisation, and declare it its runner.

    Refuses an opportunity with this label filed under any other organisation:
    a label is free text anyone can type. Without an admin (labs), the real
    admin is added later by platform support -- a running organisation takes no
    invitations (#2458).
    """
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.labs.synthetic.provenance import mark_generated
    from connect_labs.labs.synthetic.provisioning import register_labs_only_opp
    from connect_labs.marketplace.models import OrgMembership
    from connect_labs.supply_chain.runner import declare

    org = ensure_org(key, spec)
    existing = SyntheticOpportunity.objects.filter(labs_only=True, label=spec["label"]).first()
    if existing is not None and existing.labs_org_id != org.pk:
        raise ValueError(f"{spec['label']!r} is already registered under another organisation; refusing to seed it")
    if existing is None:
        opp_id = SyntheticOpportunity.next_labs_only_opp_id()
        # Filed under the example organisation before its first save, so the
        # registry never files it by name.
        SyntheticOpportunity(
            opportunity_id=opp_id, labs_only=True, enabled=True, label=spec["label"], labs_org=org,
            org_name=spec["org_name"], program_name=spec["program_name"], program_id=opp_id,
            allowed_domains=_domains(spec), gdrive_folder_id="", created_by=admin,
        ).save()  # fmt: skip
    else:
        opp_id = existing.opportunity_id
    opp = register_labs_only_opp(
        opportunity_id=opp_id,
        label=spec["label"],
        gdrive_folder_id="",
        org_name=spec["org_name"],
        program_name=spec["program_name"],
        program_id=opp_id,
        allowed_domains=_domains(spec),
    )
    mark_generated(opp.opportunity_id, "")
    if admin is not None:
        OrgMembership.objects.get_or_create(org=org, user=admin, defaults={"role": "admin"})
    program_id = opp.program_id or opp.opportunity_id
    declare(program_id, org, note=NOTE)
    return program_id, org


def _op_for(access, org):
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.operations import call_operation

    def op(day, hour, name, **payload):
        at = timezone.make_aware(datetime.combine(day, time(hour)))
        with seed_overrides(access.program_id, channel="command", recorded_at=at):
            return call_operation(name, access, payload, acting_org_id=org.pk)

    return op


def seed(key: str, *, today: date | None = None, admin=None) -> dict:
    """Seed one organisation's programme. Refuses a programme that already holds supply rows."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.models import Tender

    spec = SPECS[key]
    today = today or timezone.localdate()
    with transaction.atomic():
        program_id, org = ensure_programme(key, spec, admin=admin)
        if Tender.objects.filter(program_id=program_id).exists():
            return {"program_id": program_id, "org": org.name, "seeded": False, "reason": "already seeded"}
        access = SupplyDataAccess(access_token="synthetic", program_id=program_id, caller=SYSTEM)
        op = _op_for(access, org)
        chain = _seed_oes(op, program_id, today) if key == "oes" else _seed_llo(op, program_id, org, today)
    return {"program_id": program_id, "org": org.name, "org_slug": org.slug, "seeded": True, **chain}


def _seed_oes(op, program_id, today):
    from connect_labs.supply_chain.demo.clone_upstream import seed_upstream
    from connect_labs.supply_chain.models import Item

    setup = today - timedelta(days=45)
    op(setup, 9, "commodity_upsert", data={
        "slug": "rutf", "name": "RUTF (ready-to-use therapeutic food)", "category": "therapeutic_food",
        "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150,
    })  # fmt: skip
    item = op(setup, 9, "item_upsert", data={
        "sku": "RUTF-92G-150", "name": "RUTF 92 g sachet, carton of 150", "commodity_slug": "rutf",
        "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150,
    })  # fmt: skip
    central = op(setup, 9, "supply_point_upsert", data={
        "slug": "oes-pooled-store", "name": "OES pooled store, Maiduguri", "kind": "central_store",
        "source": "we_recorded", "min_months_of_stock": "2", "max_months_of_stock": "6", "note": NOTE,
    })  # fmt: skip
    for slug, name in (
        ("implementer-a", "Implementer A store, Damaturu"),
        ("implementer-b", "Implementer B store, Yola"),
    ):
        op(setup, 9, "supply_point_upsert", data={
            "slug": slug, "name": name, "kind": "regional_store", "parent_supply_point_id": central["id"],
            "source": "partner_reported", "min_months_of_stock": "1", "max_months_of_stock": "3", "note": NOTE,
        })  # fmt: skip
    chain = seed_upstream(
        op, program_id=program_id, central_id=central["id"], item=Item.objects.get(pk=item["id"]),
        opening_sachets=150 * 1200, setup=setup, today=today,
    )  # fmt: skip
    return {"tenders": chain["tenders"], "orders": chain["orders"], "suppliers": chain["suppliers"]}


def _seed_llo(op, program_id, org, today):
    start = today - timedelta(days=40)
    op(start, 9, "commodity_upsert", data={
        "slug": "chlorine-tablets", "name": "Chlorine tablets (NaDCC 67 mg)", "category": "consumable",
        "base_unit": "tablet", "pack_unit": "box", "base_per_pack": 100,
    })  # fmt: skip
    op(start, 9, "commodity_upsert", data={
        "slug": "ors-zinc", "name": "ORS and zinc co-pack", "category": "oral_rehydration",
        "base_unit": "co-pack", "pack_unit": "carton", "base_per_pack": 50,
    })  # fmt: skip
    tabs = op(start, 9, "item_upsert", data={
        "sku": "NADCC-67-100", "name": "NaDCC 67 mg, box of 100", "commodity_slug": "chlorine-tablets",
        "base_unit": "tablet", "pack_unit": "box", "base_per_pack": 100,
    })  # fmt: skip
    store = op(start, 9, "supply_point_upsert", data={
        "slug": "kilele-store", "name": "Kilele office store, Kilifi", "kind": "central_store",
        "source": "we_recorded", "min_months_of_stock": "1", "max_months_of_stock": "3", "note": NOTE,
    })  # fmt: skip
    suppliers = {
        key: op(start, 9, "supplier_create", data={
            "name": name, "country": country, "city": city, "type": kind, "status": "contacted",
            "contacts": [{"name": contact, "email": email}],
        })["id"]  # fmt: skip
        for key, name, country, city, kind, contact, email in LLO_SUPPLIERS
    }
    delivery = {"key": "kilifi", "name": "Kilele office store", "city": "Kilifi", "country": "KE",
                "country_name": "Kenya"}  # fmt: skip

    def tender(label, slug, qty, unit, opened, deadline):
        t = op(opened, 10, "tender_create", data={
            "label": label, "lines": [{"commodity_slug": slug, "quantity": qty, "quantity_unit": unit}],
            "delivery_points": [delivery], "incoterm_requested": "DAP Kilifi", "reminder_interval_days": 5,
            "response_deadline": deadline.isoformat(), "visibility": "private",
            "notes_to_supplier": "Please quote delivered to our Kilifi office, VAT shown separately.",
        })  # fmt: skip
        op(opened, 10, "tender_open", tender_id=t["id"])
        return t["id"]

    def ask(day, tender_id):
        return {
            key: op(day, 10, "outreach_log", data={
                "tender_id": tender_id, "supplier_id": sid, "channel": "manual", "sent_on": day.isoformat(),
            })["id"]  # fmt: skip
            for key, sid in suppliers.items()
        }

    def quote(day, tender_id, supplier, price, qty, unit, slug, ref, outreach, **extra):
        q = op(day, 11, "quote_record", data={
            "tender_id": tender_id, "supplier_id": suppliers[supplier], "commodity_slug": slug,
            "as_quoted_amount": price, "as_quoted_unit": "per_pack", "as_quoted_currency": "KES",
            "quantity_basis": qty, "quantity_basis_unit": unit, "received_on": day.isoformat(),
            "supplier_reference": ref, "notes": NOTE, **extra,
        })  # fmt: skip
        op(day, 11, "outreach_update", outreach_id=outreach, data={
            "responded": True, "response_kind": "quote", "responded_on": day.isoformat(),
        })  # fmt: skip
        return q

    # Tender 1: chlorine tablets -- quoted, compared, awarded, ordered, paid in advance, on the road.
    t1_open = start + timedelta(days=1)
    t1 = tender("Chlorine tablets: 600 boxes", "chlorine-tablets", 600, "box", t1_open, t1_open + timedelta(days=10))
    asked = ask(t1_open, t1)
    win = quote(t1_open + timedelta(days=3), t1, "pwani", "1450", 600, "box", "chlorine-tablets", "PMD-Q-3381",
                asked["pwani"], freight_basis="included", lead_time_days=10, incoterm="DAP Kilifi",
                pack_spec_source="stated_on_quote", base_per_pack_stated=100,
                validity_until=(t1_open + timedelta(days=33)).isoformat())  # fmt: skip
    quote(t1_open + timedelta(days=5), t1, "rift", "1390", 600, "box", "chlorine-tablets", "RVPS-0912",
          asked["rift"], freight_basis="not_specified", lead_time_days=14, incoterm="EXW Nakuru",
          pack_spec_source="not_stated", validity_until=(t1_open + timedelta(days=35)).isoformat())  # fmt: skip
    awarded = t1_open + timedelta(days=12)
    award = op(awarded, 12, "award_create", tender_id=t1, quote_id=win["id"],
               rationale="Delivered to Kilifi with the pack size stated; the cheaper quote left freight open.",
               decided_by="Programme lead", decided_on=awarded.isoformat())  # fmt: skip
    goods = 1450 * 600
    order = op(awarded, 12, "contract_create", data={
        "tender_id": t1, "award_id": award["id"], "supplier_id": suppliers["pwani"],
        "commodity_slug": "chlorine-tablets", "item_id": tabs["id"], "buyer_of_record": "programme_org",
        "buyer_org_id": org.pk, "reference": "KCH-PO-0001", "status": "placed", "currency": "KES",
        "quantity": 600, "quantity_unit": "box", "unit_price": "1450", "unit_price_unit": "per_pack",
        "freight_basis": "included", "duties_basis": "included", "vat_basis": "excluded", "incoterm": "DAP Kilifi",
        "delivery_supply_point_id": store["id"], "promised_lead_time_days": 10, "payment_terms": "advance",
        "source": "we_recorded", "signed_on": awarded.isoformat(), "note": NOTE,
    })["id"]  # fmt: skip
    op(awarded, 13, "payment_record", data={
        "contract_id": order, "paid_on": awarded.isoformat(), "amount": str(goods // 2), "currency": "KES",
        "reference": "advance on PMD-Q-3381", "source": "we_recorded", "note": NOTE,
    })  # fmt: skip
    out = min(awarded + timedelta(days=6), today)
    op(out, 14, "shipment_record", data={
        "contract_id": order, "reference": "KCH-PO-0001", "status": "in_transit", "dispatched_on": out.isoformat(),
        "expected_on": (today + timedelta(days=3)).isoformat(), "carrier": "Coast Couriers",
        "source": "supplier_reported", "note": NOTE,
        "lines": [{"item_id": tabs["id"], "batch": "PMD-2610", "quantity": 600, "quantity_unit": "box"}],
    })  # fmt: skip

    # Tender 2: ORS/zinc co-packs -- open now, one quote in, two to chase.
    t2_open = today - timedelta(days=4)
    t2 = tender("ORS and zinc co-packs: 80 cartons", "ors-zinc", 80, "carton", t2_open, today + timedelta(days=6))
    asked2 = ask(t2_open, t2)
    quote(today - timedelta(days=1), t2, "lakeview", "5200", 80, "carton", "ors-zinc", "LHT-554", asked2["lakeview"],
          freight_basis="included", lead_time_days=7, incoterm="DAP Kilifi",
          validity_until=(today + timedelta(days=30)).isoformat())  # fmt: skip
    return {"tenders": [t1, t2], "orders": [order], "suppliers": len(suppliers)}
