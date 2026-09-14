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
    access_token: str | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> list[dict]:
    """The full per-WA row list, scoped to `selected_wards` (empty = every
    ward in the opportunity), ready for `core.indicators.evaluate_run`.

    Pass `pipeline` (an already-constructed `AnalysisPipeline`, e.g. built
    from a Celery task's resolved tokens) to fetch every stage through the
    SAME pipeline instance instead of `request`-deriving a fresh one per
    call. Merges in real centroid (`lat`/`lon`, for §6a's spatial neighbor
    graph), boundary geometry (for a locked candidate's Phase 3
    hand-off), and the work area's own WAG (Work Area Group) name from
    `core.geometry.fetch_work_area_geometry` — a work area with no
    geometry match just keeps `lat`/`lon`/`boundary` at `None` and
    `wag_name` at `""` (evaluate_run already degrades gracefully for that).

    `access_token`, if given, resolves each row's `flw_username` (the raw
    Connect FLW id) into a real display name via
    `labs.analysis.data_access.fetch_flw_names` — the same
    cached-and-reused mechanism `audit`/`custom_analysis`/`workflow` already
    use for this exact "raw FLW id -> display name" lookup. Without it,
    `flw_name` falls back to the raw id (same as every other caller of that
    helper does for a username with no resolved name)."""

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
        row["wag_name"] = geo.get("wag_name") or ""

    if access_token:
        stage("Resolving FLW names…")
        from connect_labs.labs.analysis.data_access import fetch_flw_names

        flw_names = fetch_flw_names(access_token, opportunity_id)
        for row in rows:
            row["flw_name"] = flw_names.get(row["flw_username"], row["flw_username"])
    else:
        for row in rows:
            row["flw_name"] = row["flw_username"]

    stage("Aggregating…")
    return rows


def _empty_ward_row(ward: str, lga: str, state: str) -> dict:
    return {
        "ward": ward,
        "lga": lga,
        "state": state,
        "total_work_areas": 0,
        "total_hsd": 0,
        "total_ncf": 0,
        "total_buildings": 0,
        "total_evc": 0,
        "candidate_count": 0,
        "candidate_buildings": 0,
        "candidate_evc": 0,
        "flagged_by_2_plus": 0,
    }


def ward_key(ward: str, lga: str, state: str) -> str:
    """The composite string key used to align a ward's row across
    `summarize_candidates_by_ward` and `gap_summary_by_ward` — plain
    ward-name lookup risks colliding two same-named wards in different
    LGAs/states, so every ward-keyed dict in this module uses this same
    "state|lga|ward" composite."""
    return f"{state}|{lga}|{ward}"


