"""Build the Operation End Starvation demo world.

Idempotent and deterministic: keyed on natural names (org legal name, round
title, RFP title) and driven by a fixed PRNG seed, so re-running updates in
place and always produces the same world.

Supplier geography mirrors the real RUTF producer landscape — plants in Kano,
Lagos, Ouagadougou and Addis Ababa, none in Sudan (which is supplied through
Port Sudan). Names are fictional.
"""
import os
import random
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from connect_labs.supply.models import (
    RFP,
    Award,
    Bid,
    BidScore,
    Certification,
    EOIReview,
    EOIRound,
    EOISubmission,
    Lot,
    LotBid,
    Qualification,
    StaffRole,
    SupplierMember,
    SupplierOrg,
)

from ._supply_execution_seed import execution_summary, reset_execution, seed_execution

User = get_user_model()

SEED = 20260725

# Demo-persona password. The repo default is fine locally, but any deployed
# instance is publicly reachable and this seeds a procurement_admin — set
# SUPPLY_DEMO_PASSWORD in the environment there so the credential is not
# discoverable from the source.
DEMO_PASSWORD = os.environ.get("SUPPLY_DEMO_PASSWORD", "oes-demo-2026")
TODAY = date.today()

# (legal_name, country, city, categories, cert_profile, gln_suffix)
# cert_profile: "strong" | "expiring" | "thin"
ORGS = [
    ("Savanna Nutrients Ltd", "NG", "Kano", ["rutf"], "strong"),
    ("Kano Therapeutic Foods PLC", "NG", "Kano", ["rutf", "therapeutic_milk"], "expiring"),
    ("Lagos NutriWorks Ltd", "NG", "Lagos", ["rutf"], "strong"),
    ("Faso NutriWorks SA", "BF", "Ouagadougou", ["rutf"], "strong"),
    ("Rift Valley Therapeutics PLC", "ET", "Addis Ababa", ["rutf", "therapeutic_milk"], "strong"),
    ("Abyssinia Nutrition Industries", "ET", "Addis Ababa", ["therapeutic_milk"], "expiring"),
    ("Sahel Milk Products SARL", "BF", "Bobo-Dioulasso", ["therapeutic_milk"], "thin"),
    ("Northern Corridor Logistics Ltd", "NG", "Maiduguri", ["transport"], "strong"),
    ("Blue Nile Freight Co", "SD", "Port Sudan", ["transport", "warehousing"], "expiring"),
    ("Horn Transit Services PLC", "ET", "Dire Dawa", ["transport"], "strong"),
    ("Djibouti Corridor Haulage", "DJ", "Djibouti", ["transport"], "strong"),
    ("Volta Overland SARL", "BF", "Ouagadougou", ["transport"], "thin"),
    ("TransSahel Carriers Ltd", "NG", "Kano", ["transport"], "thin"),
    ("Maiduguri Cold Chain Stores", "NG", "Maiduguri", ["warehousing"], "strong"),
    ("Kassala Warehousing Co", "SD", "Kassala", ["warehousing"], "expiring"),
    ("Addis Central Depot PLC", "ET", "Addis Ababa", ["warehousing"], "strong"),
]

# Tender contacts, indexed positionally against ORGS so the mapping is stable.
CONTACT_NAMES = [
    "Amina Bello",
    "Ibrahim Sanusi",
    "Folake Adeyemi",
    "Salif Ouédraogo",
    "Meseret Tadesse",
    "Yohannes Kebede",
    "Aïcha Traoré",
    "Grace Okonkwo",
    "Osman El-Tayeb",
    "Hanna Girma",
    "Fatouma Ahmed",
    "Boukary Zongo",
    "Nasir Danjuma",
    "Zainab Musa",
    "Awad Hassan",
    "Selam Bekele",
]

CERT_TYPES = {
    "rutf": ["ISO 22000", "GMP", "UNICEF RUTF approval"],
    "therapeutic_milk": ["ISO 22000", "GMP"],
    "transport": ["ISO 9001", "Goods-in-transit insurance"],
    "warehousing": ["ISO 9001", "Cold chain certification"],
}

