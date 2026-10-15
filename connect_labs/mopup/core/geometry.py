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

# Connect's own work-area `status` values that mean "this area could not be
# reached": an FLW's inaccessibility request is awaiting an admin's review
# (REQUEST_FOR_INACCESSIBLE) or has been approved (INACCESSIBLE); a denied
# request puts the area back to NOT_VISITED, so it correctly drops out. This
# is the ONLY place inaccessibility exists in Connect's data -- it is never a
# visit (the request is a `work_area_update` block handled by
# commcare-connect's form_receiver, not a deliver-unit visit), which is why the
# "Inaccessible WA" visit form the indicator used to look for never existed in
# any CHC opportunity's visit export.
INACCESSIBLE_CONNECT_STATUSES = frozenset({"INACCESSIBLE", "REQUEST_FOR_INACCESSIBLE"})


def _fetch_export_rows(
    opportunity_id: int,
    *,
    request: HttpRequest | None = None,
    pipeline=None,
    caller: str = "fetch_work_area_geometry",
) -> list[dict]:
    """Every row of `opportunity_id`'s Connect `work_areas` export, as the
    computed field dict of the config below -- shared by
    `fetch_work_area_geometry` (keyed by case id) and
    `fetch_work_area_centroids` (every row, case or not)."""
    from connect_labs.labs.analysis.config import (
        AnalysisPipelineConfig,
        CacheStage,
        DataSourceConfig,
        FieldComputation,
    )
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    if pipeline is None:
        if request is None:
            raise ValueError(f"{caller} requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="connect_export", endpoint="work_areas"),
        grouping_key="entity_id",
        # NOT the string "visit_level" — backend.py's process_and_cache
        # dispatches on `config.terminal_stage == CacheStage.VISIT_LEVEL`
        # (an enum comparison, not a value match), so a plain string here
        # silently falls through to _process_flw_level's visit-approval
        # aggregation instead, which then filters out every one of these
        # work-area records (they have no real visit_date/approval status)
        # and returns zero rows. Confirmed live this session: the raw fetch
        # and RawVisitCache write both succeed with 12937+ rows; only the
        # processed result comes back empty, and only when terminal_stage
        # was this bare string.
        terminal_stage=CacheStage.VISIT_LEVEL,
        fields=[
            FieldComputation(name="wa_case_id", path="work_area.case_id", aggregation="first"),
            FieldComputation(name="boundary", path="work_area.boundary", aggregation="first"),
            FieldComputation(name="centroid", path="work_area.centroid", aggregation="first"),
            # Already on this SAME export record (WorkAreaDataSerializer's
            # own `work_area_group_name` SerializerMethodField, verified
            # against commcare-connect's real serializer) -- no separate
            # `work_area_groups` endpoint/join needed.
            FieldComputation(name="wag_name", path="work_area.work_area_group_name", aggregation="first"),
            # Same export record again (`status` is on WorkAreaDataSerializer):
            # Connect's native status enum, the only place a work area being
            # inaccessible is recorded -- see INACCESSIBLE_CONNECT_STATUSES.
            FieldComputation(name="connect_status", path="work_area.status", aggregation="first"),
            # The work area's assigned worker, as a CONNECT username (the id
            # space the user_data export's `suspended` flag is keyed by) --
            # read by WA Revisit R2's suspended-worker rule (core/r2.py).
            FieldComputation(name="owner_username", path="work_area.username", aggregation="first"),
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
    # `row.computed` is `None` (not `{}`) for some real rows where field
    # extraction found nothing to compute — confirmed against real
    # program-217 data this session.
    return [row.computed or {} for row in result.rows]


def fetch_work_area_geometry(
    opportunity_id: int,
    *,
    request: HttpRequest | None = None,
    pipeline=None,
) -> dict[str, dict]:
    """``{wa_case_id: {"lat": float|None, "lon": float|None, "boundary": dict|None, "wag_name": str,
    "connect_status": str, "owner_username": str}}`` for every work area in `opportunity_id`.
    `connect_status` is Connect's own work-area status enum, upper-cased ("" if
    absent) -- see `INACCESSIBLE_CONNECT_STATUSES` for the values the
    Inaccessible indicator keys off. A row with unparseable/missing
    geometry maps to ``{"lat": None, "lon": None, "boundary": None, "wag_name": "", ...}``
    rather than being skipped — callers should treat a missing entry the
    same way (this function never raises on a single bad row)."""
    geometry: dict[str, dict] = {}
    for c in _fetch_export_rows(opportunity_id, request=request, pipeline=pipeline):
        wa_case_id = c.get("wa_case_id")
        if not wa_case_id:
            continue

        lon, lat = _parse_centroid(c.get("centroid"))

        boundary = None
        boundary_raw = c.get("boundary")
        if boundary_raw:
            try:
                boundary = json.loads(boundary_raw) if isinstance(boundary_raw, str) else boundary_raw
            except (TypeError, json.JSONDecodeError):
                boundary = None

        geometry[wa_case_id] = {
            "lat": lat,
            "lon": lon,
            "boundary": boundary,
            "wag_name": c.get("wag_name") or "",
            "connect_status": str(c.get("connect_status") or "").upper(),
            "owner_username": str(c.get("owner_username") or ""),
        }

    return geometry


def _parse_centroid(centroid_raw) -> tuple[float | None, float | None]:
    """``(lon, lat)`` from the export's GeoJSON Point (a JSON string or an
    already-parsed dict); ``(None, None)`` if missing or unparseable."""
    if not centroid_raw:
        return None, None
    try:
        centroid = json.loads(centroid_raw) if isinstance(centroid_raw, str) else centroid_raw
        lon, lat = centroid["coordinates"]
        return float(lon), float(lat)
    except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError):
        return None, None


def fetch_work_area_centroids(
    opportunity_id: int,
    *,
    request: HttpRequest | None = None,
    pipeline=None,
) -> list[tuple[float, float]]:
    """``(lon, lat)`` of EVERY work area in `opportunity_id`'s export,
    including ones with no CommCare case yet (an uploaded but unassigned
    work area has ``case_id: null`` -- every work area in a freshly uploaded
    revisit opportunity looks like that). WA Revisit R2 matches these against
    the original opportunity's centroids to leave out what R1 already sent
    back (core/r2.py); `fetch_work_area_geometry` would drop them."""
    centroids = []
    for c in _fetch_export_rows(
        opportunity_id, request=request, pipeline=pipeline, caller="fetch_work_area_centroids"
    ):
        lon, lat = _parse_centroid(c.get("centroid"))
        if lon is not None and lat is not None:
            centroids.append((lon, lat))
    return centroids
