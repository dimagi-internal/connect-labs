"""The world of the `supply-sophie-unanswered-round` walkthrough, replayed as dated history.

Runs IN-PROCESS inside the labs app (Django already set up): on the labs worker
through `seed.py`, or against a local database for a dry run. Everything here
is invented -- companies, people, prices, `.example.invalid` addresses. This
repository is public.

The story is a half-silent round. Sophie asked six suppliers to quote round 2
17 days ago; three never answered, though she chased them twice. The
three who did answer sent one complete quote (Harmattan), one quote missing its
pack size (Kanem), and three questions instead of a price (Northgate). Round 1,
in July, is history: Harmattan won it, and its order now waits on our import
permit, carries the advance Sophie paid before any invoice, and an invoice
above the agreed price.

Two hands write, each on its own day:

- **Sophie**, at her screens (channel `web`): tenders, suppliers, the requests
  she sent, the reminders she sent, the award, the order and the advance.
- **The ACE agent** she forwards supplier email to (channel `mcp`): every
  supplier or forwarder email, recorded with `source = {ref, excerpt, sender}`
  exactly as the agent would send it through the MCP tools.

Every date is anchored to the render day, not the calendar. The story was
written as of 2 Oct 2026 (`STORY_TODAY`): the ask on 15 Sep, the deadline on
29 Sep, the reminders on 18 and 23-25 Sep. The pages count ages from
`date.today()`, so a fixed calendar would make the round look older on every
later render ("No reply · 17 days" becoming "· 40 days"). `story_day` moves
every dated step by the same amount, keeping each offset from the render day
exactly as the story has it on 2 Oct 2026.

Only a registered labs-only program accepts dated writes (`seed_overrides`),
so this refuses any other. The program is this walkthrough's own: it never
touches 10672 (the `supply-sophie-unanswered-round`'s sibling demo), the
chlorine program 10673 or the CHC scopes.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.contrib.auth import get_user_model

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.operations import call_operation, declared_only
from connect_labs.supply_chain.reference import catalogue as reference_catalogue

PROGRAM_ID = 10690
PROGRAM_NAME = "Connect-RUTF (unanswered round walkthrough)"
OPP_LABEL = "Connect-RUTF unanswered-round walkthrough"
# Programs this seeder must never write to, whatever PROGRAM_ID says.
PROTECTED = {10610, 10671, 10672, 10673}
SOPHIE_USERNAME = "demo-sophie"
BUYER_SLUG = "dimagi"

KANO = {"key": "kano", "name": "Partner warehouse", "city": "Kano", "country": "NG"}

SUPPLIERS = [
    # key, name, country, city, type, contact, email
    (
        "harmattan",
        "Harmattan Therapeutics",
        "GH",
        "Tema",
        "manufacturer",
        "Kwame Mensah",
        "k.mensah@harmattan-tx.example.invalid",
    ),
    (
        "kanem",
        "Kanem Foods Ltd",
        "NG",
        "Calabar",
        "manufacturer",
        "Grace Okon",
        "grace.okon@kanemfoods.example.invalid",
    ),
    (
        "northgate",
        "Northgate Commodities",
        "NG",
        "Lagos",
        "trader",
        "Tunde Bakare",
        "tunde@northgate-commodities.example.invalid",
    ),
    (
        "sahel",
        "Sahel Nutrition Industries",
        "NE",
        "Niamey",
        "manufacturer",
        "Amadou Issoufou",
        "sales@sahel-nutrition.example.invalid",
    ),
    (
        "lagoon",
        "Lagoon Nutripharm",
        "CI",
        "Abidjan",
        "manufacturer",
        "Awa Kone",
        "export@lagoon-nutripharm.example.invalid",
    ),
    (
        "savanna",
        "Savanna Ready Foods",
        "NG",
        "Kano",
        "distributor",
        "Musa Abdullahi",
        "musa@savannaready.example.invalid",
    ),
]
SILENT = ("sahel", "lagoon", "savanna")
NORTHGATE_QUESTIONS = (
    "Is delivery to one warehouse in Kano city or to several LGA facilities?",
    "Do you require NAFDAC-registered product, or is UNICEF-prequalified enough?",
    "Who will be importer of record, you or the supplier?",
)
IMPORTER_QUESTION = NORTHGATE_QUESTIONS[2]


# The day the story is written as of: every date in `seed_world` is a story date, moved by
# (render day - STORY_TODAY) so its distance from the render day never changes.
STORY_TODAY = dt.date(2026, 10, 2)


class Refused(RuntimeError):
    pass


def story_day(story_iso: str, today: dt.date | None = None) -> str:
    """A story date (as of STORY_TODAY) moved to the same offset from `today`, as ISO."""
    shift = (today or dt.date.today()) - STORY_TODAY
    return (dt.date.fromisoformat(story_iso) + shift).isoformat()


def _short(iso: str) -> str:
    """How an email writes a day without its year: "12 Sep"."""
    day = dt.date.fromisoformat(iso)
    return f"{day.day} {day:%b}"


def _slashed(iso: str) -> str:
    """How a forwarder's or supplier's paperwork writes a day: "10/10/2026"."""
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def display_day(iso: str) -> str:
    """The day as the pages print it (the one date rule, values.day_text): "15 Sep 2026"."""
    from connect_labs.supply_chain.values import day_text

    return day_text(dt.date.fromisoformat(iso))


def _ten_am(day: str) -> dt.datetime:
    from django.utils import timezone

    when = timezone.make_aware(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(10)))
    now = timezone.now()
    if when.date() > now.date():
        raise Refused(f"a step dated {day} is in the future")
    return min(when, now)


class World:
    def __init__(self, program_id: int, personas: dict):
        self.program_id = program_id
        self.personas = personas
        self.access = SupplyDataAccess(access_token="unanswered-round-seed", program_id=program_id, caller=SYSTEM)

    def op(self, who: str, day: str, name: str, **payload):
        """One operation as `who` ("sophie" over web, "ace" over mcp), recorded at 10:00 on `day`."""
        # The schemas are closed, so a key no operation takes would be refused anyway; checking here
        # names it before any write of this run lands.
        _, dropped = declared_only(name, payload)
        if dropped:
            raise Refused(f"{name}: the seed passes fields the operation does not take: {dropped}")
        channel = "web" if who == "sophie" else "mcp"
        with seed_overrides(self.program_id, actor=self.personas[who], channel=channel, recorded_at=_ten_am(day)):
            return call_operation(name, self.access, payload, channel=channel)

    def email(self, day: str, name: str, *, ref: str, excerpt: str, sender: str, **payload):
        """What the agent records from one email Sophie forwarded."""
        return self.op("ace", day, name, source={"ref": ref, "excerpt": excerpt, "sender": sender}, **payload)


# ---------------------------------------------------------------------------
# Program, people, reset
# ---------------------------------------------------------------------------


def ensure_program(program_id: int = PROGRAM_ID):
    """Register this walkthrough's labs-only opportunity under its own program, once."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.labs.synthetic.provenance import mark_generated
    from connect_labs.labs.synthetic.provisioning import register_labs_only_opp

    if program_id in PROTECTED:
        raise Refused(f"program {program_id} belongs to another demo; this seeder never writes there")
    rows = [o for o in SyntheticOpportunity.objects.filter(labs_only=True) if o.program_id == program_id]
    foreign = [o for o in rows if o.label != OPP_LABEL]
    if foreign:
        raise Refused(f"program {program_id} is already used by {[o.label for o in foreign]}; pick another id")
    # Who may read and write this program: Sophie (a demo-only @example.invalid persona) and the AI that
    # records her suppliers' email -- the configured agent account, whose domain is not a Dimagi-internal one,
    # so without it here the agent's MCP writes are refused.
    domains = ["@example.invalid"] + [
        "@" + e.split("@", 1)[1].lower() for e in settings.LABS_AGENT_ACCOUNT_EMAILS if "@" in e
    ]
    if rows:
        opp = rows[0]
        missing = [d for d in domains if d not in (opp.allowed_domains or [])]
        if missing:
            opp.allowed_domains = list(opp.allowed_domains or []) + missing
            opp.save(update_fields=["allowed_domains"])
        return opp
    opp = register_labs_only_opp(
        label=OPP_LABEL,
        gdrive_folder_id="",
        org_name="Connect-RUTF walkthrough",
        program_name=PROGRAM_NAME,
        program_id=program_id,
        allowed_domains=domains,
    )
    mark_generated(opp.opportunity_id, "")
    return opp