STAFF = [
    ("oes-lead@oes.example", "Ada Nwosu", StaffRole.Role.PROCUREMENT_ADMIN, ""),
    ("oes-review@oes.example", "Tomas Berhane", StaffRole.Role.REVIEWER, ""),
    ("gov-ng@oes.example", "Hauwa Ibrahim", StaffRole.Role.GOV_OBSERVER, "NG"),
    ("usg@oes.example", "Dale Whitmore", StaffRole.Role.FUNDER, ""),
]

SUPPLIER_LOGIN = ("supplier@savanna.example", "Amina Bello", "Savanna Nutrients Ltd")

CLOSED_ROUND = "OES Supply Base 2026-A"
OPEN_ROUND = "OES Supply Base 2026-B"
LIVE_RFP = "RUTF Northeast Nigeria Q3 2026"
AWARDED_RFP = "RUTF Ethiopia Q2 2026"

# Additional fully-awarded solicitations, one per corridor, so post-award
# execution has a contract per country rather than everything hanging off one.
# (winner org, RFP title, brief, country, lot description, cartons, unit price)
CORRIDOR_AWARDS = [
    (
        "Savanna Nutrients Ltd",
        "RUTF Northeast Nigeria Q2 2026",
        "Supply of RUTF to the north-east Nigeria response for the Q2 caseload.",
        "NG",
        "45,000 cartons RUTF delivered to Maiduguri",
        45000,
        42.10,
    ),
    (
        "Faso NutriWorks SA",
        "RUTF Sahel Q2 2026",
        "Supply of RUTF to the Burkina Faso Sahel region for the Q2 caseload.",
        "BF",
        "20,000 cartons RUTF delivered to Djibo",
        20000,
        43.60,
    ),
    (
        "Blue Nile Freight Co",
        "Sudan Corridor Logistics 2026",
        "Inland haulage and warehousing for imported RUTF through Port Sudan.",
        "SD",
        "Port Sudan inland corridor haulage, 6 months",
        6,
        41500.00,
    ),
]