def summarize_candidates_by_ward(candidates: list[dict], all_rows: list[dict]) -> list[dict]:
    """Per-ward rollup for the candidate table (design brief §8): total work
    areas/HSD/NCF visits/buildings/EVC reviewed, how many (and how much) are
    candidates, and how many were flagged by 2+ indicators (§6c) — cheap
    since severity is already computed per candidate.

    `total_hsd`/`total_ncf` mirror the NCF/inaccessible indicator's own
    definition (`core.indicators._ncf_visit_total`: NCF + Inaccessible visits
    counted together) so the ward summary's numbers are traceable back to
    what the indicator itself is computing.

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
        row["total_hsd"] += wa.get("approved_hsd_count", 0) or 0
        row["total_ncf"] += (wa.get("approved_ncf_count", 0) or 0) + (wa.get("approved_inaccessible_count", 0) or 0)
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


def gap_summary_by_ward(gap_features: list[dict]) -> dict[str, dict]:
    """Step 2's gap-fill work areas, rolled up per ward — a sibling to
    `summarize_candidates_by_ward`, not merged into its rows, so the
    frontend can render Step 2's contribution as its own distinguishable
    sub-row per ward rather than widening the table with more columns.
    Keyed by `ward_key` (state|lga|ward) so the frontend can align it
    against the matching main-row without a same-named-ward collision."""
    by_ward: dict[str, dict] = {}
    for feature in gap_features:
        props = feature.get("properties", {}) or {}
        key = ward_key(props.get("ward", ""), props.get("lga", ""), props.get("state", ""))
        row = by_ward.setdefault(
            key,
            {
                "ward": props.get("ward", ""),
                "lga": props.get("lga", ""),
                "state": props.get("state", ""),
                "gap_wa_count": 0,
                "gap_buildings": 0,
                "gap_evc": 0,
            },
        )
        row["gap_wa_count"] += 1
        row["gap_buildings"] += props.get("building_count", 0) or 0
        row["gap_evc"] += props.get("expected_visit_count", 0) or 0
    return by_ward


def build_map_features(
    all_rows: list[dict],
    candidates: list[dict],
    gap_features: list[dict] | None = None,
    building_points: list[dict] | None = None,
) -> dict:
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
    color, separate from the execution-gap candidates above.

    `building_points`, if given (Step 2's "upload your own" mode —
    `run.planning_gap_building_points`, individual `{"lon", "lat"}` dicts),
    are appended as Point features tagged `properties.source =
    "uploaded_building"` — the real building positions behind the gap-fill
    cells above, not just the gridded cells themselves.

    Every `existing_wa`/`planning_gap` feature also carries the raw counts
    the map's hover tooltip needs (`analysis.js`'s `mapHoverContent`) —
    `approved_hsd_count`/`expected_visit_count`/`building_count`, plus
    `deworming_given`/`muac_given`/`vaccination_given` for existing work
    areas (a planning-gap cell has no visit history of its own, so those
    three are meaningless for it and left out). Percentages (HSD/EVC,
    deworming/EVC, etc.) are computed client-side from these raw numbers,
    not here — same division-by-zero handling either language would need,
    so no reason to duplicate it."""
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
                    "wa_name": wa.get("wa_name", ""),
                    "wag_name": wa.get("wag_name", ""),
                    "flw_name": wa.get("flw_name", ""),
                    "ward": wa.get("ward", ""),
                    "included": candidate is not None,
                    "first_indicator": triggered[0] if triggered else None,
                    "source": "existing_wa",
                    "approved_hsd_count": wa.get("approved_hsd_count", 0),
                    "expected_visit_count": wa.get("expected_visit_count", 0),
                    "building_count": wa.get("building_count", 0),
                    "deworming_given": wa.get("deworming_given", 0),
                    "muac_given": wa.get("muac_given", 0),
                    "vaccination_given": wa.get("vaccination_given", 0),
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
                    "building_count": props.get("building_count", 0),
                    "expected_visit_count": props.get("expected_visit_count", 0),
                },
            }
        )
    for point in building_points or []:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [point["lon"], point["lat"]]},
                "properties": {
                    "wa_id": "",
                    "ward": "",
                    "included": True,
                    "first_indicator": None,
                    "source": "uploaded_building",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def filter_gap_features(gap_features: list[dict], excluded_wa_ids) -> list[dict]:
    """Drops any planning-gap Feature whose `properties.cluster` is in
    `excluded_wa_ids` — the map view's "Not include" action works on gap
    cells the same way it works on real work areas (see
    `views._apply_exclusions`), since a gap-cluster id like
    "{area_id}-gap-{cluster}" never collides with a real CommCare wa_id and
    is exactly what `build_map_features`/`gap_feature_to_candidate_row`
    already expose as that feature's own `wa_id`. Called both from
    `MopupCandidatesView` (live preview) and `handoff.create_plan_from_locked_run`
    (so an exclusion made before hand-off is actually honored in the plan,
    not just hidden from the UI)."""
    excluded = set(excluded_wa_ids)
    if not excluded:
        return gap_features
    return [f for f in gap_features if (f.get("properties", {}) or {}).get("cluster") not in excluded]


def gap_feature_to_candidate_row(feature: dict) -> dict:
    """Adapts one Step 2 planning-gap GeoJSON Feature into the same shape
    `core.indicators.evaluate_run`'s candidates use, so the candidate table
    can render both with the same code — `severity_count`/
    `triggered_indicators` are placeholders (a gap-fill cell isn't "flagged"
    by an indicator, it's new ground nothing ever covered)."""
    props = feature.get("properties", {}) or {}
    return {
        "wa_id": props.get("cluster", ""),
        "wa_name": "",
        "wag_name": "",
        "ward": props.get("ward", ""),
        "lga": props.get("lga", ""),
        "state": props.get("state", ""),
        "flw_username": "",
        "flw_name": "",
        "boundary": feature.get("geometry"),
        "building_count": props.get("building_count", 0),
        "expected_visit_count": props.get("expected_visit_count", 0),
        "source": "planning_gap",
        "triggered_indicators": ["planning_gap"],
        "severity_count": 0,
        "detail": {},
    }
