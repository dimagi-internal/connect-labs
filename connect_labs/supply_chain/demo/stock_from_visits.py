"""A synthetic RUTF opportunity whose visits use the released deliver app's form paths.

Design 2026-09-28 §7 and §9, as resolved from the released app (the plan's
"Resolved from the released app" section). Labs-only: opportunity id
>= 10,000, its own programme. Invented names only -- THIS REPOSITORY IS
PUBLIC. The paths and answer strings are the app's structure; no submission
was read or copied.

Everything is written through the same operations a person or an agent uses,
dated with `seed_overrides` so the history reads week by week: an as-of page
on week 3 shows week 3. Visits are uploaded as synthetic fixtures and read by
the real reader through `fetch_raw_visits`, so a rule written here transfers
to the real opportunity unchanged -- once the few STAND-IN paths (see
README) are confirmed against the released app.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

LABEL = "Stock from visits: synthetic RUTF (invented)"
ORG_NAME = "Harmattan Nutrition Partners (synthetic)"
PROGRAM_NAME = "Stock from visits demo (synthetic)"
ALLOWED_DOMAINS = ["@dimagi.com"]
WEEKS = 8
RATION = Decimal("14")
APPETITE = Decimal("1")  # the app deducts a whole sachet for the appetite test
FIRST_RUTF = Decimal("180")
SECOND_RUTF = Decimal("180")
RUNS_OUT_RUTF = Decimal("90")
UNRECORDED_RECEIPT = Decimal("100")

WORKERS = (
    "worker-acacia",
    "worker-baobab",
    "worker-cassia",
    "worker-doum",
    "worker-ebony",
    "worker-ficus",
    "worker-gmelina",
    "worker-hibiscus",
    "worker-iroko",
    "worker-jacaranda",
    "worker-kapok",
    "worker-locust",
    "worker-marula",
    "worker-neem",
    "worker-obeche",
    "worker-palm",
    "worker-quassia",
    "worker-raffia",
    "worker-sapele",
    "worker-tamarind",
)
RUNS_OUT = "worker-kapok"
OVER_COUNTS = "worker-marula"
REJECTED_LATER = "worker-neem"
NEVER_ANSWERS = "worker-sapele"

# The released app's form names, which the rules' `forms` filters name.
FORM_SCREENING, FORM_VISIT, FORM_STOCK = "Screening", "Visit Form", "Stock Management"

# form_json paths. Everything not marked STAND-IN is copied verbatim from the
# released app (plan addendum, items 2, 3, 6 and 11). The Vitamin A paths are
# elided there ("…vita_group", "…prepare_vita_dosage"); the stand-ins put them
# where the same app keeps albendazole (Screening `form.chc_commodities.dw_group`,
# Visit Form `form.visit_2_or_greater.dw_group`). Confirm them against the
# released app before copying the Vitamin A rules to a real opportunity.
PATHS = {
    # RUTF, Screening: the deduction is already the total (ration, or 1 for an appetite test alone).
    "rutf_screening_total": "form.screening_outcome.rutf_stock_deduction",
    "rutf_screening_ration": "form.visit_1.rutf_dispensing.rutf_sachets_dispensed",  # present, never summed
    # RUTF, Visit Form: the ration plus the appetite test's sachet (1 only when no ration was given).
    "rutf_visit": "form.rutf_dispensing.rutf_sachets_dispensed",
    "appetite_visit": "form.var.appetite_test_stock_deduction",
    # The worker's own stock.
    "balance": "form.var.new_stock_balance",
    "received": "form.current_stock.sachets_received",
    "received_on": "form.current_stock.date_received",
    "remaining": "form.stock_balance.sachets_remaining",
    # Amoxicillin DT.
    "amox_presumptive": "form.visit_1.presumptive_amoxicillin_given",
    "amox_fast_breathing": "form.visit_1.fast_breathing_treatment.dosage_pneumonia",
    "amox_comorbid": "form.comorbid_conditions.comorbid_question_list.cough_assessment.dosage_pneumonia",
    "amox_visit": "form.visit_2_or_greater.cough_assessment.dosage_pneumonia",
    # mRDT.
    "mrdt_screening": "form.visit_1.fever_treatment.mrdt_result",
    "mrdt_visit": "form.visit_2_or_greater.fever.mrdt_result",
    # Vitamin A -- every path below is a STAND-IN (see above).
    "vita_screening": "form.chc_commodities.vita_group.va_delivered",
    "vita_visit": "form.visit_2_or_greater.vita_group.va_delivered",
    "vita_6_11_screening": "form.chc_commodities.vita_group.prepare_vita_dosage.va_eligible_dose_6mo_to_11mo",
    "vita_1_2_screening": "form.chc_commodities.vita_group.prepare_vita_dosage.va_eligible_dose_1yr_2year",
    "vita_2_5_screening": "form.chc_commodities.vita_group.prepare_vita_dosage.va_eligible_dose_2yr_5yr",
    "vita_6_11_visit": "form.visit_2_or_greater.vita_group.prepare_vita_dosage.va_eligible_dose_6mo_to_11mo",
    "vita_1_2_visit": "form.visit_2_or_greater.vita_group.prepare_vita_dosage.va_eligible_dose_1yr_2year",
    "vita_2_5_visit": "form.visit_2_or_greater.vita_group.prepare_vita_dosage.va_eligible_dose_2yr_5yr",
}

# The app's own answer strings. The missing space in the second is the app's:
# a value_map matches text exactly, so it is kept.
AMOX_DOSES = {
    "1 tablet every 12 hours (total 10 tablets)": 10,
    "2 tablets every 12 hours(total 20 tablets)": 20,
}
# Presumptive amoxicillin's dose is a label in the app, not a field: a fixed protocol quantity.
AMOX_PRESUMPTIVE_TABLETS = 10
MRDT_RESULTS = ("positive", "negative", "invalid")
# What a dose question holds is not in the addendum; only that it was answered
# matters to the rule (requires_paths). STAND-IN value.
DOSE_ANSWERED = "OK"


@dataclass(frozen=True)
class Product:
    slug: str
    name: str
    category: str
    base: str
    pack: str
    per_pack: int
    sku: str
    opening: int  # into the central store, in base units
    first_issue: int  # to each worker in week 0, in base units


PRODUCTS = (
    Product(
        "rutf", "Ready-to-use therapeutic food (synthetic)", "therapeutic_food", "sachet", "carton", 150,
        "syn-rutf-150", 16000, 180,
    ),
    Product(
        "vitamin-a-100k", "Vitamin A 100,000 IU (synthetic)", "micronutrient", "capsule", "bottle", 100,
        "syn-vita100k-100", 1000, 20,
    ),
    Product(
        "vitamin-a-200k", "Vitamin A 200,000 IU (synthetic)", "micronutrient", "capsule", "bottle", 100,
        "syn-vita200k-100", 1000, 20,
    ),
    Product(
        "amoxicillin-dt", "Amoxicillin 250 mg dispersible (synthetic)", "antibiotic", "tablet", "pack", 100,
        "syn-amox-100", 10000, 200,
    ),
    Product(
        "mrdt", "Malaria rapid diagnostic test (synthetic)", "diagnostic", "test", "kit", 25,
        "syn-mrdt-25", 1000, 20,
    ),
)  # fmt: skip
BY_SKU = {p.sku: p for p in PRODUCTS}
RUTF_SKU = "syn-rutf-150"


@dataclass
class World:
    start: date
    visits: list = field(default_factory=list)
    issues: list = field(default_factory=list)  # [{"on": date, "sku": str, "lines": {username: Decimal}}]
    uuids: dict = field(default_factory=dict)


def _nest(answers: dict) -> dict:
    body: dict = {}
    for path, value in answers.items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return body


def _visit(visit_id, username, user_id, on, status, form_name, answers, modified=None):
    """One row shaped as Connect's user_visits export holds it."""
    xform = str(uuid.UUID(int=visit_id))
    return {
        "id": visit_id,
        "xform_id": xform,
        "username": username,
        "user_id": user_id,
        "deliver_unit": "",
        "deliver_unit_id": None,
        "entity_id": f"case-{visit_id}",
        "entity_name": "",
        "visit_date": on.isoformat(),
        "status": status,
        "reason": None,
        "location": "",
        "flagged": False,
        "flag_reason": {},
        "form_json": {"id": xform, "form": {"@name": form_name, **_nest(answers)}},
        "completed_work": "",
        "status_modified_date": f"{(modified or on).isoformat()}T12:00:00Z",
        "review_status": "",
        "review_created_on": None,
        "justification": None,
        "date_created": f"{on.isoformat()}T09:00:00Z",
        "completed_work_id": None,
        "images": [],
    }


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _rutf(rng, answers, screening, app, *, force_ration=False) -> Decimal:
    """Write the RUTF answers for one visit; return what left the bag by the app's own arithmetic."""
    if screening:
        if force_ration or rng.random() < 0.85:  # enrolled: the deduction is the ration
            ration = min(RATION, app)
            answers[PATHS["rutf_screening_ration"]] = _text(ration)
            answers[PATHS["rutf_screening_total"]] = _text(ration)
            return ration
        taken = min(APPETITE, app)  # not enrolled: the appetite test alone
        answers[PATHS["rutf_screening_total"]] = _text(taken)
        return taken
    if not force_ration and rng.random() < 0.1:  # no ration this visit, an appetite test instead
        ration, appetite = Decimal(0), min(APPETITE, app)
    else:
        ration, appetite = min(RATION, app), Decimal(0)
    answers[PATHS["rutf_visit"]] = _text(ration)
    answers[PATHS["appetite_visit"]] = _text(appetite)
    return ration + appetite


