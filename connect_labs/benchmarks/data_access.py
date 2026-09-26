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

from connect_labs.benchmarks.models import UNIT_ORGANISATION, BenchmarkCohort, BenchmarkValue
from connect_labs.labs.access.scopes import Caller, may_use


def benchmarks_for_opportunity(request, opportunity_id: int) -> dict:
    """The latest publication of every cohort this opportunity belongs to.

    The reading opportunity's own published rows are kept OUT of `peers` and
    returned separately as `own` / `ownSeries` -- see the comment in the loop.

    Shape: {"as_of": ..., "cohorts": {cohort_id: {"name", "as_of"}},
            "indicators": {cohort_id: {series: {indicator_id: {
        "peers": [{peer_index, value}, ...],           # the OTHER members
        "series": {period: [{peer_index, value}, ...]},
        "own": value | None,                           # this opportunity's own
        "ownSeries": {period: value},                  # published figures
        "organisations": {                             # organisation peers
            "own": {"value", "band"} | None,           #   the reader's organisation
            "others": [{peer_index, value, band}, ...] #   the rest, unnamed, sorted
        },
    }}}}}

    Organisation rows (`unit: organisation`) are the benchmark tab's stable peer
    set. The reader's own organisation is told apart by the publication's
    `organisation_of` map -- provenance, like `opportunity_id`, never returned.

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
        own_organisation = (publication.organisation_of or {}).get(str(opportunity_id))
        # A reader's OWN rows never land in `peers`. Two reasons, and the second
        # is the one that bites: the report draws this opportunity as its own
        # bar, so leaving the published copy among the peers draws it twice --
        # a 12-member cohort renders 13 bars. And a reader who can difference
        # "the set including me" against "me" learns something about the
        # remainder that the floors never budgeted for.
        #
        # `min_peers` was already reasoning this way: its own rule says that
        # below 3 "the reader is one of the contributors, so the one remaining
        # bar is a named peer's exact value". Separating self makes that
        # arithmetic explicit rather than implied.
        #
        # They are returned, though, as `own` / `ownSeries`. A trend chart has
        # to draw its own line from the SAME publication as the peers' lines --
        # the report's live figure is a different vintage, so a line built from
        # it would not be comparable to the ones beside it.
        for row in BenchmarkValue.objects.filter(publication=publication):
            public = row.to_public()
            entry = indicators.setdefault(public["series"], {}).setdefault(
                public["indicator_id"],
                {"peers": [], "series": {}, "own": None, "ownSeries": {}},
            )
            if public["unit"] == UNIT_ORGANISATION:
                orgs = entry.setdefault("organisations", {"own": None, "others": []})
                figure = {"value": public["value"], "band": public["band"]}
                if own_organisation and row.organisation == own_organisation:
                    orgs["own"] = figure
                else:
                    orgs["others"].append({"peer_index": public["peer_index"], **figure})
                continue
            if row.opportunity_id == opportunity_id:
                if public["period"] is None:
                    entry["own"] = public["value"]
                else:
                    entry["ownSeries"][public["period"]] = public["value"]
                continue
            point = {"peer_index": public["peer_index"], "value": public["value"]}
            if public["period"] is None:
                entry["peers"].append(point)
            else:
                entry["series"].setdefault(public["period"], []).append(point)
        for series in indicators.values():
            for entry in series.values():
                entry["peers"].sort(key=lambda p: p["peer_index"])
                if "organisations" in entry:
                    entry["organisations"]["others"].sort(key=lambda p: p["peer_index"])
                for points in entry["series"].values():
                    points.sort(key=lambda p: p["peer_index"])
        by_cohort[str(cohort.pk)] = indicators
        cohort_meta[str(cohort.pk)] = {"name": cohort.name, "as_of": str(publication.as_of)}
    return {"as_of": str(as_of) if as_of else None, "cohorts": cohort_meta, "indicators": by_cohort}
