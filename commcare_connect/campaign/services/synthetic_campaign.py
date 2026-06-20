"""Build a full national-scale synthetic campaign from CommCare-shaped cases.

Faithful to the Data Model's ownership split:

* CommCare-HQ-owned roster — Worker (as ``WorkerCase`` cases), Region/geography —
  is sourced from real labs ``AdminBoundary`` + the case generator.
* Tool-owned datasets — RegionPlan, Microplan, ReportDay, HouseholdStat, Donor,
  WorkerRole, CampaignUser — are derived/materialized into the campaign's own ORM
  (the tool's local view, mirroring a CommCare→tool sync).

The worker ``Worker`` rows are MATERIALIZED from the worker cases (the tool's
synced read/write copy) so the existing read + write paths are untouched; the
cases remain the CommCare-owned source of truth that a future ``CommCareProvider``
will read live instead.

Entry point: :func:`build_synthetic_campaign`. National scale = all loaded states;
``states_limit`` caps it for a smaller (or test) run.
"""
from __future__ import annotations

import random

from django.db import transaction

from commcare_connect.campaign.models import (
    Campaign,
    Donor,
    HouseholdStat,
    Microplan,
    Region,
    RegionPlan,
    ReportDay,
    SyntheticCommCareDomain,
    WorkerRole,
    Workspace,
)
from commcare_connect.campaign.services import geography, seed, worker_cases

DEFAULT_CODE = "MR-NAT-2026"
DEFAULT_NAME = "Measles–Rubella Vaccination Campaign (National)"


def _campaign(workspace, *, code, name, target_pop):
    return Campaign.objects.create(
        workspace=workspace,
        name=name,
        code=code,
        round="Round 2",
        country="Nigeria",
        period="May 18 – Jun 14, 2026",
        status="Active",
        days_elapsed=16,
        days_total=28,
        target_pop=target_pop,
    )


def _regions_from_boundaries(campaign, states):
    """One Region per AdminBoundary state; region_id = the state's boundary_id so
    worker cases (which carry the same boundary_id) join cleanly."""
    regions = {}
    for order, state in enumerate(states):
        lga_names = [lga.name for lga in geography.lgas(state)]
        regions[state.boundary_id] = Region.objects.create(
            campaign=campaign,
            region_id=state.boundary_id,
            name=state.name,
            lgas=lga_names,
            order=order,
        )
    return regions


def _region_plans(regions, workers_by_region, state_pop):
    for region_id, region in regions.items():
        ws = workers_by_region.get(region_id, [])
        actual_wf = len(ws)
        spent = sum(w["amount"] for w in ws)
        pop = int(state_pop.get(region_id, 0))
        target = max(actual_wf, round(pop * 0.18))  # ~18% of pop is campaign-eligible
        RegionPlan.objects.create(
            region=region,
            planned_wf=round(actual_wf * 1.15) or 1,
            actual_wf=actual_wf,
            budget=round(spent * 1.25) or 1,
            spent=spent,
            target=target,
            reached=round(target * 0.57),
            vaccine_alloc=round(target * 1.1),
            vaccine_used=round(target * 0.55),
        )