def _protocol(rng, answers, screening) -> None:
    """The protocol items' answers. A question the app did not show is absent, as in a real submission."""
    roll = rng.random()
    if roll < 0.2:
        where = "screening" if screening else "visit"
        dose = rng.choice(("vita_6_11", "vita_1_2", "vita_2_5"))
        answers[PATHS[f"vita_{where}"]] = "child_fine" if rng.random() < 0.9 else "child_unwell"
        answers[PATHS[f"{dose}_{where}"]] = DOSE_ANSWERED
    roll = rng.random()
    if screening and roll < 0.1:
        answers[PATHS["amox_presumptive"]] = "yes"
    elif roll < 0.22:
        if screening:
            path = PATHS["amox_fast_breathing"] if rng.random() < 0.7 else PATHS["amox_comorbid"]
        else:
            path = PATHS["amox_visit"]
        answers[path] = rng.choice(sorted(AMOX_DOSES))
    elif screening and roll < 0.3:
        answers[PATHS["amox_presumptive"]] = "no"
    if rng.random() < 0.25:
        answers[PATHS["mrdt_screening" if screening else "mrdt_visit"]] = rng.choice(MRDT_RESULTS)


def build_world(start: date, *, weeks: int = WEEKS, seed: int = 7) -> World:
    """Every visit and every issue, deterministically. Pure: no database.

    Each worker is issued stock on week 0's Monday (and RUTF again on week
    4's), files a Stock Management form the next day, and makes three visits
    a week (Tuesday, Thursday, Saturday); the first each week is a Screening.
    The phone keeps its own RUTF balance and writes it on every visit.
    """
    rng = random.Random(seed)
    world = World(start=start, uuids={w: str(uuid.UUID(int=rng.getrandbits(128))) for w in WORKERS})
    for product in PRODUCTS:
        lines = {w: Decimal(product.first_issue) for w in WORKERS}
        if product.sku == RUTF_SKU:
            lines[RUNS_OUT] = RUNS_OUT_RUTF
        world.issues.append({"on": start, "sku": product.sku, "lines": lines})
    world.issues.append(
        {
            "on": start + timedelta(weeks=4),
            "sku": RUTF_SKU,
            "lines": {w: SECOND_RUTF for w in WORKERS if w != RUNS_OUT},
        }
    )
    rutf_issues = {(i["on"], w): q for i in world.issues if i["sku"] == RUTF_SKU for w, q in i["lines"].items()}
    rejected_on_day = start + timedelta(weeks=4, days=2)
    next_id = 700_000_001
    for username in WORKERS:
        app = Decimal(0)  # the app's own running RUTF balance on this worker's phone
        user_id = world.uuids[username]
        for week in range(weeks):
            monday = start + timedelta(weeks=week)
            status_now = "approved" if week < weeks - 2 else "pending"
            events = []
            received = rutf_issues.get((monday, username))
            if received:
                events.append((monday + timedelta(days=1), 0, "stock", received, monday))
            if username == OVER_COUNTS and week == 6:
                thursday = monday + timedelta(days=3)
                events.append((thursday, 0, "stock", UNRECORDED_RECEIPT, thursday))
            for n in range(3):
                events.append((monday + timedelta(days=1 + 2 * n), 1, "visit", n, None))
            for on, _order, kind, value, received_on in sorted(events, key=lambda e: (e[0], e[1])):
                visit_id, next_id = next_id, next_id + 1
                if kind == "stock":
                    app += value
                    answers = {
                        PATHS["received"]: _text(value),
                        PATHS["received_on"]: received_on.isoformat(),
                        PATHS["remaining"]: _text(app),
                    }
                    world.visits.append(_visit(visit_id, username, user_id, on, status_now, FORM_STOCK, answers))
                    continue
                screening = value == 0
                rejected = username == REJECTED_LATER and week == 2 and value == 0
                answers = {}
                if username != NEVER_ANSWERS:
                    app -= _rutf(rng, answers, screening, app, force_ration=rejected)
                answers[PATHS["balance"]] = _text(app)
                if not rejected:  # the rejected visit gave RUTF only, so one reversal tells its story
                    _protocol(rng, answers, screening)
                status, modified = status_now, None
                if rejected:
                    status, modified = "rejected", rejected_on_day
                form_name = FORM_SCREENING if screening else FORM_VISIT
                world.visits.append(_visit(visit_id, username, user_id, on, status, form_name, answers, modified))
    return world


