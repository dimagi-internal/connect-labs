"""Seeds what those organisations are bidding on: EOI rounds and their
submissions, the qualifications those reviews grant, and the solicitations —
one closed, one live and mid-flight, and one fully awarded per corridor.

The mix is deliberate: every status a reviewer or bidder can encounter appears
somewhere in the seeded world.
"""
from datetime import date, timedelta

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
from .data import (
    AWARDED_RFP,
    CLOSED_ROUND,
    CORRIDOR_AWARDS,
    LIVE_RFP,
    OPEN_ROUND,
    SPLIT_AWARD_LOTS,
    SPLIT_AWARD_RFP,
    TODAY,
)

# Seed timestamps hang off a single reference point so a rerun reproduces the
# same world rather than drifting with the clock.

# What each RUTF supplier bids into each corridor of the live tender.
#
# A plant is cheapest into its own corridor and dearer across a border, because
# on a $44 carton the freight is most of the delta. That single fact is what
# makes the price leader on Maiduguri a different organisation from the price
# leader on Djibo — which is the whole reason a tender carries lots at all.
#
# It has to be true in the DATA, not just in the caption. A global price ladder
# (bidder 0 cheapest everywhere) ranks the same four organisations the same way
# on every lot, and then splitting the award reads as a whim rather than as
# buying each corridor from whoever is actually cheapest into it.
LIVE_RFP_RUTF_PRICES = {
    "Maiduguri": {
        "Savanna Nutrients Ltd": 42.29,
        "Kano Therapeutic Foods PLC": 43.77,
        "Lagos NutriWorks Ltd": 45.79,
        "Faso NutriWorks SA": 48.64,
    },
    "Djibo": {
        "Faso NutriWorks SA": 41.85,
        "Savanna Nutrients Ltd": 45.90,
        "Kano Therapeutic Foods PLC": 46.42,
        "Lagos NutriWorks Ltd": 47.31,
    },
    "Damaturu": {
        "Savanna Nutrients Ltd": 41.83,
        "Kano Therapeutic Foods PLC": 43.13,
        "Lagos NutriWorks Ltd": 46.00,
        "Faso NutriWorks SA": 49.15,
    },
}

# Technical scores on the two corridors the award decision actually turns on.
# The Damaturu and haulage lots are deliberately left unscored — reviewers work
# through a tender lot by lot, and a comparison screen with every cell filled in
# on the day bidding closes is the tell of a fixture rather than a tender.
# Keyed by (category, delivery_place), because the live tender carries a
# transport lot delivering to Maiduguri as well as an RUTF one, and a
# place-only key silently gave both the RUTF panel's scores.
#
# EVERY lot is scored. Leaving two of the four unscored meant the comparison
# Ada awards from rendered a "Score" button — the affordance to score it later
# — in the Technical column of half the tender, so a scene claiming bids are
# "priced AND scored lot by lot" showed a tender still mid-evaluation at the
# moment it was awarded. Scores differ per lot: the same supplier assessed on
# two corridors is assessed on that corridor's evidence.
LIVE_RFP_TECHNICAL_SCORES = {
    ("rutf", "Maiduguri"): {
        "Savanna Nutrients Ltd": 91,
        "Kano Therapeutic Foods PLC": 60,
        "Lagos NutriWorks Ltd": 83,
        "Faso NutriWorks SA": 82,
    },
    ("rutf", "Djibo"): {
        "Faso NutriWorks SA": 88,
        "Savanna Nutrients Ltd": 79,
        "Kano Therapeutic Foods PLC": 61,
        "Lagos NutriWorks Ltd": 74,
    },
    ("rutf", "Damaturu"): {
        "Savanna Nutrients Ltd": 87,
        "Lagos NutriWorks Ltd": 80,
        "Faso NutriWorks SA": 76,
        "Kano Therapeutic Foods PLC": 64,
    },
    ("transport", "Maiduguri"): {
        "Northern Corridor Logistics Ltd": 84,
        "TransSahel Carriers Ltd": 77,
        "Horn Transit Services PLC": 69,
    },
}


def _next_15_september(today=None):
    """The date the narration speaks aloud for the Maiduguri lot.

    Every other deadline in this world is an offset from today, which is right
    for a demo that must stay plausible whenever it is seeded. This one is
    said out loud — "sixty thousand cartons into Maiduguri by the fifteenth of
    September" — so it is pinned to that date instead, and rolls to next year
    once it is past rather than drifting a day at a time out of the narration.
    """
    today = today or TODAY
    return date(today.year if today <= date(today.year, 9, 15) else today.year + 1, 9, 15)


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
    # 2 drafts, 4 awaiting review, 1 qualified, 1 rejected — every status on screen.
    #
    # Savanna heads the queue and applies for TWO categories, which is what lets
    # the fourth scene of oes-supply-base happen at all: Tomas deciding per
    # category on camera, qualifying the ready-to-use therapeutic food and
    # declining the therapeutic milk, where Savanna has no plant and the
    # evidence is thin. One supplier, two different answers is the whole claim,
    # and a submission that only ever asked for one thing cannot carry it.
    # (winner org, status, categories — None means the org's own categories)
    plan = [
        (names[0], EOISubmission.Status.SUBMITTED, ["rutf", "therapeutic_milk"]),
        (names[14], EOISubmission.Status.DRAFT, None),
        (names[15], EOISubmission.Status.DRAFT, None),
        (names[1], EOISubmission.Status.SUBMITTED, None),
        (names[6], EOISubmission.Status.SUBMITTED, None),
        (names[11], EOISubmission.Status.SUBMITTED, None),
        (names[3], EOISubmission.Status.QUALIFIED, None),
        (names[12], EOISubmission.Status.REJECTED, None),
    ]

    for name, status, category_override in plan:
        org = orgs[name]
        categories = category_override or org.categories_hint
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

    _diverge_live_profile_from_its_snapshots(orgs)