def personas() -> dict:
    users = get_user_model()
    sophie, _ = users.objects.get_or_create(
        username=SOPHIE_USERNAME, defaults={"name": "Sophie", "email": "demo-sophie@example.invalid"}
    )
    if sophie.is_staff or sophie.is_superuser or not sophie.email.endswith("@example.invalid"):
        raise Refused(f"{sophie.username} is not a demo-only persona")
    if not sophie.view_synthetic_opps:
        sophie.view_synthetic_opps = True
        sophie.save(update_fields=["view_synthetic_opps"])
    agent_email = settings.LABS_AGENT_ACCOUNT_EMAILS[0]
    ace = users.objects.filter(email__iexact=agent_email).first()
    if ace is None:
        ace, _ = users.objects.get_or_create(username="ace-agent", defaults={"name": "ACE", "email": agent_email})
    return {"sophie": sophie, "ace": ace}


def reset(program_id: int = PROGRAM_ID):
    """The guarded purge: this program only, and never one an update link may have been sent from."""
    from connect_labs.supply_chain.update_links.models import UpdateLink

    if program_id in PROTECTED:
        raise Refused(f"program {program_id} belongs to another demo; not purging")
    links = UpdateLink.objects.filter(program_id=program_id).count()
    if links:
        raise Refused(f"program {program_id} holds {links} update link(s); not purging")
    return SupplyDataAccess(access_token="unanswered-round-reset", program_id=program_id, caller=SYSTEM).purge()