class Command(BaseCommand):
    help = "Seed the Operation End Starvation demo world (idempotent, deterministic)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all supply_* demo data before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(SEED)

        if options["reset"]:
            self._reset()

        orgs = self._seed_orgs(rng)
        staff = self._seed_staff()
        self._seed_supplier_login(orgs)
        closed_round = self._seed_closed_round(rng, orgs, staff)
        self._seed_open_round(rng, orgs)
        self._seed_live_rfp(rng, orgs, staff)
        self._seed_awarded_rfp(rng, orgs, staff)
        self._seed_corridor_awards(orgs, staff)
        seed_execution(rng, orgs, staff)

        shown_password = "<from SUPPLY_DEMO_PASSWORD>" if os.environ.get("SUPPLY_DEMO_PASSWORD") else DEMO_PASSWORD
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded OES demo world: {SupplierOrg.objects.count()} suppliers, "
                f"{Qualification.objects.count()} qualifications, "
                f"{RFP.objects.count()} solicitations, {Award.objects.count()} awards; "
                f"{execution_summary()}. "
                f"Logins: {SUPPLIER_LOGIN[0]} / {', '.join(s[0] for s in STAFF)} "
                f"(password: {shown_password})"
            )
        )
        assert closed_round  # closed round anchors the registry; keep the reference explicit

    def _reset(self):
        reset_execution()
        Award.objects.all().delete()
        BidScore.objects.all().delete()
        LotBid.objects.all().delete()
        Bid.objects.all().delete()
        Lot.objects.all().delete()
        RFP.objects.all().delete()
        Qualification.objects.all().delete()
        EOIReview.objects.all().delete()
        EOISubmission.objects.all().delete()
        EOIRound.objects.all().delete()
        Certification.objects.all().delete()
        SupplierMember.objects.all().delete()
        SupplierOrg.objects.all().delete()
        StaffRole.objects.all().delete()

    # ---------- suppliers ----------

    def _seed_orgs(self, rng):
        orgs = {}
        for index, (name, country, city, categories, cert_profile) in enumerate(ORGS):
            org, _ = SupplierOrg.objects.update_or_create(
                legal_name=name,
                defaults={
                    "country": country,
                    "hq_city": city,
                    "registration_number": f"{country}-{2015 + (index % 9)}-{4100 + index * 7}",
                    "description": self._describe(name, categories, city, country),
                    "contact_name": CONTACT_NAMES[index % len(CONTACT_NAMES)],
                    "contact_email": f"tenders@{self._domain(name)}",
                    "gln": f"62912345{index:04d}"[:13].ljust(13, "0"),
                    "gs1_company_prefix": f"629123{index:02d}",
                },
            )
            org.categories_hint = categories  # transient, used by later stages
            self._seed_certs(org, categories, cert_profile, rng)
            orgs[name] = org
        return orgs

    def _describe(self, name, categories, city, country):
        if "rutf" in categories:
            return (
                f"{name} manufactures ready-to-use therapeutic food at its {city} plant, "
                "packed 150 sachets per carton to UNICEF specification with GS1 logistics "
                "labelling applied at palletisation."
            )
        if "therapeutic_milk" in categories:
            return f"{name} produces F-75 and F-100 therapeutic milk powders in {city}."
        if "transport" in categories:
            return (
                f"{name} operates road freight along the {city} corridor, including "
                "temperature-monitored trailers for nutrition commodities."
            )
        return f"{name} operates bonded and ambient warehousing in {city}, {country}."

    def _domain(self, name):
        slug = "".join(ch.lower() for ch in name if ch.isalnum())[:18]
        return f"{slug}.example"

    def _seed_certs(self, org, categories, profile, rng):
        wanted = []
        for cat in categories:
            wanted.extend(CERT_TYPES.get(cat, []))
        wanted = list(dict.fromkeys(wanted))

        if profile == "thin":
            wanted = wanted[:1]

        for i, cert_type in enumerate(wanted):
            if profile == "expiring" and i == 0:
                expiry = TODAY + timedelta(days=rng.randint(12, 55))
            else:
                expiry = TODAY + timedelta(days=rng.randint(200, 900))
            Certification.objects.update_or_create(
                org=org,
                cert_type=cert_type,
                defaults={
                    "issuer": {"UNICEF RUTF approval": "UNICEF Supply Division"}.get(cert_type, "SGS"),
                    "expiry_date": expiry,
                    "document_name": f"{cert_type.lower().replace(' ', '-')}-certificate.pdf",
                },
            )

    # ---------- users ----------

    def _user(self, email, name):
        user, created = User.objects.update_or_create(username=email, defaults={"email": email, "name": name})
        if created or not user.has_usable_password():
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
        return user

    def _seed_staff(self):
        staff = {}
        for email, name, role, country in STAFF:
            user = self._user(email, name)
            StaffRole.objects.update_or_create(user=user, defaults={"role": role, "country": country})
            staff[role] = user
        return staff

    def _seed_supplier_login(self, orgs):
        email, name, org_name = SUPPLIER_LOGIN
        user = self._user(email, name)
        SupplierMember.objects.update_or_create(user=user, defaults={"org": orgs[org_name]})

    # ---------- EOI rounds ----------

    def _commitments(self, categories, rng):
        regions = {"NG": "NG, SD", "ET": "ET, DJ", "BF": "BF, ML", "SD": "SD", "DJ": "DJ, ET"}
        out = {}
        for cat in categories:
            out[cat] = {
                "capacity": (
                    f"{rng.randrange(8, 42) * 1000:,} cartons/month"
                    if cat in ("rutf", "therapeutic_milk")
                    else f"{rng.randrange(6, 30)} vehicles"
                    if cat == "transport"
                    else f"{rng.randrange(1, 9) * 1000:,} pallet positions"
                ),
                "regions": regions.get("NG"),
                "lead_time_days": rng.choice([14, 21, 28, 35]),
                "notes": "",
            }
        return out

    def _seed_closed_round(self, rng, orgs, staff):
        rnd, _ = EOIRound.objects.update_or_create(
            title=CLOSED_ROUND,
            defaults={
                "brief": (
                    "Prequalification of suppliers of ready-to-use therapeutic food, therapeutic "
                    "milk, road transport and warehousing for the Operation End Starvation response "
                    "in Nigeria, Sudan, Ethiopia and Burkina Faso."
                ),
                "categories": ["rutf", "therapeutic_milk", "transport", "warehousing"],
                "opens_at": TODAY - timedelta(days=180),
                "closes_at": TODAY - timedelta(days=120),
                "status": EOIRound.Status.CLOSED,
                "created_by": staff[StaffRole.Role.PROCUREMENT_ADMIN],
            },
        )

        reviewer = staff[StaffRole.Role.REVIEWER]
        # Everyone but the last two orgs came through this round and was qualified.
        for name, org in list(orgs.items())[:14]:
            categories = org.categories_hint
            sub, _ = EOISubmission.objects.update_or_create(
                org=org,
                round=rnd,
                defaults={
                    "categories": categories,
                    "commitments": self._commitments(categories, rng),
                    "status": EOISubmission.Status.QUALIFIED,
                    "submitted_at": timezone.now() - timedelta(days=rng.randint(130, 175)),
                },
            )
            if sub.profile_snapshot is None:
                from connect_labs.supply.serializers import org_dict

                sub.profile_snapshot = org_dict(org, include_qualifications=False)
                sub.save(update_fields=["profile_snapshot"])

            if not sub.reviews.exists():
                EOIReview.objects.create(
                    submission=sub,
                    reviewer=reviewer,
                    decisions={c: "qualify" for c in categories},
                    notes="Capacity and certification evidence accepted.",
                )

            for cat in categories:
                granted = TODAY - timedelta(days=rng.randint(115, 125))
                Qualification.objects.update_or_create(
                    org=org,
                    category=cat,
                    defaults={
                        "source_submission": sub,
                        "granted_at": granted,
                        # staggered expiry so the registry shows renewal pressure
                        "expires_at": granted + timedelta(days=rng.choice([200, 320, 420, 540])),
                        "status": Qualification.Status.ACTIVE,
                    },
                )
        return rnd

    def _seed_open_round(self, rng, orgs):
        rnd, _ = EOIRound.objects.update_or_create(
            title=OPEN_ROUND,
            defaults={
                "brief": (
                    "Second-wave prequalification: expanding the supplier base for the 2026-27 "
                    "response, with emphasis on in-country manufacture and Sudan corridor capacity."
                ),
                "categories": ["rutf", "therapeutic_milk", "transport", "warehousing"],
                "opens_at": TODAY - timedelta(days=21),
                "closes_at": TODAY + timedelta(days=18),
                "status": EOIRound.Status.OPEN,
            },
        )

        from connect_labs.supply.serializers import org_dict

        names = list(orgs.keys())
        # 2 drafts, 3 awaiting review, 1 qualified, 1 rejected — every status on screen.
        plan = [
            (names[14], EOISubmission.Status.DRAFT),
            (names[15], EOISubmission.Status.DRAFT),
            (names[1], EOISubmission.Status.SUBMITTED),
            (names[6], EOISubmission.Status.SUBMITTED),
            (names[11], EOISubmission.Status.SUBMITTED),
            (names[3], EOISubmission.Status.QUALIFIED),
            (names[12], EOISubmission.Status.REJECTED),
        ]

        for name, status in plan:
            org = orgs[name]
            categories = org.categories_hint
            submitted_at = (
                None if status == EOISubmission.Status.DRAFT else timezone.now() - timedelta(days=rng.randint(2, 16))
            )
            sub, _ = EOISubmission.objects.update_or_create(
                org=org,
                round=rnd,
                defaults={
                    "categories": categories,
                    "commitments": self._commitments(categories, rng),
                    "status": status,
                    "submitted_at": submitted_at,
                    "profile_snapshot": (
                        None if status == EOISubmission.Status.DRAFT else org_dict(org, include_qualifications=False)
                    ),
                },
            )
            if status in (EOISubmission.Status.QUALIFIED, EOISubmission.Status.REJECTED) and not sub.reviews.exists():
                verdict = "qualify" if status == EOISubmission.Status.QUALIFIED else "reject"
                EOIReview.objects.create(
                    submission=sub,
                    reviewer=None,
                    decisions={c: verdict for c in categories},
                    notes=(
                        "Existing qualification extended."
                        if verdict == "qualify"
                        else "Insufficient certification evidence for this round."
                    ),
                )

    def _seed_corridor_awards(self, orgs, staff):
        """One fully-awarded solicitation per corridor, feeding the contracts."""
        for org_name, title, brief, country, lot_desc, cartons, price in CORRIDOR_AWARDS:
            rfp, _ = RFP.objects.update_or_create(
                title=title,
                defaults={
                    "brief": brief,
                    "categories": ["transport"] if country == "SD" else ["rutf"],
                    "countries": [country],
                    "bid_deadline": TODAY - timedelta(days=55),
                    "status": RFP.Status.PUBLISHED,
                    "created_by": staff[StaffRole.Role.PROCUREMENT_ADMIN],
                },
            )
            lot, _ = Lot.objects.update_or_create(
                rfp=rfp,
                description=lot_desc,
                defaults={
                    "category": rfp.categories[0],
                    "quantity": cartons,
                    "unit": "truck-months" if country == "SD" else "cartons",
                    "delivery_country": country,
                    "delivery_place": lot_desc.split(" to ")[-1] if " to " in lot_desc else "Port Sudan",
                    "delivery_deadline": TODAY + timedelta(days=90),
                },
            )
            bid, _ = Bid.objects.update_or_create(
                org=orgs[org_name],
                rfp=rfp,
                defaults={
                    "status": Bid.Status.SUBMITTED,
                    "submitted_at": timezone.now() - timedelta(days=60),
                },
            )
            lot_bid, _ = LotBid.objects.update_or_create(
                bid=bid, lot=lot, defaults={"unit_price": price, "currency": "USD", "lead_time_days": 21}
            )
            if not hasattr(lot, "award"):
                Award.objects.create(lot=lot, lot_bid=lot_bid, awarded_by=staff[StaffRole.Role.PROCUREMENT_ADMIN])
            rfp.refresh_from_db()
            if not rfp.lots.filter(award__isnull=True).exists() and rfp.status != RFP.Status.AWARDED:
                rfp.status = RFP.Status.AWARDED
                rfp.save(update_fields=["status"])

    # ---------- solicitations ----------

    def _seed_live_rfp(self, rng, orgs, staff):
        rfp, created = RFP.objects.update_or_create(
            title=LIVE_RFP,
            defaults={
                "brief": (
                    "Supply of ready-to-use therapeutic food and inland transport for the "
                    "north-east Nigeria response, delivered to Maiduguri and Damaturu ahead of "
                    "the lean season."
                ),
                "categories": ["rutf", "transport"],
                "countries": ["NG"],
                "bid_deadline": TODAY + timedelta(days=12),
                "status": RFP.Status.PUBLISHED,
                "created_by": staff[StaffRole.Role.PROCUREMENT_ADMIN],
            },
        )

        lots_spec = [
            ("rutf", "60,000 cartons RUTF delivered to Maiduguri", 60000, "cartons", "NG", "Maiduguri", 75),
            ("rutf", "35,000 cartons RUTF delivered to Damaturu", 35000, "cartons", "NG", "Damaturu", 90),
            ("transport", "Kano–Maiduguri haulage, 6 months", 6, "truck-months", "NG", "Maiduguri", 60),
        ]
        lots = []
        for category, desc, qty, unit, country, place, due_in in lots_spec:
            lot, _ = Lot.objects.update_or_create(
                rfp=rfp,
                description=desc,
                defaults={
                    "category": category,
                    "quantity": qty,
                    "unit": unit,
                    "delivery_country": country,
                    "delivery_place": place,
                    "delivery_deadline": TODAY + timedelta(days=due_in),
                },
            )
            lots.append(lot)

        rutf_bidders = [
            "Savanna Nutrients Ltd",
            "Kano Therapeutic Foods PLC",
            "Lagos NutriWorks Ltd",
            "Faso NutriWorks SA",
        ]
        transport_bidders = ["Northern Corridor Logistics Ltd", "TransSahel Carriers Ltd", "Horn Transit Services PLC"]

        reviewer = staff[StaffRole.Role.REVIEWER]
        for lot in lots:
            bidders = rutf_bidders if lot.category == "rutf" else transport_bidders
            for i, name in enumerate(bidders):
                org = orgs[name]
                bid, _ = Bid.objects.update_or_create(
                    org=org,
                    rfp=rfp,
                    defaults={
                        "status": Bid.Status.SUBMITTED,
                        "submitted_at": timezone.now() - timedelta(days=rng.randint(1, 9)),
                    },
                )
                base = 44.0 if lot.category == "rutf" else 8200.0
                price = round(base * (0.92 + 0.05 * i + rng.random() * 0.06), 2)
                LotBid.objects.update_or_create(
                    bid=bid,
                    lot=lot,
                    defaults={
                        "unit_price": price,
                        "currency": "USD",
                        "lead_time_days": rng.choice([14, 21, 28, 35]),
                        "notes": "FCA plant, GS1-labelled pallets." if lot.category == "rutf" else "",
                    },
                )

        # Partially scored: the first lot is fully scored, the rest awaits reviewers.
        for lot_bid in LotBid.objects.filter(lot=lots[0]):
            BidScore.objects.update_or_create(
                lot_bid=lot_bid,
                reviewer=reviewer,
                defaults={
                    "technical_score": rng.randint(58, 94),
                    "notes": "Assessed on capacity evidence and past on-time performance.",
                },
            )

    def _seed_awarded_rfp(self, rng, orgs, staff):
        rfp, _ = RFP.objects.update_or_create(
            title=AWARDED_RFP,
            defaults={
                "brief": "Supply of RUTF to Ethiopian regional hubs for the Q2 2026 caseload.",
                "categories": ["rutf"],
                "countries": ["ET"],
                "bid_deadline": TODAY - timedelta(days=30),
                "status": RFP.Status.PUBLISHED,
                "created_by": staff[StaffRole.Role.PROCUREMENT_ADMIN],
            },
        )
        lot, _ = Lot.objects.update_or_create(
            rfp=rfp,
            description="48,000 cartons RUTF delivered to Gode",
            defaults={
                "category": "rutf",
                "quantity": 48000,
                "unit": "cartons",
                "delivery_country": "ET",
                "delivery_place": "Gode",
                "delivery_deadline": TODAY + timedelta(days=40),
            },
        )

        winner = orgs["Rift Valley Therapeutics PLC"]
        runner_up = orgs["Faso NutriWorks SA"]
        winning_lot_bid = None
        for i, org in enumerate([winner, runner_up]):
            bid, _ = Bid.objects.update_or_create(
                org=org,
                rfp=rfp,
                defaults={
                    "status": Bid.Status.SUBMITTED,
                    "submitted_at": timezone.now() - timedelta(days=40 + i),
                },
            )
            lb, _ = LotBid.objects.update_or_create(
                bid=bid,
                lot=lot,
                defaults={
                    "unit_price": 41.80 if org is winner else 45.30,
                    "currency": "USD",
                    "lead_time_days": 21 if org is winner else 30,
                    "notes": "",
                },
            )
            if org is winner:
                winning_lot_bid = lb

        if not hasattr(lot, "award"):
            Award.objects.create(lot=lot, lot_bid=winning_lot_bid, awarded_by=staff[StaffRole.Role.PROCUREMENT_ADMIN])
        rfp.refresh_from_db()
        if not rfp.lots.filter(award__isnull=True).exists() and rfp.status != RFP.Status.AWARDED:
            rfp.status = RFP.Status.AWARDED
            rfp.save(update_fields=["status"])
