"""Cheap, per-opportunity work-area case data — ward/lga/state/building_count/
expected_visit_count/status only, NOT the heavier per-child visit-form data.

This is Phase 1's ward-picker source (design brief §4): cheap because it's
CommCare case properties, not per-child form fields. There's no bespoke
caching here — `AnalysisPipeline` already caches (opportunity, data_source
config) results in Postgres (`RawVisitCache`/the SQL backend's cache
manager) and honors a manual refresh: when the pipeline is constructed with
a real Django `request`, appending `?refresh=1` to that request's query
string makes the underlying fetch bypass the cache (see
`AnalysisPipeline.stream_analysis`'s own `request.GET.get("refresh")` check).
A Phase 1 "refresh wards" button just needs to re-request with that param.
"""

from __future__ import annotations

from django.http import HttpRequest

# CommCare work-area case property -> our field name.
_CASE_PROPERTY_PATHS = {
    "ward": "case.properties.ward",
    "lga": "case.properties.lga",
    "state": "case.properties.state",
    "building_count": "case.properties.building_count",
    "expected_visit_count": "case.properties.expected_visit_count",
    "status": "case.properties.status",
}


def _int_or_zero(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def list_work_areas(
    opportunity_id: int,
    *,
    request: HttpRequest | None = None,
    pipeline=None,
) -> list[dict]:
    """One dict per work-area case in `opportunity_id`:
    ``{"case_id", "ward", "lga", "state", "building_count",
    "expected_visit_count", "status"}``.

    Reuses the same `cchq_cases`/`work-area` pipeline shape as
    `connect_labs.mopup.core.areas._work_area_ids_for_ward`, just with more
    fields projected. `building_count`/`expected_visit_count` are coerced to
    int (0 on missing/bad data) so callers can sum them directly.
    """
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    if pipeline is None:
        if request is None:
            raise ValueError("list_work_areas requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="cchq_cases", case_type="work-area"),
        grouping_key="entity_id",
        terminal_stage="visit_level",
        fields=[
            FieldComputation(name=name, path=path, aggregation="first") for name, path in _CASE_PROPERTY_PATHS.items()
        ],
    )
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
    work_areas = []
    for row in result.rows:
        c = row.computed
        work_areas.append(
            {
                "case_id": row.entity_id,
                "ward": c.get("ward") or "",
                "lga": c.get("lga") or "",
                "state": c.get("state") or "",
                "building_count": _int_or_zero(c.get("building_count")),
                "expected_visit_count": _int_or_zero(c.get("expected_visit_count")),
                "status": c.get("status") or "",
            }
        )
    return work_areas


def summarize_wards(work_areas: list[dict]) -> list[dict]:
    """Roll up `list_work_areas`' per-WA rows into one row per distinct
    (state, lga, ward): work-area count + summed building/expected-visit
    counts — exactly what Phase 1's ward picker needs to show/select from,
    without pulling any visit-form data yet."""
    by_ward: dict[tuple[str, str, str], dict] = {}
    for wa in work_areas:
        key = (wa["state"], wa["lga"], wa["ward"])
        if not key[2]:
            continue  # no ward name — nothing to attribute this WA to
        row = by_ward.setdefault(
            key,
            {
                "ward": wa["ward"],
                "lga": wa["lga"],
                "state": wa["state"],
                "work_area_count": 0,
                "building_count": 0,
                "expected_visit_count": 0,
            },
        )
        row["work_area_count"] += 1
        row["building_count"] += wa["building_count"]
        row["expected_visit_count"] += wa["expected_visit_count"]
    return sorted(by_ward.values(), key=lambda r: (r["state"], r["lga"], r["ward"]))
