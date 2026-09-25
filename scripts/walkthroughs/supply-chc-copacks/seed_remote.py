"""Server-side seed for the `supply-chc-copacks` walkthrough. Runs INSIDE labs.

``ensure_demo.py`` ships this file to the deployed labs worker and runs it
through ``manage.py shell``. It resets labs-only programme 10601 and rebuilds
the world the narrative starts from, entirely through ``call_operation`` -- the
same registry, schemas and validation the screens, the HTTP API and the MCP
tools use. There is no private write path here.

THIS REPOSITORY IS PUBLIC. Every organisation, person, product name, batch and
price below is invented. The shape of the chain is the programme's real CHC
procurement (see docs/superpowers/specs/2026-09-23-supply-field-use-cases.md);
none of its facts are.

What the narrative then PERFORMS on camera, and so must NOT exist here:
  - the co-pack order being placed (it is left as a draft),
  - the payment against the distributor's invoice,
  - the distributor, through its update link, confirming the order and that
    the payment arrived, and receiving and inspecting the batch,
  - the low-stock alert subscription,
  - the LLO's stock take that drops its store below its minimum,
  - the LLO collecting the resupply quantity from the warehouse, recorded by
    the distributor through the same link.

What is here is everything before that: the reference data, the round with the
distributor's co-pack options, the decision on which contents to buy, the
awards, the draft order, the distributor's invoice, the warehouse the
distributor runs for us, the LLO's store and three months of co-packs
dispensed from it -- and the update link, minted here so its raw token is never
typed into anything committed.

Why not the MCP: every write tool is rate-limited per user (30/min), four
sibling narratives seed as the same user at the same time, and this seed
alone is ~50 writes. The operations are the same ones; only the transport
differs.
"""

import json
from datetime import date, timedelta

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.alerts.models import AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links.models import UpdateLink

PROGRAMME_ID = 10601
MARK = "SUPPLY_CHC_COPACKS_RESULT"

access = SupplyDataAccess(program_id=PROGRAMME_ID, caller=SYSTEM)

# Every date is relative to the day of the render, so the figures the narrative
# reads (a consumption rate over the last 90 days, months of stock, a resupply
# quantity) are the same whichever day it is filmed.
TODAY_DATE = date.today()


def ago(days):
    return (TODAY_DATE - timedelta(days=days)).isoformat()


def op(name, **payload):
    return call_operation(name, access, payload)


# --- reset -----------------------------------------------------------------
# purge() refuses anything but a registered labs-only programme. Until the fix
# that makes purge take them too is deployed, it does not reach the update
# links or programme-wide alert subscriptions, so those go first: a reseed must
# not leave last take's link working. Harmless once purge does it itself.
UpdateLink.objects.filter(program_id=PROGRAMME_ID).delete()
AlertSubscription.objects.filter(program_id=PROGRAMME_ID).delete()
purged = access.purge()

# --- organisations (labs-wide, upserted by slug) ---------------------------
programme = op(
    "org_upsert",
    data={
        "slug": "connect-child-health-programme",
        "name": "Connect Child Health Programme",
        "country": "NG",
        "notes": "Invented programme organisation used by the supply walkthroughs.",
    },
)
harmattan = op(
    "org_upsert",
    data={
        "slug": "harmattan-health-supplies",
        "name": "Harmattan Health Supplies",
        "country": "NG",
        "notes": "Invented in-country distributor used by the supply walkthroughs.",
    },
)
sahel = op(
    "org_upsert",
    data={
        "slug": "sahel-community-health-initiative",
        "name": "Sahel Community Health Initiative",
        "country": "NG",
        "notes": "Invented local implementing partner (LLO) used by the supply walkthroughs.",
    },
)