def buyer_org_id(access, create_if_missing: bool = False) -> int:
    for org in call_operation("org_list", access, {}):
        if org.get("slug") == BUYER_SLUG:
            return org["id"]
    if not create_if_missing:
        raise Refused(f"no organisation with slug {BUYER_SLUG!r}; the buyer of record must already exist")
    return call_operation("org_upsert", access, {"data": {"slug": BUYER_SLUG, "name": "Dimagi"}})["id"]


# ---------------------------------------------------------------------------
# The story
# ---------------------------------------------------------------------------


def _round(w: World, *, label: str, opened: str, deadline: str) -> int:
    tender = w.op(
        "sophie",
        opened,
        "tender_create",
        data={
            "label": label,
            "lines": [{"commodity_slug": "rutf", "quantity": 2000, "quantity_unit": "carton"}],
            "delivery_points": [KANO],
            "incoterm_requested": "CPT Kano",
            "reminder_interval_days": 7,
            "response_deadline": deadline,
            "visibility": "private",
            "notes_to_supplier": "Please quote per carton of 150 sachets, delivered Kano, stating freight and duties.",
        },
    )
    w.op("sophie", opened, "tender_open", tender_id=tender["id"])
    return tender["id"]


def _ask_everyone(w: World, tender_id: int, suppliers: dict, day: str) -> dict:
    return {
        key: w.op(
            "sophie",
            day,
            "outreach_log",
            data={"tender_id": tender_id, "supplier_id": supplier_id, "channel": "manual", "sent_on": day},
        )["id"]
        for key, supplier_id in suppliers.items()
    }


