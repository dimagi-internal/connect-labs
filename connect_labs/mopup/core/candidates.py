"""Assembles a run's scoped work areas + visits + geometry into the exact
per-WA row list `core.indicators.evaluate_run` expects.

**This is the expensive part** — real production opportunities run tens of
thousands of visits and thousands of work areas (verified this session: a
synchronous web request pulling this for a whole opportunity reliably
gateway-times-out). It runs inside `mopup.tasks.fetch_evaluation_data` (a
Celery task, not a request), which is why every fetch function here takes an
already-constructed `pipeline` rather than a `request` — a Celery task has
no HTTP session to derive a token from. `on_stage`, if given, is called with
a short label between each fetch stage (work areas / visits / geometry) so
the task can report real progress via `set_task_progress` (see
`connect_labs.utils.celery`) rather than a bare spinner.

**Known gap, not an oversight**: only WARD scoping is wired up here. A run's
`date_from`/`date_to` (Phase 1) isn't applied yet — `AnalysisPipelineConfig`'s
`date_from`/`date_to` window is only wired into the FLW-rollup aggregation
path (`get_period_scoped_flw_result`), not the raw entity/visit_level pull
this app uses (verified this session). Applying a date bound here needs a
`visit_date` field extracted per visit and filtered in Python, the same way
ward-scoping already is — not built yet since program 217's own round uses
all-time data regardless (§4/§11 of the design brief).
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest

from connect_labs.mopup.core.geometry import fetch_work_area_geometry
from connect_labs.mopup.core.visits import aggregate_visits_by_wa, build_evaluation_rows, list_approved_visits
from connect_labs.mopup.core.work_areas import list_work_areas


def _ward_matches(wa: dict, selected_wards: list[dict]) -> bool:
    if not selected_wards:
        return True  # no selection -> every ward
    return any(
        wa["ward"] == sw.get("ward") and wa["lga"] == sw.get("lga") and wa["state"] == sw.get("state")
        for sw in selected_wards
    )


def build_evaluation_input(
    opportunity_id: int,
    selected_wards: list[dict],
    *,
    request: HttpRequest | None = None,
    pipeline=None,
    on_stage: Callable[[str], None] | None = None,
) -> list[dict]:
    """The full per-WA row list, scoped to `selected_wards` (empty = every
    ward in the opportunity), ready for `core.indicators.evaluate_run`.

    Pass `pipeline` (an already-constructed `AnalysisPipeline`, e.g. built
    from a Celery task's resolved tokens) to fetch every stage through the
    SAME pipeline instance instead of `request`-deriving a fresh one per
    call. Merges in real centroid (`lat`/`lon`, for §6a's spatial neighbor
    graph) and boundary geometry (for a locked candidate's Phase 3
    hand-off) from `core.geometry.fetch_work_area_geometry` — a work area
    with no geometry match just keeps `lat`/`lon`/`boundary` at `None`
    (evaluate_run already degrades gracefully for that)."""

    def stage(label: str) -> None:
        if on_stage:
            on_stage(label)

    stage("Fetching work areas…")
    work_areas = list_work_areas(opportunity_id, request=request, pipeline=pipeline)
    scoped = [wa for wa in work_areas if _ward_matches(wa, selected_wards)]
    wa_ids = {wa["case_id"] for wa in scoped}

    stage("Fetching visit data…")
    visits = list_approved_visits(opportunity_id, request=request, pipeline=pipeline)
    aggregates = aggregate_visits_by_wa(visits, wa_ids=wa_ids)

    rows = build_evaluation_rows(scoped, aggregates)

    stage("Fetching work-area geometry…")
    geometry = fetch_work_area_geometry(opportunity_id, request=request, pipeline=pipeline)
    for row in rows:
        geo = geometry.get(row["wa_id"], {})
        row["lat"] = geo.get("lat")
        row["lon"] = geo.get("lon")
        row["boundary"] = geo.get("boundary")

    stage("Aggregating…")
    return rows


def _empty_ward_row(ward: str, lga: str, state: str) -> dict:
    return {
        "ward": ward,
        "lga": lga,
        "state": state,
        "total_work_areas": 0,
        "total_buildings": 0,
        "total_evc": 0,
        "candidate_count": 0,
        "candidate_buildings": 0,
        "candidate_evc": 0,
        "flagged_by_2_plus": 0,
    }


def summarize_candidates_by_ward(candidates: list[dict], all_rows: list[dict]) -> list[dict]:
    """Per-ward rollup for the candidate table (design brief §8): total work
    areas/buildings/EVC reviewed, how many (and how much) are candidates, and
    how many were flagged by 2+ indicators (§6c) — cheap since severity is
    already computed per candidate.

    Every ward present in `all_rows` gets a row here, even one with zero
    candidates under the current thresholds — this is a survey of what was
    evaluated, not just a rollup of what got flagged. (Real bug, caught live
    against program 217/opportunity 2154: seeding `by_ward` only from
    `candidates` left this table completely empty whenever thresholds
    happened to flag nothing, despite thousands of work areas having been
    evaluated.)"""
    by_ward: dict[tuple[str, str, str], dict] = {}
    for wa in all_rows:
        key = (wa["state"], wa["lga"], wa["ward"])
        row = by_ward.setdefault(key, _empty_ward_row(wa["ward"], wa["lga"], wa["state"]))
        row["total_work_areas"] += 1
        row["total_buildings"] += wa.get("building_count", 0) or 0
        row["total_evc"] += wa.get("expected_visit_count", 0) or 0

    for c in candidates:
        key = (c["state"], c["lga"], c["ward"])
        row = by_ward.setdefault(key, _empty_ward_row(c["ward"], c["lga"], c["state"]))
        row["candidate_count"] += 1
        row["candidate_buildings"] += c.get("building_count", 0) or 0
        row["candidate_evc"] += c.get("expected_visit_count", 0) or 0
        if c["severity_count"] >= 2:
            row["flagged_by_2_plus"] += 1

    return sorted(by_ward.values(), key=lambda r: (r["state"], r["lga"], r["ward"]))


def build_map_features(all_rows: list[dict], candidates: list[dict], gap_features: list[dict] | None = None) -> dict:
    """One GeoJSON Feature per evaluated work area that has boundary
    geometry, for Phase 2's map — a work area with no geometry match is
    skipped (nothing to draw), same "never guess a shape" rule
    `evaluate_run` already follows for missing data.

    `properties.included`/`properties.first_indicator` are all the frontend
    needs to color a feature (see `analysis.js`'s `mapFeatureStyle`) — grey
    for `included: false`, one fixed color per indicator otherwise, using
    only the FIRST triggered indicator when a work area was flagged by
    several (candidate table shows the rest).

    `gap_features`, if given (Step 2's already-computed
    `run.planning_gap_features`), are appended as-is with
    `properties.source = "planning_gap"` added — their own distinct map
    color, separate from the execution-gap candidates above."""
    candidates_by_id = {c["wa_id"]: c for c in candidates}
    features = []
    for wa in all_rows:
        boundary = wa.get("boundary")
        if not boundary:
            continue
        candidate = candidates_by_id.get(wa["wa_id"])
        triggered = candidate["triggered_indicators"] if candidate else []
        features.append(
            {
                "type": "Feature",
                "geometry": boundary,
                "properties": {
                    "wa_id": wa["wa_id"],
                    "ward": wa.get("ward", ""),
                    "included": candidate is not None,
                    "first_indicator": triggered[0] if triggered else None,
                    "source": "existing_wa",
                },
            }
        )
    for gap in gap_features or []:
        props = gap.get("properties", {}) or {}
        features.append(
            {
                "type": "Feature",
                "geometry": gap.get("geometry"),
                "properties": {
                    "wa_id": props.get("cluster", ""),
                    "ward": props.get("ward", ""),
                    "included": True,
                    "first_indicator": None,
                    "source": "planning_gap",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def gap_feature_to_candidate_row(feature: dict) -> dict:
    """Adapts one Step 2 planning-gap GeoJSON Feature into the same shape
    `core.indicators.evaluate_run`'s candidates use, so the candidate table
    can render both with the same code — `severity_count`/
    `triggered_indicators` are placeholders (a gap-fill cell isn't "flagged"
    by an indicator, it's new ground nothing ever covered)."""
    props = feature.get("properties", {}) or {}
    return {
        "wa_id": props.get("cluster", ""),
        "ward": props.get("ward", ""),
        "lga": props.get("lga", ""),
        "state": props.get("state", ""),
        "flw_username": "",
        "boundary": feature.get("geometry"),
        "building_count": props.get("building_count", 0),
        "expected_visit_count": props.get("expected_visit_count", 0),
        "source": "planning_gap",
        "triggered_indicators": ["planning_gap"],
        "severity_count": 0,
        "detail": {},
    }
