"""Work-area boundary/centroid geometry — from Connect's own `work_areas`
export, NOT a CommCare HQ case property (verified this session: work-area
cases carry no geometry field at all; Connect's native export is the real
source). This closes two gaps flagged when Phase 2 was first wired to real
data (see core/visits.py's original module docstring):

  1. `lat`/`lon` for §6a's spatial neighbor graph (EVC-shortfall /
     NCF-inaccessible cluster-aware detection) — previously always `None`.
  2. The actual boundary polygon a locked candidate needs for Phase 3's
     hand-off into microplans (`build_mopup_areas` expects a `geometry` per
     candidate work area).

Reuses the exact same live pipeline (`connect_export`/`work_areas`, id 12971
"CHC Work Area Geometry" in this environment) an earlier, since-abandoned
attempt built — that attempt's DASHBOARD didn't work for the reviewer, but
its underlying pipeline definitions are plain data infrastructure, unrelated
to why the dashboard failed, and are still live and correct. Verified
directly against opportunity 2154's real data this session before writing
this module: `boundary`/`centroid` are JSON-stringified GeoJSON, joined to
work-area case data via `wa_case_id == entity_id` (the same case data
`core/work_areas.py` already pulls separately, via cchq_cases).
"""

from __future__ import annotations

import json

from django.http import HttpRequest


def fetch_work_area_geometry(
    opportunity_id: int,
    *,
    request: HttpRequest | None = None,
    pipeline=None,
) -> dict[str, dict]:
    """``{wa_case_id: {"lat": float|None, "lon": float|None, "boundary": dict|None}}``
    for every work area in `opportunity_id`. A row with unparseable/missing
    geometry maps to ``{"lat": None, "lon": None, "boundary": None}`` rather
    than being skipped — callers should treat a missing entry the same way
    (this function never raises on a single bad row)."""
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    if pipeline is None:
        if request is None:
            raise ValueError("fetch_work_area_geometry requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="connect_export", endpoint="work_areas"),
        grouping_key="entity_id",
        terminal_stage="visit_level",
        fields=[
            FieldComputation(name="wa_case_id", path="work_area.case_id", aggregation="first"),
            FieldComputation(name="boundary", path="work_area.boundary", aggregation="first"),
            FieldComputation(name="centroid", path="work_area.centroid", aggregation="first"),
        ],
        # Real production bug, found live this session: without this, this
        # ad-hoc config shares ONE raw-visit-cache slot per opportunity with
        # every other ad-hoc caller in this app — see the identical comment
        # in core/work_areas.py/core/visits.py for the full explanation.
        # 12971 is the existing "CHC Work Area Geometry" pipeline definition
        # for this exact data_source/fields shape.
        pipeline_id=12971,
    )
    # force_refresh: this app's Celery task builds `pipeline` with no `request`
    # (headless), so its `labs_context` is empty and `expected_visits_for`
    # always returns 0 for it — which makes the processed-cache validity check
    # accept ANY existing cached row for this (opportunity_id, pipeline_id)
    # regardless of whether it's stale/incomplete (e.g. missing `wa_case_id`).
    # A false cache hit here silently returns an empty `geometry` dict, which
    # surfaces downstream as every locked candidate's `boundary` being None.
    # This call always wants a guaranteed-fresh read, not a lenient cache hit.
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id, force_refresh=True)

    geometry: dict[str, dict] = {}
    for row in result.rows:
        # `row.computed` is `None` (not `{}`) for some real rows where field
        # extraction found nothing to compute — confirmed against real
        # program-217 data this session.
        c = row.computed or {}
        wa_case_id = c.get("wa_case_id")
        if not wa_case_id:
            continue

        lat = lon = None
        centroid_raw = c.get("centroid")
        if centroid_raw:
            try:
                centroid = json.loads(centroid_raw) if isinstance(centroid_raw, str) else centroid_raw
                lon, lat = centroid["coordinates"]
            except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError):
                lat = lon = None

        boundary = None
        boundary_raw = c.get("boundary")
        if boundary_raw:
            try:
                boundary = json.loads(boundary_raw) if isinstance(boundary_raw, str) else boundary_raw
            except (TypeError, json.JSONDecodeError):
                boundary = None

        geometry[wa_case_id] = {"lat": lat, "lon": lon, "boundary": boundary}

    return geometry
