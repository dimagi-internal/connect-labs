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

import logging

from django.http import HttpRequest

logger = logging.getLogger(__name__)

# CommCare work-area case property -> our field name.
_CASE_PROPERTY_PATHS = {
    "ward": "case.properties.ward",
    "lga": "case.properties.lga",
    "state": "case.properties.state",
    "building_count": "case.properties.building_count",
    "expected_visit_count": "case.properties.expected_visit_count",
    "status": "case.properties.status",
}
# Not a case property — a base case field (Connect's internal FLW id, the
# grouping key for §6b's within-FLW clustering + the whole-FLW-average view).
_OWNER_ID_PATH = "case.owner_id"


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
    "expected_visit_count", "status", "owner_id"}``.

    Reuses the same `cchq_cases`/`work-area` pipeline shape as
    `connect_labs.mopup.core.areas.work_area_ids_for_ward`, just with more
    fields projected. `building_count`/`expected_visit_count` are coerced to
    int (0 on missing/bad data) so callers can sum them directly.
    """
    from connect_labs.labs.analysis.config import (
        AnalysisPipelineConfig,
        CacheStage,
        DataSourceConfig,
        FieldComputation,
    )
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    if pipeline is None:
        if request is None:
            raise ValueError("list_work_areas requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="cchq_cases", case_type="work-area"),
        grouping_key="entity_id",
        # NOT the string "visit_level" — backend.py's process_and_cache
        # dispatches on an enum comparison (`terminal_stage == CacheStage.
        # VISIT_LEVEL`), so a bare string here silently falls through to the
        # FLW-aggregation branch on any cache miss. Confirmed elsewhere in
        # this app (core/geometry.py) that this string form can return zero
        # rows on a fresh compute — this call site has likely only ever
        # "worked" because cchq_cases' cache-validity is checked with
        # expected_count=0 (lenient), so a real cache miss here is rare;
        # fixed for correctness regardless of whether it's been hit yet.
        terminal_stage=CacheStage.VISIT_LEVEL,
        fields=[
            FieldComputation(name=name, path=path, aggregation="first") for name, path in _CASE_PROPERTY_PATHS.items()
        ]
        + [FieldComputation(name="owner_id", path=_OWNER_ID_PATH, aggregation="first")],
        # Real production bug, found live this session: leaving this unset
        # makes this ad-hoc config share ONE raw-visit-cache slot per
        # opportunity with every other ad-hoc caller (list_approved_visits,
        # fetch_work_area_geometry included) — each one's wholesale
        # DELETE+INSERT clobbers whatever the others just wrote, exactly the
        # `AnalysisPipelineConfig.pipeline_id` docstring's own documented
        # "issue #116" pattern. 12965 is the existing, already-correct "CHC
        # Work Areas" pipeline definition for this exact case_type/fields
        # shape — reusing its id gives this ad-hoc config its own isolated
        # cache slot without duplicating a saved pipeline record.
        pipeline_id=12965,
    )
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
    work_areas = []
    for row in result.rows:
        # `row.computed` is `None` (not `{}`) for some real cases where field
        # extraction found nothing to compute — confirmed against real
        # program-217 data (opportunity 2154, 41,900+ visits) this session.
        c = row.computed or {}
        work_areas.append(
            {
                "case_id": row.entity_id,
                "ward": c.get("ward") or "",
                "lga": c.get("lga") or "",
                "state": c.get("state") or "",
                "building_count": _int_or_zero(c.get("building_count")),
                "expected_visit_count": _int_or_zero(c.get("expected_visit_count")),
                "status": c.get("status") or "",
                "owner_id": c.get("owner_id") or "",
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


def fetch_connect_implementation_areas(opportunity_id: int, access_token: str, timeout: float = 30.0) -> list[dict]:
    """This opportunity's own uploaded Implementation Area boundaries — the
    ground-truth ward shape Connect's own microplanning actually used to
    define its work areas, as opposed to a third-party name-matched guess
    (see `microplans.core.admin_boundaries`). Only possible since Connect
    shipped a read endpoint for `ImplementationArea` on 2026-09-10
    (commcare-connect#1517) — previously Implementation Area data was
    write-only from labs' side.

    Returns `[{"id", "name", "centroid": geojson, "boundary": geojson}, ...]`
    — `name` is the same value labs itself wrote as `implementation_area`
    when uploading work areas (confirmed same as `ward`, see
    `microplans.core.workarea`/`plan.py`), so callers match on it directly.
    Returns `[]` (never raises) on any API failure — this enriches the map,
    it doesn't gate it; a failure here should silently fall back to the
    existing third-party boundary resolver, not break the page."""
    from connect_labs.labs.integrations.connect.export_client import ExportAPIError
    from connect_labs.labs.integrations.connect.factory import get_export_client

    endpoint = f"/export/opportunity/{opportunity_id}/implementation_areas/"
    try:
        with get_export_client(opportunity_id=opportunity_id, access_token=access_token, timeout=timeout) as client:
            return client.fetch_all(endpoint)
    except ExportAPIError as e:
        logger.warning(f"Failed to fetch Connect implementation areas for opportunity {opportunity_id}: {e}")
        return []
