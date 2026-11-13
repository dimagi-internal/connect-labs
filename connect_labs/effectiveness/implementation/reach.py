"""What Pulse knows about an opportunity: the people it reached, where its visits centre, and their areas."""

from __future__ import annotations

from django.db.models import Avg, Count, Q

from connect_labs.effectiveness.implementation.catalog import SERVICE_MODEL, iso3_for

REACH_DISTINCT = "distinct_people"


REACH_PROXY = "busiest_payment_unit"


def people_reached(opp_ids) -> dict[int, dict]:
    """People reached per opportunity: ``{reached, reach_basis, works_per_person}``."""
    from connect_labs.pulse.models import PulseWork

    approved = PulseWork.objects.filter(opportunity_id__in=list(opp_ids), status="approved")
    keyed = {
        r["opportunity_id"]: r
        for r in approved.exclude(beneficiary_key="")
        .values("opportunity_id")
        .annotate(people=Count("beneficiary_key", distinct=True), works=Count("id"))
    }
    busiest: dict[int, int] = {}
    for r in approved.values("opportunity_id", "payment_unit_id").annotate(n=Count("id")):
        busiest[r["opportunity_id"]] = max(busiest.get(r["opportunity_id"], 0), r["n"])

    out: dict[int, dict] = {}
    for oid in set(keyed) | set(busiest):
        k = keyed.get(oid)
        if k and k["people"]:
            out[oid] = {
                "reached": k["people"],
                "reach_basis": REACH_DISTINCT,
                "works_per_person": round(k["works"] / k["people"], 2),
            }
        else:
            out[oid] = {"reached": busiest.get(oid, 0), "reach_basis": REACH_PROXY, "works_per_person": None}
    return out


def centres(opp_ids) -> dict[int, tuple[float, float]]:
    """Mean position of each opportunity's approved visits that carry GPS."""
    from connect_labs.pulse.models import PulseEvent

    rows = (
        PulseEvent.objects.filter(opportunity_id__in=list(opp_ids), status="approved")
        .exclude(lat__isnull=True)
        .exclude(lon__isnull=True)
        .exclude(Q(lat=0) & Q(lon=0))
        .values("opportunity_id")
        .annotate(lat=Avg("lat"), lon=Avg("lon"))
    )
    return {r["opportunity_id"]: (round(r["lat"], 3), round(r["lon"], 3)) for r in rows}


def opportunity_areas(opps, reach_of: dict) -> dict:
    """The place layer for every valued opportunity: ``{opp_id: [area dict, ...]}``.

    One spatial query places every approved visit (``places``), and one
    resolver reads every area's baseline (``baseline``). An opportunity
    with no placed visits is left out and valued at its country's figures.
    """
    from connect_labs.effectiveness import baseline, places

    wanted = {
        o.opportunity_id: (SERVICE_MODEL.get(o.service_slug or ""), iso3_for(o.country))
        for o in opps
        if SERVICE_MODEL.get(o.service_slug or "") and iso3_for(o.country)
    }
    if not wanted:
        return {}
    placed = places.opportunity_places(wanted)
    boundaries = {a.boundary.pk: a.boundary for p in placed.values() for a in p.areas}
    resolver = baseline.Baselines(list(boundaries.values()))
    out = {}
    for opp_id, (key, iso3) in wanted.items():
        p = placed.get(opp_id)
        # Only areas in the opportunity's own country: a stray point over a border
        # must not value a delivery with a neighbour's figures.
        areas = [a for a in (p.areas if p else []) if a.boundary.iso_code == iso3]
        total = sum(a.visits for a in areas)
        if not total:
            continue
        out[opp_id] = [
            {
                "name": a.boundary.name,
                "level": baseline.LEVEL_NAMES.get(a.boundary.admin_level, ""),
                "boundary_id": a.boundary.pk,
                "visits": a.visits,
                "share": a.visits / total,
                "inputs": resolver.for_area(key, iso3, a.boundary),
            }
            for a in areas
        ]
    return out
