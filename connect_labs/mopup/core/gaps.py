"""Planning-gap detection (design brief §7): buildings inside a ward that no
existing work area covers, gridded into new candidate WorkArea features.

Computed at Phase 2's Step 2 (`mopup.tasks.preview_planning_gaps`, only
reachable once the run is locked), not at Phase 3 hand-off time — an earlier
version computed this at hand-off, but that meant the reviewer never saw the
gap-fill work areas before they landed in the final plan, and a real
`include_planning_gaps=True` hand-off measured well over a minute
synchronously (fetching + diffing thousands of buildings). Step 2's own
Recompute button now does that work up front, with the result stored on the
run (`MopupRunRecord.planning_gap_features`) and carried forward as-is by
`core.handoff.create_plan_from_locked_run` — no recomputation at hand-off.

Step 2 also supports a third building source alongside Overture/OSM/Microsoft:
a user-uploaded CSV (`buildings_from_upload`), for when the reviewer has
better local building data than any of the automated providers carry. Either
way, the buildings feed the SAME `buildings_not_covered`/`grid_clusters`
pipeline below — only where the DataFrame comes from differs.

An uploaded CSV is shrunk to this run's own ward(s) at UPLOAD time
(`filter_upload_to_wards`) and stored as CSV text directly on the run's own
JSON record (`MopupRunRecord.uploaded_buildings_csv`) — deliberately NOT
Django's file storage/S3 (`default_storage`), which has no working bucket/
IAM wiring for labs (confirmed live: every upload attempt through that path
500'd). This mirrors how `microplans`' own "upload your own boundary"
feature works (parse client-side or server-side, store the result directly
on the record — never touch file storage at all), adapted here to keep the
data server-side (a 28MB/362k-row CSV is a much heavier payload than a
handful of boundary polygons, so parsing happens in Python via pandas
rather than in the browser).

Deliberately not built on top of `microplans.coverage.frame.generate_coverage_frame`
— that function always calls `fetch_buildings` itself with no seam to inject
a pre-filtered building set, and adding one would be a microplans-file change
to a widely-used function for exactly one caller. This module composes the
same lower-level primitives (`fetch_buildings`, `clustering.grid_clusters`)
directly instead, keeping microplans untouched, per this app's zero-footprint
architecture constraint (see `connect_labs/mopup/models.py`'s module
docstring).
"""

from __future__ import annotations

import logging

import pandas as pd
from django.http import HttpRequest
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

from connect_labs.microplans.core.footprints import fetch_buildings

logger = logging.getLogger(__name__)