# --- commodities ------------------------------------------------------------
op(
    "commodity_upsert",
    data={
        "slug": "ors",
        "name": "Oral rehydration salts",
        "category": "oral_rehydration",
        "base_unit": "sachet",
        "pack_unit": "box",
        "base_per_pack": 100,
        "course_definition": {
            "base_units_per_course": 2,
            "source": "as packed in the protocol co-pack: 2 sachets per diarrhoea episode",
        },
    },
)
op(
    "commodity_upsert",
    data={
        "slug": "zinc",
        "name": "Zinc sulfate dispersible tablet",
        "category": "micronutrient",
        "base_unit": "tablet",
        "pack_unit": "box",
        "base_per_pack": 100,
        "spec_requirements": [
            {
                "field": "zinc_mg",
                "operator": "==",
                "value": 20,
                "unit": "mg",
                "rationale": "the treatment protocol is 20 mg a day for ten days",
            }
        ],
        "course_definition": {
            "base_units_per_day": "1",
            "days_per_course": 10,
            "base_units_per_course": 10,
            "source": "treatment protocol: one 20 mg tablet a day for ten days",
        },
    },
)
op(
    "commodity_upsert",
    data={
        "slug": "ors-zinc-copack",
        "name": "ORS/zinc co-pack",
        "category": "oral_rehydration",
        "base_unit": "co-pack",
        "pack_unit": "carton",
        "base_per_pack": 50,
        "course_definition": {
            "base_units_per_course": 1,
            "source": "one co-pack is one child's diarrhoea treatment course",
        },
    },
)
op(
    "commodity_upsert",
    data={
        "slug": "vitamin-a",
        "name": "Vitamin A 200,000 IU",
        "category": "micronutrient",
        "base_unit": "capsule",
        "pack_unit": "bottle",
        "base_per_pack": 100,
        "course_definition": {"base_units_per_course": 1, "source": "one capsule is one six-monthly dose"},
    },
)
op(
    "commodity_upsert",
    data={
        "slug": "albendazole",
        "name": "Albendazole 400 mg (dewormer)",
        "category": "anthelmintic",
        "base_unit": "tablet",
        "pack_unit": "box",
        "base_per_pack": 100,
        "course_definition": {"base_units_per_course": 1, "source": "one tablet is one deworming dose"},
    },
)