def fixtures(world: World, opportunity_id: int) -> dict:
    """The five export endpoints a synthetic opportunity serves."""
    return {
        "opportunity": {"id": opportunity_id, "name": LABEL},
        "user_visits": [{**v, "opportunity_id": opportunity_id} for v in world.visits],
        "user_data": [{"username": w, "name": w} for w in WORKERS],
        "completed_works": [],
        "completed_module": [],
    }


def rule_data(items: dict, resupply_point_id: int, start: date, *, opportunity_id: int) -> list[dict]:
    """The dispensing rules, as `dispensing_rule_upsert` payloads.

    AL and paracetamol are deliberately absent (no dose field in the app;
    mixed units), as are ORS and zinc (the app contradicts itself) and
    albendazole (not in the design's §7 story).
    """
    common = {
        "opportunity_id": opportunity_id,
        "resupply_point_id": resupply_point_id,
        "active_from": start.isoformat(),
        "forms": [FORM_SCREENING, FORM_VISIT],
    }

    def stated(key, form):
        return {"kind": "stated", "paths": [PATHS[key]], "unit": "sachet", "forms": [form]}

    def vitamin_a(doses):
        """One line per (form, dose question): requires_paths needs every path it names."""
        return [
            {
                "kind": "protocol",
                "given_paths": [PATHS[f"vita_{where}"]],
                "given_values": ["child_fine"],
                "requires_paths": [PATHS[f"{dose}_{where}"]],
                "quantity": "1",
                "unit": "capsule",
                "forms": [form],
            }
            for where, form in (("screening", FORM_SCREENING), ("visit", FORM_VISIT))
            for dose in doses
        ]

    return [
        {
            **common,
            "item_id": items[RUTF_SKU]["id"],
            "lines": [
                stated("rutf_screening_total", FORM_SCREENING),
                stated("rutf_visit", FORM_VISIT),
                stated("appetite_visit", FORM_VISIT),
            ],
            "reports": {
                "balance_paths": [PATHS["balance"], PATHS["remaining"]],
                "receipt": {"quantity_paths": [PATHS["received"]], "date_paths": [PATHS["received_on"]]},
            },
        },
        {**common, "item_id": items["syn-vita100k-100"]["id"], "lines": vitamin_a(("vita_6_11",))},
        {**common, "item_id": items["syn-vita200k-100"]["id"], "lines": vitamin_a(("vita_1_2", "vita_2_5"))},
        {
            **common,
            "item_id": items["syn-amox-100"]["id"],
            "lines": [
                {
                    "kind": "value_map",
                    "paths": [PATHS["amox_fast_breathing"], PATHS["amox_comorbid"], PATHS["amox_visit"]],
                    "map": {answer: str(tablets) for answer, tablets in AMOX_DOSES.items()},
                    "unit": "tablet",
                },
                {
                    "kind": "protocol",
                    "given_paths": [PATHS["amox_presumptive"]],
                    "given_values": ["yes"],
                    "quantity": str(AMOX_PRESUMPTIVE_TABLETS),
                    "unit": "tablet",
                },
            ],
        },
        {
            **common,
            "item_id": items["syn-mrdt-25"]["id"],
            "lines": [
                {
                    "kind": "protocol",
                    "given_paths": [PATHS["mrdt_screening"], PATHS["mrdt_visit"]],
                    "given_values": None,  # any answer, "invalid" included, used a test
                    "quantity": "1",
                    "unit": "test",
                }
            ],
        },
    ]


