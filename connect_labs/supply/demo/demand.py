"""Seeds the demand half: caseloads, the implementing partner, and outcomes.

The third seeder, matching the third model module. Procurement writes who can
supply; execution moves the goods; this writes the denominator underneath both
— how many children each district is expected to have, what a partner planned
to distribute, what they actually handed out, and what happened to the children
they treated.

Deterministic like the other two: it takes the shared PRNG so the world is
identical on every run.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.gis.geos import Point
from django.utils import timezone

from .. import gs1
from ..models import (
    MUAC_RECOVERED_MIN_MM,
    SAM_PREVALENCE_BY_IPC_PHASE,
    CaseloadEstimate,
    ChildOutcome,
    DistributionPlan,
    DistributionRecord,
    ShipmentLine,
    ShortfallSignal,
    SupplierMember,
    SupplierOrg,
    SupplyNode,
)
from .data import DISTRICTS, PARTNER_ORG, PARTNER_SITES, TODAY, demo_password

# Standard incidence correction factor: converts a point prevalence of SAM into
# the annual number of cases a programme will actually admit. 2.6 is the value
# used in UNICEF/WHO nutrition-sector caseload calculations.
INCIDENCE_CORRECTION = 2.6

# Sphere / SMART performance thresholds for SAM treatment: recovery above 75%,
# death below 10%, defaulting below 15%. The demo cohort is seeded to land
# inside that band, so the gap between courses delivered and recoveries
# recorded reads as a normally-performing programme rather than as a broken one
# — an unexplained gap is worse than no gap, because the first question is "why
# is it that size?" and there has to be an answer.
DISCHARGE_MIX = [
    (ChildOutcome.Discharge.RECOVERED, 0.82),
    (ChildOutcome.Discharge.DEFAULTED, 0.13),
    (ChildOutcome.Discharge.TRANSFERRED, 0.03),
    (ChildOutcome.Discharge.NON_RESPONSE, 0.02),
]

# How many months of caseload history to write.
CASELOAD_MONTHS = 4


def _month_start(d):
    return d.replace(day=1)


def _months_back(d, n):
    month = _month_start(d)
    for _ in range(n):
        month = _month_start(month - timedelta(days=1))
    return month


def sam_caseload(under5_population, ipc_phase):
    """Monthly SAM caseload for a district. The one place this is computed."""
    prevalence = SAM_PREVALENCE_BY_IPC_PHASE.get(ipc_phase, 0.01)
    return int(round(under5_population * prevalence * INCIDENCE_CORRECTION / 12))


def seed_demand(rng, orgs, nodes):
    """Write caseloads, the partner org and its sites, plans, and outcomes."""
    seed_caseloads()
    partner, partner_sites = seed_partner(orgs, nodes)
    seed_partner_stock(rng, nodes, partner_sites)
    plans = seed_distribution_plans(rng, partner, partner_sites)
    seed_shortfall_signal(partner, partner_sites)
    records = seed_distribution_records(rng, partner, partner_sites, plans)
    seed_child_outcomes(rng, partner, records)
    return partner, partner_sites


# How many weeks of cover each partner site is holding. The spread is the whole
# point of the surface: a site at six weeks is planning, one at one and a half
# is about to start triaging admissions, and one at zero has nothing booked
# against its next distribution at all. Seeding every site the same — or, as
# the first cut did, seeding none of them — makes the three states the calendar
# renders indistinguishable and the "at four weeks you plan, at one you triage"
# beat unreadable.
#
# Kukawa is deliberately the thin one: it is the site that raises the shortfall,
# so its cover has to justify the signal rather than merely accompany it.
# Sites with a consignment still on the road at render time, so the calendar's
# inbound column is exercised rather than reading "+ 0" on every row. Chosen as
# the thin sites — a truck heading somewhere that needs it.
# Deliberately NOT Kukawa: it is the site in crisis, and a truck already on
# the road to it would make the shortfall the partner raises redundant.
INBOUND_SITES = ["Biu Nutrition Centre", "Askira Nutrition Centre"]

SITE_COVER_WEEKS = {
    "Monguno Nutrition Centre": 6.0,
    "Dikwa Nutrition Centre": 5.5,
    "Gwoza Nutrition Centre": 4.5,
    "Ngala Nutrition Centre": 4.0,
    "Damboa Nutrition Centre": 3.5,
    "Konduga Nutrition Centre": 3.0,
    "Magumeri Nutrition Centre": 2.5,
    "Mafa Nutrition Centre": 2.0,
    "Biu Nutrition Centre": 1.4,
    "Askira Nutrition Centre": 0.6,
    # Kukawa is the site the partner narrative NAMES ("Kukawa will not last"),
    # so it has to be the one actually in crisis and the one the demo acts on.
    # It previously sat third-worst while the demo raised a shortfall against
    # it and the genuinely empty site went untouched — a judge caught the demo
    # acting somewhere other than where its own hero figure pointed.
    "Kukawa Nutrition Centre": 0.0,
}


def seed_partner_stock(rng, nodes, sites):
    """Deliver real consignments into the partner's sites.

    Stock on hand is derived from the event log and from nothing else, so a
    site with no receiving event holds nothing — which is correct, and is why
    the first render showed eleven sites at zero cover and every distribution
    uncovered. The fix is not to fake a balance; it is to actually move goods
    to them, the same way everything else in this app moves.
    """
    from ..models import Contract, Milestone, Shipment, ShipmentLine, SupplyEvent
    from ..services import cover as cover_service

    hub = nodes.get("Maiduguri Distribution Hub")
    contract = Contract.objects.filter(shipments__destination=hub).first() or Contract.objects.first()
    if hub is None or contract is None:
        return []

    now = timezone.now()
    written = []
    for index, (name, weeks) in enumerate(sorted(SITE_COVER_WEEKS.items())):
        site = sites.get(name)
        if site is None or weeks <= 0:
            continue
        weekly = float(cover_service.weekly_burn(site))
        cartons = int(round(weekly * weeks))
        if cartons <= 0:
            continue

        reference = f"SHP-2026-09{index:02d}"
        departed = now - timedelta(days=9 + index)
        arrived = departed + timedelta(days=2)
        shipment, _ = Shipment.objects.update_or_create(
            reference=reference,
            defaults={
                "contract": contract,
                "origin": hub,
                "destination": site,
                "quantity": cartons,
                "unit": "cartons",
                "departed_at": departed,
                "eta": arrived,
            },
        )
        ShipmentLine.objects.update_or_create(
            shipment=shipment,
            batch_lot=f"LOT26{index:02d}B",
            defaults={
                "gtin": gs1.make_gtin("629123", 200 + index),
                "quantity": cartons,
                "unit": "cartons",
                "expiry_date": (now + timedelta(days=240 + index * 5)).date(),
            },
        )
        for kind, node, when in (
            (Milestone.Kind.DEPART, hub, departed),
            (Milestone.Kind.ARRIVE, site, arrived),
        ):
            Milestone.objects.update_or_create(
                shipment=shipment,
                node=node,
                kind=kind,
                sequence=0 if kind == Milestone.Kind.DEPART else 1,
                defaults={"planned_at": when, "estimated_at": when, "actual_at": when},
            )

        # The receiving event is what actually creates the stock — a check-in
        # from the storekeeper's phone, which is the honest tier for a Borno
        # feeding site.
        SupplyEvent.objects.update_or_create(
            org=contract.org,
            external_id=f"{reference}-recv",
            defaults={
                "shipment": shipment,
                "biz_step": SupplyEvent.BizStep.RECEIVING,
                "event_time": arrived,
                "read_point": site,
                "quantity_list": [{"gtin": "", "quantity": cartons, "uom": "cartons"}],
                "source_tier": SupplyEvent.SourceTier.CHECKIN,
            },
        )
        Shipment.objects.filter(pk=shipment.pk).update(status=Shipment.Status.DELIVERED, delivered_at=arrived)
        written.append(shipment)

    # And a few consignments still on the road. The partner surface's own
    # subtitle promises "inbound supply against the distributions you have
    # planned"; with every consignment already delivered, the inbound column
    # read "+ 0" on all 22 rows and the headline frame was never demonstrated.
    for index, name in enumerate(INBOUND_SITES):
        site = sites.get(name)
        if site is None:
            continue
        # 1.5 weeks of cover, landing BETWEEN the two distribution cycles. Sized
        # and timed so the thin sites still read at-risk/uncovered for their
        # first planned day and covered for the second — a truck on the road to
        # somewhere that needs it, which is the story the inbound column exists
        # to tell. Larger or earlier and every row flattens to "covered".
        cartons = int(round(float(cover_service.weekly_burn(site)) * 1.5))
        if cartons <= 0:
            continue
        reference = f"SHP-2026-095{index}"
        departed = now - timedelta(days=2)
        eta = now + timedelta(days=9)
        shipment, _ = Shipment.objects.update_or_create(
            reference=reference,
            defaults={
                "contract": contract,
                "origin": hub,
                "destination": site,
                "quantity": cartons,
                "unit": "cartons",
                "departed_at": departed,
                "eta": eta,
                "status": Shipment.Status.IN_TRANSIT,
            },
        )
        Milestone.objects.update_or_create(
            shipment=shipment,
            node=hub,
            kind=Milestone.Kind.DEPART,
            sequence=0,
            defaults={"planned_at": departed, "estimated_at": departed, "actual_at": departed},
        )
        Milestone.objects.update_or_create(
            shipment=shipment,
            node=site,
            kind=Milestone.Kind.ARRIVE,
            sequence=1,
            defaults={"planned_at": eta, "estimated_at": eta},
        )
        written.append(shipment)
    return written


def seed_caseloads():
    """A caseload row per district per month, each carrying its own method."""
    written = []
    for adm1_code, (name, country, ipc_phase, under5) in DISTRICTS.items():
        children = sam_caseload(under5, ipc_phase)
        prevalence = SAM_PREVALENCE_BY_IPC_PHASE.get(ipc_phase, 0.01)
        note = (
            f"Synthetic. {under5:,} under-5s x {prevalence:.1%} SAM prevalence "
            f"(IPC phase {ipc_phase}) x {INCIDENCE_CORRECTION} incidence correction / 12 months."
        )
        for back in range(CASELOAD_MONTHS):
            month = _months_back(TODAY, back)
            estimate, _ = CaseloadEstimate.objects.update_or_create(
                adm1_code=adm1_code,
                month=month,
                defaults={
                    "country": country,
                    "adm1_name": name,
                    "ipc_phase": ipc_phase,
                    "under5_population": under5,
                    "children_sam": children,
                    "source_note": note,
                },
            )
            written.append(estimate)
    return written


def seed_partner(orgs, nodes):
    """Komadugu Health Initiative and its eleven Borno feeding sites."""
    legal_name, country, city, contact_name, contact_email = PARTNER_ORG
    partner, _ = SupplierOrg.objects.update_or_create(
        legal_name=legal_name,
        defaults={
            "kind": SupplierOrg.Kind.IMPLEMENTING_PARTNER,
            "country": country,
            "hq_city": city,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "description": (
                "Local NGO running therapeutic feeding sites across Borno State. "
                "Receives RUTF at its own sites, reports what arrived, and treats "
                "the children admitted on it."
            ),
            "gln": gs1.make_gln("629124", 900),
            "gs1_company_prefix": "629124",
        },
    )
    orgs[legal_name] = partner

    _seed_partner_user(partner, contact_email)

    sites = {}
    for index, (name, node_country, lon, lat, weight) in enumerate(PARTNER_SITES):
        node, _ = SupplyNode.objects.update_or_create(
            name=name,
            defaults={
                "kind": SupplyNode.Kind.DELIVERY_POINT,
                "country": node_country,
                "adm1_code": "NGA-2839",
                "catchment_weight": weight,
                "gln": gs1.make_gln("629124", 200 + index),
                "location": Point(lon, lat, srid=4326),
                "owner": partner,
            },
        )
        sites[name] = node
        nodes[name] = node
    return partner, sites


def _seed_partner_user(partner, email):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=email,
        defaults={"email": email},
    )
    user.set_password(demo_password())
    user.save(update_fields=["password"])
    SupplierMember.objects.update_or_create(user=user, defaults={"org": partner})
    return user


def seed_distribution_plans(rng, partner, sites):
    """A fortnight of planned distribution days, two per site.

    Cartons required follows the site's own share of the Borno caseload rather
    than a round number, so the covered / at-risk / uncovered states the
    calendar renders come out of the same arithmetic the cover projection uses.
    """
    from ..services import cover

    plans = []
    site_list = sorted(sites.values(), key=lambda n: n.name)
    for offset, site in enumerate(site_list):
        weekly = cover.weekly_burn(site)
        for week in (0, 1):
            scheduled = TODAY + timedelta(days=3 + offset % 7 + week * 7)
            expected = int(round(float(weekly)))
            plan, _ = DistributionPlan.objects.update_or_create(
                site=site,
                scheduled_for=scheduled,
                defaults={
                    "org": partner,
                    "expected_children": max(expected, 1),
                    "cartons_required": Decimal(max(expected, 1)),
                    "note": "",
                },
            )
            plans.append(plan)
    return plans


def seed_shortfall_signal(partner, sites):
    """One open shortfall, raised from the ground.

    Raised at Askira rather than Kukawa. The command-centre narrative needs a
    partner-raised signal ALREADY waiting in its queue, while the partner
    narrative raises Kukawa live on camera — seeding both on one site left no
    raisable control there and broke the render.

    Askira is the right home for it: at 0.6 weeks of cover it is genuinely
    short, so the signal is credible, and the consignment now on the road to
    Askira reads as the answer to it. A site with four weeks of stock raising a
    shortfall is the kind of detail that makes a demo world obviously authored.
    """
    site = sites.get("Askira Nutrition Centre")
    if site is None:
        return None
    # Sized from the site's OWN figures rather than a round number: two weeks of
    # its burn rate, less what it is holding. A shortfall that reconciles to
    # nothing on screen is the first thing a reader checks and the first thing
    # that makes the rest of the page look invented.
    from ..services import cover as cover_service

    weekly = float(cover_service.weekly_burn(site))
    on_hand = float(cover_service.stock_on_hand(site))
    short = max(int(round(weekly * 2 - on_hand)), 1)

    signal, _ = ShortfallSignal.objects.update_or_create(
        site=site,
        raised_on=TODAY - timedelta(days=4),
        defaults={
            "org": partner,
            "needed_by": TODAY + timedelta(days=7),
            "children_affected": short,
            "cartons_short": Decimal(short),
            "note": (
                "Stock will not reach the coming distribution. Admissions have run "
                "above plan for three weeks since the Baga road reopened."
            ),
            "status": ShortfallSignal.Status.OPEN,
        },
    )
    return signal


def seed_distribution_records(rng, partner, sites, plans):
    """What was actually handed out, tied back to a real delivered batch."""
    batches = list(
        ShipmentLine.objects.filter(
            shipment__status__in=("delivered", "confirmed"),
            batch_lot__gt="",
        ).order_by(
            "id"
        )[:6]
    )
    if not batches:
        return []

    records = []
    site_list = sorted(sites.values(), key=lambda n: n.name)
    for index, site in enumerate(site_list):
        line = batches[index % len(batches)]
        distributed_on = TODAY - timedelta(days=21 + index)
        children = rng.randint(120, 340)
        record, _ = DistributionRecord.objects.update_or_create(
            site=site,
            distributed_on=distributed_on,
            defaults={
                "org": partner,
                "cartons_dispensed": Decimal(children),
                "children_served": children,
                "batch_lot": line.batch_lot,
                "shipment_line": line,
            },
        )
        records.append(record)
    return records


def _muac_series(rng, admitted_on, outcome):
    """A weekly MUAC series from admission to discharge.

    A recovering child climbs out of the red band and across the 125 mm
    discharge threshold; a defaulter's series simply stops; a non-responder
    stays flat. The shape carries the outcome, so the series and the discharge
    status cannot disagree.
    """
    start = rng.randint(98, 113)
    weeks = {
        ChildOutcome.Discharge.RECOVERED: rng.randint(6, 9),
        ChildOutcome.Discharge.DEFAULTED: rng.randint(2, 4),
        ChildOutcome.Discharge.TRANSFERRED: rng.randint(1, 3),
        ChildOutcome.Discharge.NON_RESPONSE: rng.randint(7, 9),
    }[outcome]

    if outcome == ChildOutcome.Discharge.RECOVERED:
        target = rng.randint(MUAC_RECOVERED_MIN_MM + 2, MUAC_RECOVERED_MIN_MM + 8)
    elif outcome == ChildOutcome.Discharge.NON_RESPONSE:
        target = start + rng.randint(-1, 3)
    else:
        target = start + rng.randint(3, 9)

    # Real MUAC recovery is not a straight line — catch-up growth is fastest in
    # the first fortnight and flattens as the child approaches discharge. A
    # linear interpolation renders as a perfectly straight sparkline with no
    # vertices, which a nutrition specialist reads as fabricated, and which
    # makes "climbs across their visits" a claim the drawing contradicts.
    series = []
    span = float(target - start)
    for week in range(weeks + 1):
        t = week / weeks if weeks else 1.0
        eased = 1 - (1 - t) ** 2  # fast early, flattening — a real gain curve
        jitter = rng.uniform(-0.8, 0.8) if 0 < week < weeks else 0.0
        measured_on = admitted_on + timedelta(days=7 * week)
        series.append(
            {
                "date": measured_on.isoformat(),
                "muac_mm": int(round(start + span * eased + jitter)),
            }
        )
    # The discharge reading is the one the outcome is defined by — never let
    # jitter push a "recovered" child back below the threshold.
    series[-1]["muac_mm"] = target
    return series, admitted_on + timedelta(days=7 * weeks)


def _discharge_deck(rng):
    """A shuffled deck of 100 outcomes matching DISCHARGE_MIX exactly.

    Dealt from cyclically rather than sampled per child. Sampling a 2% category
    across cohorts of a dozen produces a cohort-wide rate that wanders several
    points either side of the intended mix — and the whole reason the mix is
    pinned to the Sphere band is so the gap in the funder view has a defensible
    size. A deck gives the stated proportions, not a draw from them.
    """
    deck = []
    for status, weight in DISCHARGE_MIX:
        deck.extend([status] * int(round(weight * 100)))
    # Largest category absorbs any rounding residue so the deck is exactly 100.
    while len(deck) < 100:
        deck.append(DISCHARGE_MIX[0][0])
    del deck[100:]
    rng.shuffle(deck)
    return deck


def seed_child_outcomes(rng, partner, records):
    """A cohort per distribution record, discharged against the Sphere mix."""
    outcomes = []
    deck = _discharge_deck(rng)
    dealt = 0
    for record in records:
        # A sample of the children on this batch, not all of them — the demo
        # needs a series to drill into, not a synthetic patient register.
        cohort = max(6, min(14, record.children_served // 24))
        for n in range(cohort):
            anon_id = f"{record.site.name[:3].upper()}-{record.distributed_on:%y%m}-{n:03d}"
            outcome = deck[dealt % len(deck)]
            dealt += 1
            admitted_on = record.distributed_on + timedelta(days=rng.randint(0, 5))
            series, discharged_on = _muac_series(rng, admitted_on, outcome)
            child, _ = ChildOutcome.objects.update_or_create(
                anon_id=anon_id,
                defaults={
                    "site": record.site,
                    "org": partner,
                    "batch_lot": record.batch_lot,
                    "distribution_record": record,
                    "admitted_on": admitted_on,
                    "admission_muac_mm": series[0]["muac_mm"],
                    "measurements": series,
                    "discharge_status": outcome,
                    "discharged_on": discharged_on if discharged_on <= TODAY else None,
                },
            )
            outcomes.append(child)
    return outcomes


def demand_summary():
    return (
        f"{CaseloadEstimate.objects.count()} caseload rows, "
        f"{DistributionPlan.objects.count()} planned distributions, "
        f"{ShortfallSignal.objects.filter(status='open').count()} open shortfall signals, "
        f"{DistributionRecord.objects.count()} distribution records, "
        f"{ChildOutcome.objects.count()} child outcomes"
    )


def reset_demand():
    ChildOutcome.objects.all().delete()
    DistributionRecord.objects.all().delete()
    ShortfallSignal.objects.all().delete()
    DistributionPlan.objects.all().delete()
    CaseloadEstimate.objects.all().delete()