def _diverge_live_profile_from_its_snapshots(orgs):
    """Renew a certificate AFTER the applications that froze a copy of it.

    The frozen snapshot is the property the narrative claims an inspector
    general asks about first, and on a world where nothing has changed since
    submission it is unfalsifiable: a reader cannot tell a frozen copy from a
    second render of the live record. So the demo world contains one supplier
    whose live profile has genuinely moved on — Savanna renewed its UNICEF RUTF
    approval after applying — and the two columns visibly disagree.

    Runs LAST, after every snapshot in both rounds is taken, so it is the live
    row that moves and the frozen ones that do not. That ordering is the whole
    point and is why this is a function rather than four lines up there.
    """
    savanna = orgs.get("Savanna Nutrients Ltd")
    if savanna is None:
        return
    cert = savanna.certifications.filter(cert_type="UNICEF RUTF approval").first()
    if cert is None:
        return
    cert.expiry_date = TODAY + timedelta(days=730)
    cert.issuer = "UNICEF Supply Division (renewed)"
    cert.save(update_fields=["expiry_date", "issuer"])


def _seed_corridor_awards(orgs, staff):
    """One fully-awarded solicitation per corridor, feeding the contracts."""
    for org_name, title, brief, country, closed_days_ago, due_in_days, lot_desc, cartons, price in CORRIDOR_AWARDS:
        rfp, _ = RFP.objects.update_or_create(
            title=title,
            defaults={
                "brief": brief,
                "categories": ["transport"] if country == "SD" else ["rutf"],
                "countries": [country],
                "bid_deadline": TODAY - timedelta(days=closed_days_ago),
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
                # The unit the lot's own description states, on every screen.
                #
                # The Sudan lot was relabelled "truck-months" here while its
                # description reads "Inland haulage of 40,000 CARTONS" and the
                # contract it produces reports 40,000 cartons in the pipeline
                # table — so scene 6 said "40,000 truck-months" and scene 9 said
                # "40,000 cartons" about one record, five rows apart. A haulage
                # contract is priced per carton moved here ($3.20), so cartons
                # is the honest unit and the one everything else already uses.
                "unit": "cartons",
                "delivery_country": country,
                "delivery_place": lot_desc.split(" to ")[-1] if " to " in lot_desc else "Port Sudan",
                "delivery_deadline": TODAY + timedelta(days=due_in_days),
            },
        )
        bid, _ = Bid.objects.update_or_create(
            org=orgs[org_name],
            rfp=rfp,
            defaults={
                "status": Bid.Status.SUBMITTED,
                "submitted_at": timezone.now() - timedelta(days=closed_days_ago + 5),
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
                "north-east Nigeria and Burkinabé Sahel response, delivered to Maiduguri, "
                "Djibo and Damaturu ahead of the lean season. Bid lot by lot — OES "
                "intends to award corridors separately."
            ),
            "categories": ["rutf", "transport"],
            "countries": ["NG", "BF"],
            # BEHIND today, because the narrative awards this tender on camera
            # and you cannot award a tender that is still taking bids. It used
            # to close twelve days out, so the award was stamped Jul 29 against
            # a deadline of Aug 11 — the demo performed, on screen, the single
            # thing a procurement auditor looks for first.
            "bid_deadline": TODAY - timedelta(days=3),
            "status": RFP.Status.PUBLISHED,
            "created_by": staff[StaffRole.Role.PROCUREMENT_ADMIN],
        },
    )

    # Four lots across TWO corridors. The Djibo lot exists so the live tender
    # can carry the beat the narrative actually describes — Ada awarding
    # Maiduguri to one supplier and Djibo to another, on camera, rather than
    # showing a split that happened off screen. A tender confined to one
    # country cannot demonstrate splitting corridor risk.
    lots_spec = [
        (
            "rutf",
            "60,000 cartons RUTF delivered to Maiduguri",
            60000,
            "cartons",
            "NG",
            "Maiduguri",
            _next_15_september(),
        ),
        (
            "rutf",
            "20,000 cartons RUTF delivered to Djibo",
            20000,
            "cartons",
            "BF",
            "Djibo",
            TODAY + timedelta(days=80),
        ),
        (
            "rutf",
            "35,000 cartons RUTF delivered to Damaturu",
            35000,
            "cartons",
            "NG",
            "Damaturu",
            TODAY + timedelta(days=90),
        ),
        (
            "transport",
            "Kano–Maiduguri haulage, 6 months",
            6,
            "truck-months",
            "NG",
            "Maiduguri",
            TODAY + timedelta(days=60),
        ),
    ]
    lots = []
    for category, desc, qty, unit, country, place, deadline in lots_spec:
        lot, _ = Lot.objects.update_or_create(
            rfp=rfp,
            description=desc,
            defaults={
                "category": category,
                "quantity": qty,
                "unit": unit,
                "delivery_country": country,
                "delivery_place": place,
                "delivery_deadline": deadline,
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
            if lot.category == "rutf":
                price = LIVE_RFP_RUTF_PRICES[lot.delivery_place][name]
                # A plant trucking into its own country quotes a shorter lead
                # time than one clearing a border with the same pallets.
                lead_time = 21 if org.country == lot.delivery_country else 35
            else:
                price = round(8200.0 * (0.97 + 0.05 * i + rng.random() * 0.03), 2)
                lead_time = rng.choice([21, 28, 35])
            LotBid.objects.update_or_create(
                bid=bid,
                lot=lot,
                defaults={
                    "unit_price": price,
                    "currency": "USD",
                    "lead_time_days": lead_time,
                    "notes": "FCA plant, GS1-labelled pallets." if lot.category == "rutf" else "",
                },
            )

    # Every lot scored — see LIVE_RFP_TECHNICAL_SCORES on why none is left out.
    for lot in lots:
        scores = LIVE_RFP_TECHNICAL_SCORES.get((lot.category, lot.delivery_place))
        if not scores:
            continue
        for lot_bid in LotBid.objects.filter(lot=lot).select_related("bid__org"):
            score = scores.get(lot_bid.bid.org.legal_name)
            if score is None:
                continue
            BidScore.objects.update_or_create(
                lot_bid=lot_bid,
                reviewer=reviewer,
                defaults={
                    "technical_score": score,
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


def _seed_split_award_rfp(rng, orgs, staff):
    """One solicitation whose lots go to two different suppliers.

    Both bid on both lots, and neither wins both — the leader on Maiduguri is
    not the leader on Djibo. That is the whole argument for pricing and
    awarding lot by lot, and it is only convincing if the comparison screen
    actually shows two different price leaders.
    """
    rfp, _ = RFP.objects.update_or_create(
        title=SPLIT_AWARD_RFP,
        defaults={
            "brief": (
                "RUTF into the Somali region and the Burkinabé Sahel. Bid lot by lot: "
                "OES intends to award corridors separately to avoid concentrating the "
                "response on a single plant."
            ),
            "categories": ["rutf"],
            "countries": ["ET", "BF"],
            "bid_deadline": TODAY - timedelta(days=140),
            "status": RFP.Status.PUBLISHED,
            "created_by": staff[StaffRole.Role.PROCUREMENT_ADMIN],
        },
    )

    bidders = [orgs[name] for _d, _c, _q, _dc, _dp, name in SPLIT_AWARD_LOTS]
    bids = {}
    for index, org in enumerate(bidders):
        bid, _ = Bid.objects.update_or_create(
            org=org,
            rfp=rfp,
            defaults={
                "status": Bid.Status.SUBMITTED,
                "submitted_at": timezone.now() - timedelta(days=25 + index),
            },
        )
        bids[org.id] = bid

    for description, category, quantity, country, place, winner_name in SPLIT_AWARD_LOTS:
        winner = orgs[winner_name]
        lot, _ = Lot.objects.update_or_create(
            rfp=rfp,
            description=description,
            defaults={
                "category": category,
                "quantity": quantity,
                "unit": "cartons",
                "delivery_country": country,
                "delivery_place": place,
                "delivery_deadline": TODAY + timedelta(days=55),
            },
        )
        winning_lot_bid = None
        for org in bidders:
            # The local supplier is cheaper on its own corridor and dearer on
            # the other one — freight, not favouritism.
            local = org is winner
            lot_bid, _ = LotBid.objects.update_or_create(
                bid=bids[org.id],
                lot=lot,
                defaults={
                    "unit_price": 42.10 if local else 46.90,
                    "currency": "USD",
                    "lead_time_days": 18 if local else 34,
                    "notes": "",
                },
            )
            BidScore.objects.update_or_create(
                lot_bid=lot_bid,
                reviewer=staff[StaffRole.Role.REVIEWER],
                defaults={
                    "technical_score": 82 if local else 74,
                    "notes": "Capacity and corridor experience assessed against the frozen EOI snapshot.",
                },
            )
            if local:
                winning_lot_bid = lot_bid

        if not hasattr(lot, "award"):
            Award.objects.create(
                lot=lot,
                lot_bid=winning_lot_bid,
                awarded_by=staff[StaffRole.Role.PROCUREMENT_ADMIN],
            )

    rfp.refresh_from_db()
    if not rfp.lots.filter(award__isnull=True).exists() and rfp.status != RFP.Status.AWARDED:
        rfp.status = RFP.Status.AWARDED
        rfp.save(update_fields=["status"])
    return rfp
