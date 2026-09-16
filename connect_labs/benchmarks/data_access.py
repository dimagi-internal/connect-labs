"""Reading benchmarks: cohort membership grants, the caller's own access gates.

Two conditions, both required. The opportunity must belong to a cohort (the
grant), AND the caller must hold that opportunity in their Connect access (the
authentication). Neither alone is enough: cohort membership without access
would let anyone read any cohort, and access without membership would have
nothing to read.

The payload is keyed BY COHORT, and that is load-bearing rather than tidy.
Overlapping cohorts are supported on purpose (an opportunity can sit in "All
KMC" and in "Uganda only"), and `peer_index` is only unique within one
publication of one cohort -- so merging cohorts into one indicator map gave two
different opportunities `peer_index 0` for the same indicator, indistinguishable
in a bar chart and, in a series, a "peer 0" line zig-zagging between two
programmes. Keying by cohort is also the honest shape: a peer bar means nothing
without saying which set of peers it was drawn from.
"""

from __future__ import annotations

from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkValue
from connect_labs.labs.access.scopes import Caller, may_use


def benchmarks_for_opportunity(request, opportunity_id: int) -> dict:
    """The latest publication of every cohort this opportunity belongs to.

    The reading opportunity's own published row is excluded, so `peers` is
    always the OTHER members -- see the comment on the query below.

    Shape: {"as_of": ..., "cohorts": {cohort_id: {"name", "as_of"}},
            "indicators": {cohort_id: {series: {indicator_id: {
        "peers": [{peer_index, value}, ...],
        "series": {period: [{peer_index, value}, ...]},
    }}}}}

    Every figure comes from `BenchmarkValue.to_public()`, which is the only
    projection that may reach a viewer -- narrowed further here (a viewer needs
    neither the series nor the indicator repeated inside the entry they key it
    by), never widened.
    """
    opportunity_id = int(opportunity_id)
    empty: dict = {"as_of": None, "cohorts": {}, "indicators": {}}
    if may_use(Caller(request=request), opportunity_id=opportunity_id) is not None:
        return empty

    cohorts = list(BenchmarkCohort.for_opportunity(opportunity_id))
    if not cohorts:
        return empty

    by_cohort: dict[str, dict] = {}
    cohort_meta: dict[str, dict] = {}
    as_of = None
    for cohort in cohorts:
        publication = cohort.publications.order_by("-created_at").first()
        if publication is None:
            continue
        as_of = max(as_of, publication.as_of) if as_of else publication.as_of
        indicators: dict[str, dict] = {}
        # A reader never receives its OWN row back. Two reasons, and the second
        # is the one that bites: the report draws this opportunity as its own
        # bar from its own LIVE figures, so leaving the published copy in the
        # peer set draws it twice -- once at the published value, once at the
        # current one -- and a 12-member cohort renders 13 bars. And a reader
        # who can difference "the set including me" against "me" learns
        # something about the remainder that the floors never budgeted for.
        #
        # `min_peers` was already reasoning this way: its own rule says that
        # below 3 "the reader is one of the contributors, so the one remaining
        # bar is a named peer's exact value". Excluding self makes that
        # arithmetic explicit rather than implied.
        for row in BenchmarkValue.objects.filter(publication=publication).exclude(opportunity_id=opportunity_id):
            public = row.to_public()
            entry = indicators.setdefault(public["series"], {}).setdefault(
                public["indicator_id"],
                {"peers": [], "series": {}},
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
        by_cohort[str(cohort.pk)] = indicators
        cohort_meta[str(cohort.pk)] = {"name": cohort.name, "as_of": str(publication.as_of)}
    return {"as_of": str(as_of) if as_of else None, "cohorts": cohort_meta, "indicators": by_cohort}