def seed_world(program_id: int = PROGRAM_ID, *, create_buyer: bool = False, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()

    def d(story_iso: str) -> str:
        return story_day(story_iso, today)

    w = World(program_id, personas())
    access = w.access

    rutf = next(p for p in reference_catalogue.PRODUCTS if p["slug"] == "rutf")
    # The program's own treatment protocol (illustrative, not a clinical figure), so a quote can be costed
    # per child: one carton of 150 sachets is one child's course.
    rutf = {
        **rutf,
        "course_definition": {"base_units_per_course": 150, "source": "program protocol (walkthrough, illustrative)"},
    }
    w.op("sophie", d("2026-07-01"), "commodity_upsert", data=dict(rutf))
    buyer = buyer_org_id(access, create_if_missing=create_buyer)

    suppliers = {}
    for key, name, country, city, kind, contact, address in SUPPLIERS:
        suppliers[key] = w.op(
            "sophie",
            d("2026-07-01"),
            "supplier_create",
            data={
                "name": name,
                "country": country,
                "city": city,
                "type": kind,
                "status": "contacted",
                "contacts": [{"name": contact, "email": address}],
            },
        )["id"]

    # ---- Round 1 (ten weeks back): history. Harmattan answered fully and won. ---
    r1 = _round(w, label="RUTF round 1: 2,000 cartons to Kano", opened=d("2026-07-06"), deadline=d("2026-07-20"))
    r1_out = _ask_everyone(w, r1, suppliers, d("2026-07-06"))
    src = dict(
        ref="<PFI0457.k.mensah@harmattan-tx.example.invalid>",
        excerpt="PFI-2026-0457: USD 49.80/CTN x 2,000 (150 x 92 g), FCA Tema. Estimated freight USD 7,200. "
        "MOQ 500 CTN. "
        "Validity 30 days. Payment: 50% with order, 50% before loading.",
        sender="Kwame Mensah, Harmattan Therapeutics",
    )
    r1_quote = w.email(
        d("2026-07-10"),
        "quote_record",
        **src,
        data=dict(
            tender_id=r1,
            supplier_id=suppliers["harmattan"],
            commodity_slug="rutf",
            as_quoted_amount="49.80",
            as_quoted_unit="per_pack",
            as_quoted_currency="USD",
            quantity_basis=2000,
            quantity_basis_unit="carton",
            pack_spec_source="stated_on_quote",
            base_per_pack_stated=150,
            base_unit_grams_stated=92,
            freight_basis="excluded",
            freight_amount="7200.00",
            # Duty is ours as importer, and RUTF enters under the program's duty waiver: nothing to add.
            duties_basis="excluded",
            duties_amount="0.00",
            shelf_life_months_stated=24,
            lead_time_days=35,
            moq=500,
            moq_unit="carton",
            incoterm="FCA Tema",
            validity_until=d("2026-08-09"),
            received_on=d("2026-07-10"),
            supplier_reference="PFI-2026-0457",
            payment_terms="50% with order, 50% before loading",
        ),
    )
    w.email(
        d("2026-07-10"),
        "outreach_update",
        **src,
        outreach_id=r1_out["harmattan"],
        data={"responded": True, "response_kind": "quote", "responded_on": d("2026-07-10")},
    )

    award = w.op(
        "sophie",
        d("2026-07-28"),
        "award_create",
        tender_id=r1,
        quote_id=r1_quote["id"],
        rationale="The only quote with pack, quantity and freight stated.",
        decided_by="Sophie",
        decided_on=d("2026-07-28"),
    )
    contract = w.op(
        "sophie",
        d("2026-07-28"),
        "contract_create",
        data={
            "tender_id": r1,
            "award_id": award["id"],
            "supplier_id": suppliers["harmattan"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": buyer,
            "reference": "DMG-PO-2026-031",
            "status": "placed",
            "currency": "USD",
            "quantity": 2000,
            "quantity_unit": "carton",
            "unit_price": "49.80",
            "unit_price_unit": "per_pack",
            "freight_basis": "excluded",
            "freight_amount": "7200.00",
            "duties_basis": "excluded",
            "duties_amount": "0.00",
            "vat_basis": "included",
            "incoterm": "FCA Tema",
            "promised_lead_time_days": 35,
            "payment_terms": "advance",
            "source": "we_recorded",
            "signed_on": d("2026-07-28"),
        },
    )
    contract_id = contract["id"]
    advance = w.op(
        "sophie",
        d("2026-07-28"),
        "payment_record",
        data={
            "contract_id": contract_id,
            "paid_on": d("2026-07-28"),
            "amount": "53400.00",
            "currency": "USD",
            "reference": "advance on PFI-2026-0457",
            "source": "we_recorded",
        },
    )
    shipment = w.email(
        d("2026-08-26"),
        "shipment_record",
        ref="<disp-031@harmattan-tx.example.invalid>",
        excerpt="The 2,000 cartons left our Tema warehouse today by truck with Crescent Freight. "
        f"Batch HT2608A. Expected in Kano around {_short(d('2026-09-12'))}.",
        sender="Kwame Mensah, Harmattan Therapeutics",
        data={
            "contract_id": contract_id,
            "reference": "DMG-PO-2026-031",
            "status": "dispatched",
            "dispatched_on": d("2026-08-26"),
            "expected_on": d("2026-09-12"),
            "carrier": "Crescent Freight",
            "source": "supplier_reported",
            "lines": [{"batch": "HT2608A", "quantity": 2000, "quantity_unit": "carton"}],
        },
    )
    w.email(
        d("2026-09-26"),
        "shipment_update",
        ref="<cfc-trk-4471-0926@crescent-freight.example.invalid>",
        excerpt="CARGO: 2000 CTNS RUTF / TRUCKS: 2 / HELD AT SEME BORDER - DOCUMENTATION (FORM M) / "
        f"REVISED ETA KANO: {_slashed(d('2026-10-10'))} ONCE FORM M IS "
        "LODGED / CONSIGNEE TO PROVIDE FORM M / PAAR.",
        sender="Crescent Freight & Clearing",
        shipment_id=shipment["id"],
        data={
            "status": "at_customs",
            "expected_on": d("2026-10-10"),
            "required_documents": [{"kind": "import_permit", "name": "Form M", "owed_by_org_id": buyer}],
        },
    )
    w.email(
        d("2026-09-21"),
        "invoice_record",
        ref="<inv-0912@harmattan-tx.example.invalid>",
        excerpt="2,000 CTN @ USD 51.20 = 102,400.00; Freight 7,950.00; Total 110,350.00; "
        f"Less advance received {_slashed(d('2026-07-28'))} (USD 53,400.00)",
        sender="Kwame Mensah, Harmattan Therapeutics",
        data={
            "contract_id": contract_id,
            "reference": "INV-HT-26-0912",
            "issued_on": d("2026-09-21"),
            "status": "received",
            "currency": "USD",
            "amount": "110350.00",
            "quantity_billed": 2000,
            "quantity_unit": "carton",
            "unit_price": "51.20",
            "freight_amount": "7950.00",
            "acknowledges_payment_ids": [advance["id"]],
            "source": "supplier_reported",
        },
    )

    # ---- Round 2 (asked 17 days back): open, and half of it is silent. ---
    r2 = _round(w, label="RUTF round 2: 2,000 cartons to Kano", opened=d("2026-09-15"), deadline=d("2026-09-29"))
    r2_out = _ask_everyone(w, r2, suppliers, d("2026-09-15"))

    src = dict(
        ref="<PFI0611.k.mensah@harmattan-tx.example.invalid>",
        excerpt="PFI-2026-0611: USD 50.10/CTN x 2,000 (150 x 92 g), DDP Kano, freight, duty and clearance included. "
        "Shelf life 24 months. Lead time 5 weeks. MOQ 500 cartons. Validity 30 days.",
        sender="Kwame Mensah, Harmattan Therapeutics",
    )
    w.email(
        d("2026-09-17"),
        "quote_record",
        **src,
        data=dict(
            tender_id=r2,
            supplier_id=suppliers["harmattan"],
            commodity_slug="rutf",
            as_quoted_amount="50.10",
            as_quoted_unit="per_pack",
            as_quoted_currency="USD",
            quantity_basis=2000,
            quantity_basis_unit="carton",
            pack_spec_source="stated_on_quote",
            base_per_pack_stated=150,
            base_unit_grams_stated=92,
            freight_basis="included",
            duties_basis="included",
            shelf_life_months_stated=24,
            lead_time_days=35,
            moq=500,
            moq_unit="carton",
            incoterm="DDP Kano",
            delivery_point_keys=["kano"],
            validity_until=d("2026-10-17"),
            received_on=d("2026-09-17"),
            supplier_reference="PFI-2026-0611",
            payment_terms="50% with order, 50% before loading",
        ),
    )
    w.email(
        d("2026-09-17"),
        "outreach_update",
        **src,
        outreach_id=r2_out["harmattan"],
        data={"responded": True, "response_kind": "quote", "responded_on": d("2026-09-17")},
    )

    src = dict(
        ref="<ng-rfq-0918@northgate-commodities.example.invalid>",
        excerpt="Before we can price, kindly clarify: 1. one warehouse in Kano city or several LGA facilities? "
        "2. NAFDAC-registered or UNICEF-prequalified? 3. Who will be importer of record?",
        sender="Tunde Bakare, Northgate Commodities",
    )
    w.email(
        d("2026-09-18"),
        "outreach_update",
        **src,
        outreach_id=r2_out["northgate"],
        data={"responded": True, "response_kind": "needs_info", "responded_on": d("2026-09-18")},
    )
    questions = {}
    for text in NORTHGATE_QUESTIONS:
        questions[text] = w.email(
            d("2026-09-18"),
            "commitment_record",
            **src,
            data={
                "kind": "question",
                "supplier_id": suppliers["northgate"],
                "tender_id": r2,
                "text": text,
                "raised_on": d("2026-09-18"),
                "source": "supplier_reported",
            },
        )["id"]

    # Sophie's first reminder, three days after the ask: everyone still silent (Kanem included).
    for key in ("kanem", *SILENT):
        w.op(
            "sophie",
            d("2026-09-18"),
            "outreach_update",
            outreach_id=r2_out[key],
            data={"last_reminder_on": d("2026-09-18")},
        )

    src = dict(
        ref="<CAK9q2719@mail.kanemfoods.example.invalid>",
        excerpt="Our price for 2,000 cartons of RUTF is USD 55.00 per carton, DDP Kano, duty paid. Delivery 6 "
        "weeks from PO. Shelf life 24 months. Minimum order 500 cartons. Quote valid 45 days.",
        sender="Grace Okon, Kanem Foods",
    )
    w.email(
        d("2026-09-19"),
        "quote_record",
        **src,
        data=dict(
            tender_id=r2,
            supplier_id=suppliers["kanem"],
            commodity_slug="rutf",
            as_quoted_amount="55.00",
            as_quoted_unit="per_pack",
            as_quoted_currency="USD",
            quantity_basis=2000,
            quantity_basis_unit="carton",
            pack_spec_source="not_stated",
            freight_basis="included",
            duties_basis="included",
            shelf_life_months_stated=24,
            moq=500,
            moq_unit="carton",
            lead_time_days=42,
            incoterm="DDP Kano",
            delivery_point_keys=["kano"],
            validity_until=d("2026-11-03"),
            received_on=d("2026-09-19"),
            supplier_reference="KF/Q/2719",
        ),
    )
    w.email(
        d("2026-09-19"),
        "outreach_update",
        **src,
        outreach_id=r2_out["kanem"],
        data={"responded": True, "response_kind": "quote", "responded_on": d("2026-09-19")},
    )

    # Her second reminder, to the three who have still said nothing, as she got to each.
    for key, day in zip(SILENT, (d("2026-09-23"), d("2026-09-24"), d("2026-09-25"))):
        w.op("sophie", day, "outreach_update", outreach_id=r2_out[key], data={"last_reminder_on": day})

    return {
        **story_dates(today),
        "program_id": program_id,
        "round1_tender_id": r1,
        "round2_tender_id": r2,
        "contract_id": contract_id,
        "sahel_supplier_id": suppliers["sahel"],
        "sahel_outreach_id": r2_out["sahel"],
        "importer_question_id": questions[IMPORTER_QUESTION],
    }


# The dates the screens print, by the name a recipe or lock can use as ${var}: each is the
# story date moved to the render day, in the pages' own format.
STORY_DATES = {
    "ask_date": "2026-09-15",  # round 2 asked of all six: 17 days before the render day
    "first_reminder_date": "2026-09-18",  # the ask + 3; also the day Northgate's questions came
    "northgate_asked_date": "2026-09-18",
    "second_reminder_date": "2026-09-23",  # Sahel's second reminder (Lagoon +1 day, Savanna +2)
    "deadline_date": "2026-09-29",  # round 2's response deadline: 3 days before the render day
    "advance_paid_date": "2026-07-28",  # round 1's award, contract and advance
    "invoice_date": "2026-09-21",
}


def story_dates(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    dates = {name: display_day(story_day(iso, today)) for name, iso in STORY_DATES.items()}
    dates["today_date"] = display_day(today.isoformat())
    return dates


def mint_sophie_session(hours: int = 12) -> dict:
    """The session a real sign-in would produce for the demo-only persona; off camera, 12 hours."""
    import time
    from importlib import import_module

    sophie = personas()["sophie"]
    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store["_auth_user_id"] = str(sophie.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store["_auth_user_hash"] = sophie.get_session_auth_hash()
    store["labs_oauth"] = {
        "access_token": "demo-persona-no-connect-account",
        "refresh_token": "",
        "expires_at": time.time() + hours * 3600,
        "organization_data": {"organizations": [], "programs": [], "opportunities": []},
    }
    store.set_expiry(hours * 3600)
    store.create()
    return {"key": store.session_key, "expires": int(time.time() + hours * 3600)}


def run(program_id: int = PROGRAM_ID, *, mint: bool = False, create_buyer: bool = False) -> dict:
    """Register (once), reset, seed; optionally mint Sophie's session. Returns the recorder's variables."""
    ensure_program(program_id)
    print("purged", reset(program_id))
    outputs = seed_world(program_id, create_buyer=create_buyer)
    outputs["today"] = dt.date.today().isoformat()
    if mint:
        outputs["sophie_session"] = mint_sophie_session()
    return outputs
