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
    plans = seed_distribution_plans(rng, partner, partner_sites)
    seed_shortfall_signal(partner, partner_sites)
    records = seed_distribution_records(rng, partner, partner_sites, plans)
    seed_child_outcomes(rng, partner, records)
    return partner, partner_sites


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
    for index, (name, node_country, lon, lat) in enumerate(PARTNER_SITES):
        node, _ = SupplyNode.objects.update_or_create(
            name=name,
            defaults={
                "kind": SupplyNode.Kind.DELIVERY_POINT,
                "country": node_country,
                "adm1_code": "NGA-2839",
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

    Kukawa, because the narrative needs a site that is short before the centre
    knows it — the whole point of the signal is that it arrives from the people
    holding the cartons rather than being derived centrally.
    """
    site = sites.get("Kukawa Nutrition Centre")
    if site is None:
        return None
    signal, _ = ShortfallSignal.objects.update_or_create(
        site=site,
        raised_on=TODAY - timedelta(days=4),
        defaults={
            "org": partner,
            "needed_by": TODAY + timedelta(days=7),
            "children_affected": 780,
            "cartons_short": Decimal("780"),
            "note": (
                "Stock will not reach the Thursday distribution. Admissions have run "
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
        target = rng.randint(MUAC_RECOVERED_MIN_MM, MUAC_RECOVERED_MIN_MM + 6)
        step = (target - start) / weeks
    elif outcome == ChildOutcome.Discharge.NON_RESPONSE:
        step = rng.uniform(-0.2, 0.4)
    else:
        step = rng.uniform(0.6, 1.6)

    series = []
    for week in range(weeks + 1):
        measured_on = admitted_on + timedelta(days=7 * week)
        series.append(
            {
                "date": measured_on.isoformat(),
                "muac_mm": int(round(start + step * week)),
            }
        )
    return series, admitted_on + timedelta(days=7 * weeks)


def seed_child_outcomes(rng, partner, records):
    """A cohort per distribution record, discharged against the Sphere mix."""
    outcomes = []
    for record in records:
        # A sample of the children on this batch, not all of them — the demo
        # needs a series to drill into, not a synthetic patient register.
        cohort = max(6, min(14, record.children_served // 24))
        for n in range(cohort):
            anon_id = f"{record.site.name[:3].upper()}-{record.distributed_on:%y%m}-{n:03d}"
            outcome = _weighted_choice(rng, DISCHARGE_MIX)
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


def _weighted_choice(rng, weighted):
    roll = rng.random()
    cumulative = 0.0
    for value, weight in weighted:
        cumulative += weight
        if roll <= cumulative:
            return value
    return weighted[-1][0]


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