def _microplans(campaign, regions, workers_by_lga, role_rates, rng):
    statuses = ["On track", "Behind", "At risk", "Planned"]
    n = 0
    for region_id, region in regions.items():
        for li, lga in enumerate(region.lgas):
            ws = workers_by_lga.get((region_id, lga), [])
            if not ws:
                continue
            n += 1
            actual_wf = len(ws)
            spent = sum(w["amount"] for w in ws)
            roles = {}
            for w in ws:
                roles.setdefault(w["role_id"], {"roleId": w["role_id"], "planned": 0, "actual": 0})
                roles[w["role_id"]]["actual"] += 1
            for r in roles.values():
                r["planned"] = round(r["actual"] * 1.15) or 1
                r["rate"] = role_rates.get(r["roleId"], 0)
            target = actual_wf * 1200
            Microplan.objects.create(
                campaign=campaign,
                microplan_id=f"MP-{n:04d}",
                region_id=region_id,
                region=region.name,
                lga=lga,
                settlements=actual_wf * 3,
                wards=max(1, actual_wf // 4),
                planned_wf=round(actual_wf * 1.15) or 1,
                actual_wf=actual_wf,
                roles=list(roles.values()),
                budget=round(spent * 1.25) or 1,
                spent=spent,
                planned_to_date=round(spent * 1.1),
                target=target,
                objective=round(target * 0.95),
                goal_pct=95,
                reached=round(target * (0.4 + rng.random() * 0.4)),
                doses=round(target * 1.1),
                doses_used=round(target * 0.5),
                cold_boxes=max(1, actual_wf // 5),
                vehicles=max(1, actual_wf // 10),
                status=rng.choice(statuses),
                owner=f"{region.name} Field Office",
                updated="Jun 3, 2026",
            )


def _report_days(campaign, total_workers, rng):
    rows = []
    for d in range(16):
        daily = total_workers * rng.randint(180, 320) * (0.6 if d < 3 else 1.0)
        rows.append(
            ReportDay(
                campaign=campaign,
                day=f"D{d + 1}",
                order=d,
                enrolled=round(daily),
                attended=round(daily * (0.88 + rng.random() * 0.1)),
                paid=round(daily * (0.7 + rng.random() * 0.15)),
            )
        )
    ReportDay.objects.bulk_create(rows)


def _household_stat(campaign, regions, workers_by_region, target_pop):
    registered = round(target_pop * 0.62)
    coverage = [
        {
            "name": region.name,
            "hh": round(len(workers_by_region.get(rid, [])) * 380) or 100,
            "visited": round(len(workers_by_region.get(rid, [])) * 245) or 60,
        }
        for rid, region in regions.items()
    ]
    HouseholdStat.objects.create(
        campaign=campaign,
        registered=registered,
        visited=round(registered * 0.64),
        members=round(registered * 4.4),
        members_reached=round(registered * 4.4 * 0.65),
        coverage=coverage,
    )


@transaction.atomic
def build_synthetic_campaign(
    *, worker_count, states_limit=None, code=DEFAULT_CODE, name=DEFAULT_NAME, seed_value=20260603
):
    """Build (or rebuild) a full synthetic campaign of ``worker_count`` workers
    spread across real Nigeria geography. Returns the Campaign."""
    if not geography.is_loaded():
        raise geography.GeographyUnavailable(
            "No NGA admin boundaries loaded. Run `manage.py load_geopode_from_drive --iso NGA`."
        )
    rng = random.Random(seed_value)
    workspace, _ = Workspace.objects.get_or_create(slug="nigeria", defaults={"country": "Nigeria", "name": "Nigeria"})
    Campaign.objects.filter(workspace=workspace, code=code).delete()

    # Register this campaign's workers as a synthetic CommCare project space, so the
    # tool reads them through the Case API (served in-app from WorkerCase) — the same
    # way it would read a real CommCare domain.
    domain = f"campaign-synthetic-{code.lower()}"
    SyntheticCommCareDomain.objects.update_or_create(domain=domain, defaults={"label": name, "enabled": True})

    states = geography.states()
    if states_limit:
        states = states[:states_limit]
    state_pop = {s.boundary_id: (s.population or 0) for s in states}
    target_pop = round(sum(state_pop.values()) * 0.18) or 1

    campaign = _campaign(workspace, code=code, name=name, target_pop=target_pop)
    campaign.commcare_domain = domain
    campaign.save(update_fields=["commcare_domain"])

    # Region/Donor/WorkerRole are CommCare-owned reference data. They live in the
    # campaign ORM here as a SYNCED READ-CACHE (sourced from AdminBoundary + config),
    # NOT a competing primary store — exactly how a real tool caches CommCare
    # locations/lookup-tables locally for join performance. CommCare stays the source
    # of truth; this is the local projection the serializer joins worker cases against.
    for o, (did, dname, short, committed, color) in enumerate(seed.DONORS):
        Donor.objects.create(
            campaign=campaign, donor_id=did, name=dname, short=short, committed=committed, color=color, order=o
        )
    role_rates = {}
    for o, (rid, rname, rate) in enumerate(seed.ROLES):
        WorkerRole.objects.create(campaign=campaign, role_id=rid, name=rname, rate=rate, order=o)
        role_rates[rid] = rate

    regions = _regions_from_boundaries(campaign, states)
    # Workers are generated as CommCare cases (WorkerCase). The tool reads them via
    # the Case API (CommCareProvider) — no local Worker ORM copy for this path.
    cases = worker_cases.generate_worker_cases(
        campaign, count=worker_count, states_limit=states_limit, seed=seed_value
    )

    workers_by_region: dict[str, list] = {}
    workers_by_lga: dict[tuple[str, str], list] = {}
    for c in cases:
        p = c.properties
        workers_by_region.setdefault(p["region_id"], []).append(p)
        workers_by_lga.setdefault((p["region_id"], p["lga"]), []).append(p)

    _region_plans(regions, workers_by_region, state_pop)
    _microplans(campaign, regions, workers_by_lga, role_rates, rng)
    _report_days(campaign, worker_count, rng)
    _household_stat(campaign, regions, workers_by_region, target_pop)
    seed.seed_demo_users()
    return campaign
