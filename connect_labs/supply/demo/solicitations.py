"""Seeds what those organisations are bidding on: EOI rounds and their
submissions, the qualifications those reviews grant, and the solicitations —
one closed, one live and mid-flight, and one fully awarded per corridor.

The mix is deliberate: every status a reviewer or bidder can encounter appears
somewhere in the seeded world.
"""
from datetime import timedelta

from django.utils import timezone

from ..models import (
    RFP,
    Award,
    Bid,
    BidScore,
    EOIReview,
    EOIRound,
    EOISubmission,
    Lot,
    LotBid,
    Qualification,
    StaffRole,
)
from ..serializers import org_dict
from .data import AWARDED_RFP, CLOSED_ROUND, CORRIDOR_AWARDS, LIVE_RFP, OPEN_ROUND, TODAY


def _commitments(categories, rng):
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


def _seed_closed_round(rng, orgs, staff):
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
                "commitments": _commitments(categories, rng),
                "status": EOISubmission.Status.QUALIFIED,
                "submitted_at": timezone.now() - timedelta(days=rng.randint(130, 175)),
            },
        )
        if sub.profile_snapshot is None:
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


def _seed_open_round(rng, orgs):
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
                "commitments": _commitments(categories, rng),
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


def _seed_corridor_awards(orgs, staff):
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


def _seed_live_rfp(rng, orgs, staff):
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


def _seed_awarded_rfp(rng, orgs, staff):
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
