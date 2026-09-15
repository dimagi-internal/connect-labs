"""Reading benchmarks: cohort membership grants, the caller's own access gates.

Two conditions, both required. The opportunity must belong to a cohort (the
grant), AND the caller must hold that opportunity in their Connect access (the
authentication). Neither alone is enough: cohort membership without access
would let anyone read any cohort, and access without membership would have
nothing to read.
"""

from __future__ import annotations

from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkValue


def _accessible_opp_ids(request) -> set[int]:
    # Shared chokepoint with the synthetic registry, so labs-only opps and
    # real Connect membership are resolved exactly one way.
    from connect_labs.labs.synthetic.registry import accessible_opp_ids

    return accessible_opp_ids(request)


def benchmarks_for_opportunity(request, opportunity_id: int) -> dict:
    """The latest publication of every cohort this opportunity belongs to.

    Shape: {"as_of": ..., "indicators": {series: {indicator_id: {
        "peers": [{peer_index, value}, ...],
        "series": {period: [{peer_index, value}, ...]},
    }}}}

    Every figure comes from `BenchmarkValue.to_public()`, which is the only
    projection that may reach a viewer.
    """
    opportunity_id = int(opportunity_id)
    empty = {"as_of": None, "indicators": {}}
    if opportunity_id not in _accessible_opp_ids(request):
        return empty

    cohorts = list(BenchmarkCohort.for_opportunity(opportunity_id))
    if not cohorts:
        return empty

    indicators: dict[str, dict] = {}
    as_of = None
    for cohort in cohorts:
        publication = cohort.publications.order_by("-created_at").first()
        if publication is None:
            continue
        as_of = max(as_of, publication.as_of) if as_of else publication.as_of
        for row in BenchmarkValue.objects.filter(publication=publication):
            public = row.to_public()
            entry = indicators.setdefault(public["series"], {}).setdefault(
                public["indicator_id"], {"peers": [], "series": {}}
            )
            point = {"peer_index": public["peer_index"], "value": public["value"]}
            if public["period"] is None:
                entry["peers"].append(point)
            else:
                entry["series"].setdefault(public["period"], []).append(point)
    for series in indicators.values():
        for entry in series.values():
            entry["peers"].sort(key=lambda p: p["peer_index"])
            for points in entry["series"].values():
                points.sort(key=lambda p: p["peer_index"])
    return {"as_of": str(as_of) if as_of else None, "indicators": indicators}
