"""Turn a completed run's snapshot into a publication of anonymous figures.

The source is the frozen snapshot a saved run already carries -- it holds the
`opportunity` and `opportunity_month` scopes for every series in one semantic
pass -- so publication reads and recomputes nothing.

Publication is explicit: nothing here runs on completion unless the cohort sets
`auto_publish_on_completion`.
"""

from __future__ import annotations

from django.db import transaction

from connect_labs.benchmarks.disclosure import PeerObservation, anonymise_point, anonymise_series
from connect_labs.benchmarks.models import BenchmarkPublication, BenchmarkValue

POINT_SCOPE = "opportunity"
SERIES_SCOPE = "opportunity_month"


def _observations(rows, members: set[int]) -> list[PeerObservation]:
    """Snapshot rows -> observations, keeping only cohort members.

    A report spans every opportunity it was built over; a cohort may be a subset,
    and an opportunity that is not a member must never be benchmarked.
    """
    out = []
    for row in rows or []:
        opp = row.get("opportunity_id")
        if opp is None or int(opp) not in members:
            continue
        value = row.get("value")
        if value is None:
            continue
        out.append(
            PeerObservation(
                opportunity_id=int(opp),
                value=float(value),
                denominator=int(row.get("denominator") or 0),
                suppressed=bool(row.get("suppressed")),
            )
        )
    return out


def observations_from_snapshot(snapshot: dict, series: str, indicator_id: str, members: set[int]):
    """`(point_observations, {period: observations})` for one indicator."""
    block = (snapshot.get("series") or {}).get(series) or {}
    points = _observations((block.get(POINT_SCOPE) or {}).get(indicator_id), members)
    by_period = {
        period: _observations(rows, members)
        for period, rows in ((block.get(SERIES_SCOPE) or {}).get(indicator_id) or {}).items()
    }
    return points, by_period


@transaction.atomic
def publish_benchmark(
    cohort,
    *,
    snapshot: dict,
    source_workflow_id: int,
    source_run_id: int,
    registry_id: int | None,
    as_of: str,
    published_by: str = "",
) -> BenchmarkPublication:
    """Publish every benchmarkable indicator in `snapshot` for `cohort`.

    Atomic: a publication is either complete or absent, so no partner ever reads
    a half-written benchmark.
    """
    publication = BenchmarkPublication.objects.create(
        cohort=cohort,
        source_workflow_id=source_workflow_id,
        source_run_id=source_run_id,
        registry_id=registry_id,
        as_of=as_of,
        published_by=published_by,
    )
    members = cohort.opportunity_ids
    thresholds = {"min_peers": cohort.min_peers, "min_denominator": cohort.min_denominator}

    rows: list[BenchmarkValue] = []
    for series_name, block in (snapshot.get("series") or {}).items():
        for indicator_id in block.get(POINT_SCOPE) or {}:
            points, by_period = observations_from_snapshot(snapshot, series_name, indicator_id, members)
            for peer_index, value, opportunity_id in anonymise_point(
                points, **thresholds, tie_salt=f"{publication.pk}:{series_name}:{indicator_id}"
            ):
                rows.append(
                    BenchmarkValue(
                        publication=publication,
                        series=series_name,
                        indicator_id=indicator_id,
                        period=None,
                        peer_index=peer_index,
                        value=value,
                        opportunity_id=opportunity_id,
                    )
                )
            for period, points_for_period in anonymise_series(
                by_period, **thresholds, tie_salt=f"{publication.pk}:{series_name}:{indicator_id}"
            ).items():
                for peer_index, value, opportunity_id in points_for_period:
                    rows.append(
                        BenchmarkValue(
                            publication=publication,
                            series=series_name,
                            indicator_id=indicator_id,
                            period=period,
                            peer_index=peer_index,
                            value=value,
                            opportunity_id=opportunity_id,
                        )
                    )
    BenchmarkValue.objects.bulk_create(rows, batch_size=1000)
    return publication