def _monday_on_or_before(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _registered():
    """The one labs-only opportunity this seeder owns, made on first use."""
    from connect_labs.labs.synthetic import registry
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    opp = SyntheticOpportunity.objects.filter(labs_only=True, label=LABEL).first()
    if opp is None:
        opp = SyntheticOpportunity.objects.create(
            opportunity_id=SyntheticOpportunity.next_labs_only_opp_id(),
            labs_only=True,
            enabled=True,
            label=LABEL,
            org_name=ORG_NAME,
            program_name=PROGRAM_NAME,
            allowed_domains=ALLOWED_DOMAINS,
            gdrive_folder_id="",
            notes="Seeded by supply_seed_stock_from_visits. Invented data.",
        )
        registry.invalidate_cache()
    return opp


def seed(*, drive, reset: bool = False, today: date | None = None) -> dict:
    """Seed (or, with reset, rebuild) the synthetic opportunity. Server-side only.

    Idempotent: a second run without `reset` changes nothing. `reset` purges
    this programme's supply data and history (`purge` refuses any programme
    that is not a registered labs-only one) and seeds it again, with a fresh
    fixture folder.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.labs.synthetic import registry
    from connect_labs.labs.synthetic.generator.io.uploader import upload_and_register
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import DispensingRule
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services.visit_source import SYNTHETIC_TOKEN
    from connect_labs.supply_chain.stock.services.workers import worker_slug

    today = today or timezone.localdate()
    start = _monday_on_or_before(today - timedelta(weeks=WEEKS))
    world = build_world(start)

    opp_id = _registered().opportunity_id
    access = SupplyDataAccess(access_token=SYNTHETIC_TOKEN, program_id=opp_id, opportunity_id=opp_id, caller=SYSTEM)
    if DispensingRule.objects.filter(program_id=opp_id).exists():
        if not reset:
            return {"opportunity_id": opp_id, "seeded": False, "reason": "already seeded; pass --reset to rebuild it"}
        access.purge()

    upload_and_register(drive=drive, opportunity_id=opp_id, opportunity_name=LABEL, fixtures=fixtures(world, opp_id))
    registry.invalidate_cache()

    def op(day, hour, name, **payload):
        at = timezone.make_aware(datetime.combine(day, time(hour)))
        with seed_overrides(opp_id, channel="command", recorded_at=at):
            return call_operation(name, access, payload)

    setup = start - timedelta(days=7)
    items = {}
    for p in PRODUCTS:
        op(setup, 9, "commodity_upsert", data={
            "slug": p.slug, "name": p.name, "category": p.category,
            "base_unit": p.base, "pack_unit": p.pack, "base_per_pack": p.per_pack,
        })  # fmt: skip
        items[p.sku] = op(setup, 9, "item_upsert", data={
            "sku": p.sku, "name": p.name, "commodity_slug": p.slug,
            "base_unit": p.base, "pack_unit": p.pack, "base_per_pack": p.per_pack,
        })  # fmt: skip
    central = op(setup, 9, "supply_point_upsert", data={
        "slug": "central-store", "name": "Central store (synthetic)", "kind": "central_store",
        "source": "we_recorded", "min_months_of_stock": "2", "max_months_of_stock": "6",
    })  # fmt: skip
    partner = op(setup, 9, "supply_point_upsert", data={
        "slug": "partner-store", "name": "Partner store (synthetic)", "kind": "regional_store",
        "parent_supply_point_id": central["id"], "source": "partner_reported",
        "min_months_of_stock": "1", "max_months_of_stock": "3",
    })  # fmt: skip
    for p in PRODUCTS:
        base = {
            "commodity_slug": p.slug, "item_id": items[p.sku]["id"], "quantity_unit": p.base,
            "source": "we_recorded", "occurred_on": setup.isoformat(),
        }  # fmt: skip
        op(setup, 10, "movement_record", data={
            **base, "kind": "receipt", "to_supply_point_id": central["id"], "quantity": str(p.opening),
        })  # fmt: skip
        op(setup, 11, "movement_record", data={
            **base, "kind": "transfer", "from_supply_point_id": central["id"],
            "to_supply_point_id": partner["id"], "quantity": str(p.opening // 2),
        })  # fmt: skip
    for data in rule_data(items, partner["id"], start, opportunity_id=opp_id):
        op(setup, 12, "dispensing_rule_upsert", data=data)
    # The roster is registered up front so week 0's distribution has somewhere
    # to go, under the slug the reader itself gives a worker on first sight;
    # the band is the programme's.
    for username in WORKERS:
        op(setup, 13, "supply_point_upsert", data={
            "slug": worker_slug(opp_id, username), "name": username, "kind": "user_held",
            "opportunity_id": opp_id, "connect_username": username,
            "connect_user_uuid": world.uuids[username], "parent_supply_point_id": partner["id"],
            "source": "connect_visit", "min_months_of_stock": "0.5", "max_months_of_stock": "1.5",
        })  # fmt: skip

    weeks = []
    for week in range(WEEKS):
        monday = start + timedelta(weeks=week)
        for issue in world.issues:
            if issue["on"] != monday:
                continue
            product = BY_SKU[issue["sku"]]
            op(monday, 8, "distribution_record", data={
                "supply_point_id": partner["id"], "opportunity_id": opp_id, "commodity_slug": product.slug,
                "distributed_on": monday.isoformat(), "source": "partner_reported",
                "lines": [
                    {"connect_username": u, "item_id": items[product.sku]["id"], "quantity": str(q),
                     "quantity_unit": product.base}
                    for u, q in issue["lines"].items() if q
                ],
            })  # fmt: skip
        # Each week is read on its Sunday evening, as far as that Sunday, so the
        # as-of history walks back through the reads one week at a time.
        sunday = monday + timedelta(days=6)
        report = op(sunday, 20, "visit_consumption_ingest", opportunity_id=opp_id, until=sunday.isoformat())
        weeks.append(
            {
                "week_of": monday.isoformat(),
                **{k: report[k] for k in ("posted", "estimated", "reversed", "no_answer", "nothing_given")},
                **{k: report[k] for k in ("balances_recorded", "receipts_recorded")},
                **{k: len(report[k]) for k in ("unmatched", "unmapped", "unit_refused", "skipped")},
            }
        )

    return {
        "opportunity_id": opp_id,
        "program_id": opp_id,
        "seeded": True,
        "start": start.isoformat(),
        "workers": len(WORKERS),
        "weeks": weeks,
    }