# --- trade items: three co-packs the distributor can source -----------------
# A and B hold the protocol's contents; C holds twice the ORS. The kit is
# counted and priced as one SKU -- the co-pack -- and its components say what
# is inside it, which is what lets two co-packs be compared at all.
PROTOCOL = [
    {"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"},
    {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet", "spec_attributes": {"zinc_mg": 20}},
]
copack_a = op(
    "item_upsert",
    data={
        "sku": "kpw-orszinc-copack",
        "name": "Kaduna Pharma Works ORS/zinc co-pack",
        "commodity_slug": "ors-zinc-copack",
        "manufacturer": "Kaduna Pharma Works",
        "base_unit": "co-pack",
        "pack_unit": "carton",
        "base_per_pack": 50,
        "shelf_life_months": 24,
        "components": PROTOCOL,
        "one_course_is": "base_unit",
        "status": "active",
    },
)
copack_b = op(
    "item_upsert",
    data={
        "sku": "lg-orszinc-copack",
        "name": "Lagoon Generics ORS/zinc co-pack",
        "commodity_slug": "ors-zinc-copack",
        "manufacturer": "Lagoon Generics",
        "base_unit": "co-pack",
        "pack_unit": "carton",
        "base_per_pack": 50,
        "shelf_life_months": 24,
        "components": PROTOCOL,
        "one_course_is": "base_unit",
        "status": "active",
    },
)
copack_c = op(
    "item_upsert",
    data={
        "sku": "bls-orszinc-copack-4",
        "name": "Benue Life Sciences ORS/zinc co-pack (4 sachets)",
        "commodity_slug": "ors-zinc-copack",
        "manufacturer": "Benue Life Sciences",
        "base_unit": "co-pack",
        "pack_unit": "carton",
        "base_per_pack": 40,
        "shelf_life_months": 24,
        "components": [
            {"commodity_slug": "ors", "quantity": "4", "base_unit": "sachet"},
            {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet", "spec_attributes": {"zinc_mg": 20}},
        ],
        "one_course_is": "base_unit",
        "status": "active",
    },
)
vit_a = op(
    "item_upsert",
    data={
        "sku": "kpw-vita-200k",
        "name": "Kaduna Pharma Works vitamin A 200,000 IU",
        "commodity_slug": "vitamin-a",
        "manufacturer": "Kaduna Pharma Works",
        "base_unit": "capsule",
        "pack_unit": "bottle",
        "base_per_pack": 100,
        "shelf_life_months": 24,
        "status": "active",
    },
)
albendazole = op(
    "item_upsert",
    data={
        "sku": "lg-albendazole-400",
        "name": "Lagoon Generics albendazole 400 mg",
        "commodity_slug": "albendazole",
        "manufacturer": "Lagoon Generics",
        "base_unit": "tablet",
        "pack_unit": "box",
        "base_per_pack": 100,
        "shelf_life_months": 36,
        "status": "active",
    },
)

# --- the distributor --------------------------------------------------------
distributor = op(
    "supplier_create",
    data={
        "name": "Harmattan Health Supplies",
        "type": "distributor",
        "country": "NG",
        "city": "Kano",
        "status": "quoting",
        "org_id": harmattan["id"],
        "contacts": [{"name": "Orders desk", "role": "sales", "email": "orders@harmattan-health.example"}],
        "notes": "Finds manufacturers, offers product options, holds and releases stock for the programme.",
    },
)

# --- the round ----------------------------------------------------------------
round_rec = op(
    "tender_create",
    data={
        "label": "CHC 2026 — co-packs, vitamin A, dewormer",
        "lines": [
            {
                "commodity_slug": "ors-zinc-copack",
                "quantity": "30000",
                "quantity_unit": "co-pack",
                # The contents this round buys: the protocol co-pack. Kits
                # holding them are ranked; any other contents are refused.
                "components": [
                    {"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet"},
                ],
            },
            {"commodity_slug": "vitamin-a", "quantity": "40000", "quantity_unit": "capsule"},
            {"commodity_slug": "albendazole", "quantity": "25000", "quantity_unit": "tablet"},
        ],
        "delivery_point": {
            "name": "Harmattan warehouse",
            "city": "Kano",
            "country": "NG",
            "country_name": "Nigeria",
            "incoterm_requested": "DAP",
        },
        "response_deadline": ago(9),
        "notes_to_supplier": "Please offer every co-pack you can source, priced per co-pack.",
    },
)
round_id = round_rec["id"]
op("tender_open", tender_id=round_id)
op(
    "outreach_log",
    data={
        "tender_id": round_id,
        "supplier_id": distributor["id"],
        "channel": "manual",
        "sent_on": ago(23),
        "responded": True,
        "response_kind": "quote",
    },
)


def quote(item, commodity, amount, basis, unit, lead_days):
    return op(
        "quote_record",
        data={
            "tender_id": round_id,
            "supplier_id": distributor["id"],
            "item_id": item["id"],
            "commodity_slug": commodity,
            "as_quoted_amount": amount,
            "as_quoted_unit": "per_base_unit",
            "as_quoted_currency": "USD",
            "fx_rate_to_usd": "1",
            "quantity_basis": basis,
            "quantity_basis_unit": unit,
            "pack_spec_source": "trade_item_confirmed",
            "freight_basis": "included",
            "duties_basis": "included",
            "incoterm": "DAP",
            "shelf_life_months_stated": 24,
            "lead_time_days": lead_days,
            "validity_until": ago(-68),
        },
    )


q_a = quote(copack_a, "ors-zinc-copack", "0.60", "30000", "co-pack", 45)
q_b = quote(copack_b, "ors-zinc-copack", "0.64", "30000", "co-pack", 40)
q_c = quote(copack_c, "ors-zinc-copack", "0.55", "30000", "co-pack", 50)
q_vita = quote(vit_a, "vitamin-a", "0.021", "40000", "capsule", 30)
q_alb = quote(albendazole, "albendazole", "0.018", "25000", "tablet", 30)

# --- the awards ----------------------------------------------------------------
# The round states the contents it buys, so C -- four sachets, cheapest -- is
# refused a ranking by the comparison itself and stays on the page saying why.
# A and B rank; the lead chose A a week ago.
DECIDED = ago(7)
award_copack = op(
    "award_create",
    tender_id=round_id,
    quote_id=q_a["id"],
    rationale="Lowest landed cost of the two co-packs holding the protocol contents (2 ORS + 10 zinc 20 mg).",
    decided_by="Amara Bello",
    decided_on=DECIDED,
)
award_vita = op(
    "award_create",
    tender_id=round_id,
    quote_id=q_vita["id"],
    rationale="Only offer; comparable on every figure.",
    decided_by="Amara Bello",
    decided_on=DECIDED,
)
award_alb = op(
    "award_create",
    tender_id=round_id,
    quote_id=q_alb["id"],
    rationale="Only offer; comparable on every figure.",
    decided_by="Amara Bello",
    decided_on=DECIDED,
)

# --- the network: the distributor's warehouse and the LLO's store -------------
warehouse = op(
    "supply_point_upsert",
    data={
        "slug": "harmattan-warehouse-kano",
        "name": "Harmattan warehouse, Kano",
        "kind": "central_store",
        "managed_by_org_id": harmattan["id"],
        "admin_area": "Kano Municipal",
        # Rated on what it releases to partners, so it has a band and a
        # reorder figure of its own: this is where the programme reorders.
        "min_months_of_stock": "2",
        "max_months_of_stock": "6",
        "source": "we_recorded",
    },
)
llo_store = op(
    "supply_point_upsert",
    data={
        "slug": "sahel-chi-store-wudil",
        "name": "Sahel CHI store, Wudil",
        "kind": "regional_store",
        "managed_by_org_id": sahel["id"],
        "parent_supply_point_id": warehouse["id"],
        "admin_area": "Wudil",
        "min_months_of_stock": "3",
        "max_months_of_stock": "6",
        "source": "we_recorded",
    },
)


# The other partner stores the warehouse releases to. Sahel is one LLO among
# several, so the stock page, the alert and the resupply figure have to pick
# the one store that is short out of a network that is otherwise fine --
# which is the job they exist for. A two-point network (the render through
# iteration 4) made a well-kept spreadsheet look as good. Each is rated on
# its own dispensing and sits inside its band: (slug, name, area, co-packs a week).
PARTNER_STORES = [
    ("dala-store", "Dala community store", "Dala", 500),
    ("gezawa-store", "Gezawa community store", "Gezawa", 600),
    ("kura-store", "Kura community store", "Kura", 400),
    ("bichi-store", "Bichi community store", "Bichi", 800),
    ("rano-store", "Rano community store", "Rano", 300),
]
partner_stores = [
    (
        op(
            "supply_point_upsert",
            data={
                "slug": slug,
                "name": name,
                "kind": "facility",
                "parent_supply_point_id": warehouse["id"],
                "admin_area": area,
                "min_months_of_stock": "3",
                "max_months_of_stock": "6",
                "source": "we_recorded",
            },
        ),
        weekly,
    )
    for slug, name, area, weekly in PARTNER_STORES
]


def contract(award, item, commodity, qty, unit, price, reference, status, signed_on, lead_days=45):
    data = {
        "tender_id": round_id,
        "award_id": award["id"],
        "supplier_id": distributor["id"],
        "item_id": item["id"],
        "commodity_slug": commodity,
        # We order and pay the distributor ourselves: the programme is the
        # buyer of record, not the LLO (design doc section 17).
        "buyer_of_record": "programme_org",
        "buyer_org_id": programme["id"],
        "reference": reference,
        "quantity": qty,
        "quantity_unit": unit,
        "unit_price": price,
        "unit_price_unit": "per_base_unit",
        "currency": "USD",
        "freight_basis": "included",
        "duties_basis": "included",
        "vat_basis": "included",
        "incoterm": "DAP",
        "delivery_supply_point_id": warehouse["id"],
        "promised_lead_time_days": lead_days,
        "payment_terms": "advance",
        "status": status,
        "signed_on": signed_on,
        "source": "we_recorded",
    }
    # A draft is not signed yet: no date rather than a made-up one.
    return op("contract_create", data={key: value for key, value in data.items() if value is not None})


# --- last year's order: the stock the LLO has been dispensing -----------------
prior = op(
    "contract_create",
    data={
        "supplier_id": distributor["id"],
        "item_id": copack_a["id"],
        "commodity_slug": "ors-zinc-copack",
        "buyer_of_record": "programme_org",
        "buyer_org_id": programme["id"],
        "reference": "CHC-2025-03",
        "quantity": "212500",
        "quantity_unit": "co-pack",
        "unit_price": "0.62",
        "unit_price_unit": "per_base_unit",
        "currency": "USD",
        "freight_basis": "included",
        "duties_basis": "included",
        "vat_basis": "included",
        "incoterm": "DAP",
        "delivery_supply_point_id": warehouse["id"],
        "promised_lead_time_days": 45,
        "status": "received",
        "payment_terms": "advance",
        "signed_on": ago(280),
        "source": "we_recorded",
    },
)
op(
    "receipt_record",
    data={
        "contract_id": prior["id"],
        "supply_point_id": warehouse["id"],
        "reference": "GRN-HHS-0512",
        "received_on": ago(126),
        "source": "supplier_reported",
        "recorded_by_org_id": harmattan["id"],
        "lines": [
            {
                "item_id": copack_a["id"],
                "batch": "KPW-2504",
                "expiry": "2028-03-31",
                "quantity_accepted": "4250",
                "quantity_unit": "carton",
            }
        ],
    },
)
prior_invoice = op(
    "invoice_record",
    data={
        "contract_id": prior["id"],
        "reference": "HHS-INV-2025-031",
        "issued_on": ago(124),
        "amount": "131750.00",
        "currency": "USD",
        "quantity_billed": "212500",
        "quantity_unit": "co-pack",
        "source": "supplier_reported",
    },
)
prior_payment = op(
    "payment_record",
    data={
        "invoice_id": prior_invoice["id"],
        "paid_on": ago(117),
        "amount": "131750.00",
        "currency": "USD",
        "method": "bank transfer",
        "reference": "PAY-2025-031",
        "source": "we_recorded",
    },
)
op("payment_confirm", payment_id=prior_payment["id"], confirmed_on=ago(113))

# The LLO collected most of it over the summer, in two trips...
for occurred_on, cartons, reference in ((ago(90), "200", "REL-HHS-01"), (ago(42), "176", "REL-HHS-02")):
    op(
        "movement_record",
        data={
            "kind": "transfer",
            "occurred_on": occurred_on,
            "from_supply_point_id": warehouse["id"],
            "to_supply_point_id": llo_store["id"],
            "item_id": copack_a["id"],
            "commodity_slug": "ors-zinc-copack",
            "batch": "KPW-2504",
            "quantity": cartons,
            "quantity_unit": "carton",
            "reference": reference,
            "source": "supplier_reported",
            "recorded_by_org_id": harmattan["id"],
        },
    )
# ...and has dispensed ~700 co-packs a week since, as community health workers
# record treatments on their Connect visits: thirteen weeks, inside the 90-day
# window the consumption rate is averaged over, so the rate is 9,100 co-packs
# over 90 days -- about 3,033 a month -- on any day this is seeded.
for days_ago in range(89, 0, -7):
    op(
        "movement_record",
        data={
            "kind": "consumption",
            "occurred_on": ago(days_ago),
            "from_supply_point_id": llo_store["id"],
            "item_id": copack_a["id"],
            "commodity_slug": "ors-zinc-copack",
            "batch": "KPW-2504",
            "quantity": "700",
            "quantity_unit": "co-pack",
            "source": "connect_visit",
        },
    )

# The other stores collect twice in the same 90 days and dispense every week,
# ending about four and a half months into a three-to-six-month band. What
# they collect is 2.5 x thirteen weeks of dispensing, in cartons of 50.
for store, weekly in partner_stores:
    collected = weekly * 13 * 5 // 2 // 50
    first = collected * 3 // 5
    for occurred_on, cartons, n in ((ago(90), first, 1), (ago(42), collected - first, 2)):
        op(
            "movement_record",
            data={
                "kind": "transfer",
                "occurred_on": occurred_on,
                "from_supply_point_id": warehouse["id"],
                "to_supply_point_id": store["id"],
                "item_id": copack_a["id"],
                "commodity_slug": "ors-zinc-copack",
                "batch": "KPW-2504",
                "quantity": str(cartons),
                "quantity_unit": "carton",
                "reference": f"REL-HHS-{store['id'] % 100:02d}{n}",
                "source": "supplier_reported",
                "recorded_by_org_id": harmattan["id"],
            },
        )
    for days_ago in range(89, 0, -7):
        op(
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": ago(days_ago),
                "from_supply_point_id": store["id"],
                "item_id": copack_a["id"],
                "commodity_slug": "ors-zinc-copack",
                "batch": "KPW-2504",
                "quantity": str(weekly),
                "quantity_unit": "co-pack",
                "source": "connect_visit",
            },
        )

# --- this year's orders -------------------------------------------------------
# The chain the narrative performs took about a week in the world, and the
# render films it in minutes. So every act on camera is entered with the day it
# HAPPENED, relative to the render day, and the screens read those days rather
# than the minute they were typed (the order page once listed an order
# confirmed, a payment received and 600 cartons inspected all at "12:08"):
#
#   award                a week ago     (seeded)
#   order placed/signed  6 days ago     (scene 4)   Harmattan confirms it the same day (scene 6)
#   paid by transfer     5 days ago     (scene 5)   Harmattan sees it arrive a day later (scene 6)
#   goods received       yesterday      (scene 7)
#   stock take, release  today          (scenes 10, 13)
TODAY = TODAY_DATE.isoformat()
PLACED_ON = ago(6)
PAID_ON = ago(5)
PAYMENT_RECEIVED_ON = ago(4)
RECEIVED_ON = ago(1)

# The co-pack order is a DRAFT, and unsigned: placing it is the first thing the
# lead does on camera. Vitamin A and the dewormer were placed alongside it.
copack_order = contract(
    award_copack, copack_a, "ors-zinc-copack", "30000", "co-pack", "0.60", "CHC-2026-01", "draft", None
)
contract(award_vita, vit_a, "vitamin-a", "40000", "capsule", "0.021", "CHC-2026-02", "placed", PLACED_ON, 30)
contract(award_alb, albendazole, "albendazole", "25000", "tablet", "0.018", "CHC-2026-03", "placed", PLACED_ON, 30)

# The distributor invoices up front: we pay it before it pays the manufacturer,
# so the invoice arrives before a single carton has.
invoice = op(
    "invoice_record",
    data={
        "contract_id": copack_order["id"],
        "reference": "HHS-INV-2026-014",
        "issued_on": PLACED_ON,
        "amount": "18000.00",
        "currency": "USD",
        "quantity_billed": "30000",
        "quantity_unit": "co-pack",
        "source": "supplier_reported",
        "recorded_by_org_id": harmattan["id"],
    },
)

# --- the distributor's update link -----------------------------------------------
# Covers the co-pack order, the warehouse it runs for us and the LLO's store it
# releases to -- and nothing else. The raw token leaves this process once, in
# the result below, and is never written to anything committed.
link = op(
    "update_link_issue",
    data={
        "org_id": harmattan["id"],
        "contract_ids": [copack_order["id"]],
        "supply_point_ids": [warehouse["id"], llo_store["id"]],
        "expires_in_days": 14,
        "label": "Harmattan — CHC 2026 co-packs",
    },
)

print(
    MARK
    + json.dumps(
        {
            "program_id": PROGRAMME_ID,
            "today": TODAY,
            "placed_on": PLACED_ON,
            "paid_on": PAID_ON,
            "payment_received_on": PAYMENT_RECEIVED_ON,
            "received_on": RECEIVED_ON,
            "tender_id": round_id,
            "quote_c_id": q_c["id"],
            "decided_on": DECIDED,
            "award_copack_id": award_copack["id"],
            "copack_item_id": copack_a["id"],
            "copack_c_item_id": copack_c["id"],
            "copack_order_id": copack_order["id"],
            "invoice_id": invoice["id"],
            "warehouse_id": warehouse["id"],
            "llo_store_id": llo_store["id"],
            "update_link_path": "/supply/u/" + link["token"] + "/",
            "update_link_id": link["id"],
            "purged": purged,
        }
    )
    + MARK
)