def buildings_within_ward(
    ward_boundary: BaseGeometry,
    buildings: pd.DataFrame | None = None,
    *,
    min_confidence: float | None = None,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    """Buildings whose centroid falls inside `ward_boundary` — with NO
    existing-work-area exclusion applied (see `buildings_not_covered` for
    that). This is Step 2's own definition of "every building in this ward,"
    used for the map's "uploaded buildings" layer (every uploaded building
    actually inside the ward, whether or not an existing work area already
    covers it) as well as the first stage of `buildings_not_covered`.

    `buildings`, if given, is used instead of calling `fetch_buildings` —
    the seam Step 2's "upload your own building data" mode uses (see
    `buildings_from_upload`). `min_confidence`/`sources` are ignored when
    `buildings` is given (they're `fetch_buildings`-specific filters with no
    meaning for an already-built frame).

    Unlike `fetch_buildings` (which only ever returns buildings whose
    centroid falls inside `ward_boundary` to begin with — an Overture query
    scoped to that area), a pre-built `buildings` frame is matched by ward
    NAME only (`buildings_from_upload`'s exact-match, not spatial), so it is
    NOT guaranteed to already be confined to `ward_boundary`'s actual shape.
    This function clips it here — a row a reviewer's upload tags as this
    ward but that falls outside the boundary actually selected/reviewed for
    this mop-up round is dropped, matching the requirement that an upload
    may cover more ground than this run reviews, but only the reviewed
    boundary's own area is ever shown/computed.

    Raises `ValueError` (the same `MAX_AREA_KM2` guard) via `fetch_buildings`
    — only when `buildings` is not given.
    """
    if buildings is None:
        return fetch_buildings(ward_boundary, min_confidence=min_confidence, sources=sources)
    if buildings.empty:
        return buildings
    prepared_ward = prep(ward_boundary)
    inside = [prepared_ward.contains(Point(lon, lat)) for lon, lat in zip(buildings["lon"], buildings["lat"])]
    return buildings[pd.Series(inside, index=buildings.index)]


def _exclude_covered(buildings: pd.DataFrame, existing_wa_boundaries: list[dict]) -> pd.DataFrame:
    """`buildings` minus whichever rows fall inside the union of
    `existing_wa_boundaries` — the "never covered by any existing work
    area" step of `buildings_not_covered`, split out so `planning_gap_features`
    can apply it AFTER already keeping every within-ward building for the
    map (see `buildings_within_ward`), rather than only the ones that
    survive both filters."""
    if not existing_wa_boundaries or buildings.empty:
        return buildings
    covered = prep(unary_union([shape(g) for g in existing_wa_boundaries]))
    keep = [not covered.contains(Point(lon, lat)) for lon, lat in zip(buildings["lon"], buildings["lat"])]
    return buildings[pd.Series(keep, index=buildings.index)]


def buildings_not_covered(
    ward_boundary: BaseGeometry,
    existing_wa_boundaries: list[dict],
    *,
    min_confidence: float | None = None,
    sources: list[str] | None = None,
    buildings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Buildings inside `ward_boundary` whose centroid does NOT fall inside
    the union of `existing_wa_boundaries` — the "never covered by any
    existing work area" remainder that actually becomes new gap-fill work
    areas. Returns the same DataFrame shape
    `microplans.core.footprints.fetch_buildings` returns
    (`lon`/`lat`/`area_m2`/`confidence`/`dataset`), ready to feed straight
    into `microplans.core.clustering.grid_clusters`.

    ``existing_wa_boundaries``: GeoJSON geometries (dicts) of EVERY existing
    work area in the ward — not just the locked candidates, since a
    candidate being revisited is itself an existing WA and its footprint is
    already covered.

    Composed from `buildings_within_ward` (the ward-boundary clip — see
    that function for what `buildings`/`min_confidence`/`sources` do) then
    `_exclude_covered` (the existing-WA exclusion). `planning_gap_features`
    calls those two pieces directly instead of this function, so it can
    keep the ward-clipped-but-not-yet-excluded set for the map too.
    """
    within_ward = buildings_within_ward(ward_boundary, buildings, min_confidence=min_confidence, sources=sources)
    return _exclude_covered(within_ward, existing_wa_boundaries)


def _normalize_name(s: str | None) -> str:
    return " ".join(str(s or "").strip().casefold().split())


def filter_upload_to_wards(df: pd.DataFrame, wards: list[dict]) -> pd.DataFrame:
    """Shrinks an uploaded building CSV, AT UPLOAD TIME, down to only the
    row(s) whose ward/LGA/state exactly match one of `wards` (this run's own
    locked wards — see `core.areas.distinct_wards`) — before anything gets
    stored on the run.

    A reviewer's file is explicitly allowed to cover more ground than this
    mop-up round reviews (see `buildings_from_upload`'s own docstring), but
    there is no reason to keep rows for wards this run will never touch:
    shrinking here is what keeps what's actually stored on the run small,
    regardless of how many wards the uploaded file itself spans (confirmed
    against a real sample: 80 wards / 362k rows in the file, but a single
    mop-up round reviews only a handful of them at most).

    `wards`: `[{"ward": ..., "lga": ..., "state": ...}, ...]`. Matching is
    the same exact, case/whitespace-normalized comparison
    `buildings_from_upload` uses — deliberately not fuzzy.

    Raises `KeyError` if a required column is missing (surfaced directly to
    the uploader as a 400, not deferred to Recompute time)."""
    required = ("wardname", "lganame", "statename")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"uploaded file is missing required column(s): {', '.join(missing)}")

    wanted = {(_normalize_name(w["ward"]), _normalize_name(w["lga"]), _normalize_name(w["state"])) for w in wards}
    keys = zip(
        df["wardname"].map(_normalize_name), df["lganame"].map(_normalize_name), df["statename"].map(_normalize_name)
    )
    mask = [k in wanted for k in keys]
    return df[pd.Series(mask, index=df.index)]


def buildings_from_upload(df: pd.DataFrame, ward: str, lga: str, state: str) -> pd.DataFrame:
    """Filters a user-uploaded building CSV down to this ward's rows, and
    reshapes them into the same `lon`/`lat`/`area_m2`/`confidence`/`dataset`
    frame `fetch_buildings` produces, ready for `buildings_not_covered`.

    Expected input columns (confirmed against a real sample this session):
    `latitude`, `longitude`, `wardname`, `lganame`, `statename` (required),
    plus optional `area_in_meters`/`confidence` (kept if present, `None`
    otherwise — same "OSM/Microsoft have no confidence" convention
    `fetch_buildings` already follows). Any other column (e.g. an RCT `wardcode`/
    `treatment` label) is ignored — this app only needs building positions.

    Matching is an EXACT match on ward/LGA/state name, case/whitespace-
    normalized only — deliberately not fuzzy, since fuzzy ward-name
    matching is the exact mechanism behind this app's own ward-boundary
    mismatch problem (see `MopupAnalysisView._ward_boundaries_geojson`).
    A row whose ward isn't part of this run's selected wards is simply
    never matched here (not an error) — the upload is allowed to cover
    wards outside this mop-up round; only the ones actually being
    reviewed get their gap cells computed.

    Raises `KeyError` if a required column is missing — surfaced to the
    caller as a per-ward warning (see `tasks.preview_planning_gaps`), same
    as any other per-ward failure in that loop.
    """
    required = ("latitude", "longitude", "wardname", "lganame", "statename")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"uploaded file is missing required column(s): {', '.join(missing)}")

    mask = (
        (df["wardname"].map(_normalize_name) == _normalize_name(ward))
        & (df["lganame"].map(_normalize_name) == _normalize_name(lga))
        & (df["statename"].map(_normalize_name) == _normalize_name(state))
    )
    matched = df[mask]
    return pd.DataFrame(
        {
            "lon": matched["longitude"].astype(float).to_numpy(),
            "lat": matched["latitude"].astype(float).to_numpy(),
            "area_m2": (
                matched["area_in_meters"].astype(float).to_numpy() if "area_in_meters" in matched.columns else None
            ),
            "confidence": (matched["confidence"].to_numpy() if "confidence" in matched.columns else None),
            "dataset": "uploaded",
        }
    )


def planning_gap_features(
    ward: str,
    lga: str,
    state: str,
    area_id: str,
    ward_boundary: BaseGeometry,
    existing_wa_boundaries: list[dict],
    cell_size_m: float = 100.0,
    *,
    min_confidence: float | None = None,
    sources: list[str] | None = None,
    min_buildings_per_cell: int = 1,
    visits_per_building: float | None = None,
    buildings: pd.DataFrame | None = None,
) -> tuple[list[dict], list[dict]]:
    """Grid the buildings-minus-existing-footprint remainder for one ward
    into the SAME Feature shape `generate_coverage_frame` produces (`cluster`,
    `area_id`, `ward`/`lga`/`state`, `building_count`, `expected_visit_count`,
    `cell_size_m` — matching `microplans.coverage.frame.generate_coverage_frame`'s
    Feature shape), tagged to share `area_id` with that ward's carry-forward
    features (`core.areas.carry_forward_features`) so
    `microplans.core.plan.recompute_area_visits` pools both into one
    target-spread group for the ward.

    `min_confidence`/`sources` are Phase 2 Step 2's building-source controls,
    passed straight through to `buildings_within_ward`/`fetch_buildings` —
    ignored when `buildings` is given (Step 2's "upload your own" mode
    already has its buildings; see `buildings_from_upload`).
    `min_buildings_per_cell` drops any occupied grid cell with fewer
    buildings than this before it becomes a candidate work area — `grid_clusters`
    itself has no such floor (every occupied cell is a cluster).
    `visits_per_building`, if given, sets each feature's `expected_visit_count`
    to `round(visits_per_building * building_count)` — a DISPLAY estimate for
    Phase 2's tables (this ward's own observed visits-per-building rate,
    since a gap-fill cell has no visit history of its own); the real plan's
    target still comes from `core.areas.ward_children_per_building`,
    unrelated to this estimate. `None` (the default) keeps the original
    building-count placeholder.

    Returns `(features, building_points)` — `building_points` is EVERY
    building within the ward boundary (`{"lon": float, "lat": float}`),
    regardless of whether an existing work area already covers it — the
    map's "uploaded buildings" layer is meant to show the reviewer
    everything that landed inside the ward they're reviewing, not just the
    subset that became new gap-fill cells. Always computed (it's a
    byproduct of `buildings_within_ward`, already in hand) — callers decide
    whether to keep it (Step 2 only keeps this for "upload your own" mode,
    since an Overture-fetched ward can be far larger; see
    `tasks.preview_planning_gaps`).
    """
    from connect_labs.microplans.core import clustering

    within_ward = buildings_within_ward(ward_boundary, buildings, min_confidence=min_confidence, sources=sources)
    building_points = [
        {"lon": float(lon), "lat": float(lat)} for lon, lat in zip(within_ward["lon"], within_ward["lat"])
    ]

    remainder = _exclude_covered(within_ward, existing_wa_boundaries)
    out = clustering.grid_clusters(remainder, cell_size_m=cell_size_m)
    features = []
    for _, row in out.psu_frame.iterrows():
        n_b = int(row["n_buildings"])
        if n_b < min_buildings_per_cell:
            continue
        expected_visit_count = round(visits_per_building * n_b) if visits_per_building is not None else n_b
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [row["cell_polygon"]]},
                "properties": {
                    # "-gap-" namespacing keeps this from ever colliding with
                    # a carry-forward feature's "{area_id}-existing-{wa_id}"
                    # cluster name in the same ward.
                    "cluster": f"{area_id}-gap-{row['cluster']}",
                    "area_id": area_id,
                    "ward": ward,
                    "lga": lga,
                    "state": state,
                    "building_count": n_b,
                    "expected_visit_count": expected_visit_count,
                    "cell_size_m": float(cell_size_m),
                },
            }
        )
    return features, building_points


def work_area_boundaries_for_ward(
    pipeline,
    opportunity_id: int,
    ward: str,
    lga: str,
    state: str,
    *,
    request: HttpRequest | None = None,
) -> list[dict]:
    """Boundary GeoJSON for EVERY existing work area in `ward` (not just
    locked candidates) — joins two things that already exist separately:
    `core.areas.work_area_ids_for_ward` (WA case ids in a ward) against
    `core.geometry.fetch_work_area_geometry`'s output (boundary GeoJSON per
    WA case id). This is exactly what `buildings_not_covered` needs to
    exclude buildings already inside a drawn work area.

    Pass one of `request` or `pipeline` (mirrors every other function in this
    app's `core/` package taking either).
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.mopup.core.areas import work_area_ids_for_ward
    from connect_labs.mopup.core.geometry import fetch_work_area_geometry

    if pipeline is None:
        if request is None:
            raise ValueError("work_area_boundaries_for_ward requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    wa_ids = work_area_ids_for_ward(pipeline, opportunity_id, ward, lga, state)
    if not wa_ids:
        return []

    geometry = fetch_work_area_geometry(opportunity_id, request=request, pipeline=pipeline)
    return [geometry[wa_id]["boundary"] for wa_id in wa_ids if geometry.get(wa_id, {}).get("boundary")]


def ward_visits_per_building(all_rows: list[dict], ward: str) -> float:
    """This ward's own observed (approved HSD visits) ÷ (buildings), across
    every work area in it that has actually recorded at least one approved
    HSD visit — the best available stand-in for a gap-fill cell's expected
    visit count, since a cell that's never been visited has no history of
    its own. Computed per ward (never pooled across wards), so a multi-ward
    mop-up run applies each ward's own rate to its own gap-fill cells.
    Returns 0.0 (not a ZeroDivisionError) when there's no eligible data for
    this ward — a genuine zero is a valid answer here, matching
    `core.areas.ward_children_per_building`'s same convention.

    Deliberately gates on `approved_hsd_count > 0` rather than the work
    area's own `status` property — confirmed live against real program-217
    data that a work area's CommCare HQ case `status` can stay `NOT_VISITED`
    even after real HSD/NCF visit forms were submitted for it (the case
    property and the visit-form record apparently don't always move
    together), which made this return a false 0.0 for a ward where real
    delivery had clearly happened (hundreds of approved visits). Actual
    visit activity is the more reliable signal for "has this WA got a real
    rate to contribute" — `core/indicators.py`'s EVC-shortfall gate was
    changed to the same rule after the same finding recurred there.

    Deliberately NOT `ward_children_per_building` (registered-CHILDREN per
    building, from CommCare HQ case data) — that formula still drives the
    real plan's `area_targets` at hand-off, unchanged. This is a cheaper,
    Phase-2-local estimate (observed VISITS per building, from data Phase 2
    already evaluated) purely for Step 2's preview tables/map, so the
    reviewer isn't staring at an unexplained 0 before locking."""
    eligible = [r for r in all_rows if r.get("ward") == ward and (r.get("approved_hsd_count", 0) or 0) > 0]
    buildings = sum(r.get("building_count", 0) or 0 for r in eligible)
    if not buildings:
        return 0.0
    visits = sum(r.get("approved_hsd_count", 0) or 0 for r in eligible)
    return visits / buildings
