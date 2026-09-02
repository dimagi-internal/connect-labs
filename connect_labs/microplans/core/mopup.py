"""CHC mop-up microplanning — the seam between the (Phase 2) candidate-analysis
workflow dashboard and the existing microplans coverage engine.

Two functions:

  build_mopup_areas(candidate_work_areas)
      Turns a flat list of candidate work-area dicts (one per locked WA the
      reviewer picked — ward/lga/state + a GeoJSON boundary each, the shape
      Phase 2's render code has already loaded client-side from pipeline 12971
      "CHC Work Area Geometry") into one unioned polygon per ward, in the exact
      `area_input`-shaped dict `core/frame.py`'s `generate_coverage_frame` (via
      `core/area_input.py:resolve_area`) already knows how to consume. This is
      the ONLY geometry-side change this feature needed — everything downstream
      (grid generation, `MAX_WORK_AREAS`/`MAX_AREA_KM2` guards, cell filters,
      `materialize_work_areas`, exclude/unexclude, grouping, CSV export)
      operates on plain geometry/properties and needs no changes at all.

  ward_children_per_building(ward, lga, state, opportunity_ids, *, request)
      The EVC target RATE for a mop-up ward: ward-wide (every work area in the
      ward across the given opportunities, not just the locked candidates)
      HSD-registered-children count, divided by the ward's full-boundary
      Overture building count. `core/plan.py:recompute_area_visits()` already
      computes `EVC(wa) = ceil(wa_buildings * target / retained_buildings)`
      per area_id (`target = area_targets[area_id]`, `retained_buildings` =
      THIS PLAN's own gridded building count for that area_id) — so to get
      `EVC(wa) = ceil(wa_buildings * this_rate)` (the plan's requested "avg
      children per building for the ward x building count of the new WA")
      through that existing, unmodified formula, the caller must pass
      `area_targets[area_id] = this_rate * retained_buildings_for_that_area_id`
      — NOT the bare rate. A mop-up plan's grid covers only the candidate
      footprint (a subset of the ward), so its own retained_buildings is
      smaller than the ward's true total; feeding the rate straight through
      would silently double-divide by buildings and floor every EVC near
      zero. See `ProgramCreateMopupPlanView.post` (the only caller that feeds
      this into `area_targets`) for where that multiplication happens — this
      function itself only returns the RATE.

Both are pure/orchestration functions: `build_mopup_areas` touches no network
or DB (just shapely), `ward_children_per_building` needs a live Connect (and,
for the work-area lookup, CommCare HQ) OAuth token to query real opportunity
data — see its own docstring for why it takes a `request`/`AnalysisPipeline`
even though the approved plan's signature didn't originally include one.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from django.http import HttpRequest
from django.utils.text import slugify
from shapely.geometry import mapping
from shapely.ops import unary_union

from connect_labs.microplans.core.area_input import resolve_area
from connect_labs.microplans.core.footprints import fetch_buildings

logger = logging.getLogger(__name__)


def _ward_key(wa: dict) -> tuple[str, str, str]:
    return (
        str(wa.get("state") or "").strip(),
        str(wa.get("lga") or "").strip(),
        str(wa.get("ward") or "").strip(),
    )


def build_mopup_areas(candidate_work_areas: list[dict]) -> list[dict]:
    """Group candidate work areas by (state, lga, ward), union each ward's
    polygons into one shape, and emit one `area_input`-shaped dict per ward.

    ``candidate_work_areas``: dicts with at least ``ward``/``lga``/``state``
    and a ``geometry`` GeoJSON field (the shape pipeline 12971 "CHC Work Area
    Geometry" rows have client-side: ``ward``/``geometry`` directly on the row,
    ``lga``/``state`` joined in from the ``work_areas`` (12965) pipeline by
    ``wa_case_id`` — Phase 2's render code owns that join; by the time a
    candidate dict reaches here it must already carry all four keys). Each
    dict is parsed via ``resolve_area`` (the same GeoJSON/circle validation
    every other area-input path already uses) before unioning, so a malformed
    boundary raises the same ``ValueError`` a hand-drawn area would.

    Raises ``ValueError`` if a candidate is missing a ward name (nothing to
    group it under) or has an unparseable geometry.

    Returns one dict per distinct ward:
        {"geometry": <GeoJSON>, "ward": ..., "lga": ..., "state": ...,
         "area_id": "mopup-<state-lga-ward slug>"}
    """
    by_ward: dict[tuple[str, str, str], list] = defaultdict(list)
    for i, wa in enumerate(candidate_work_areas):
        state, lga, ward = _ward_key(wa)
        if not ward:
            raise ValueError(f"candidate work area at index {i} has no ward — cannot attribute it to a mop-up area")
        geom = resolve_area(wa)
        by_ward[(state, lga, ward)].append(geom)

    areas: list[dict] = []
    for (state, lga, ward), geoms in by_ward.items():
        union_geom = unary_union(geoms) if len(geoms) > 1 else geoms[0]
        # Slug on the full (state, lga, ward) triple, not the ward name alone —
        # two same-named wards in different LGAs must never collide onto the
        # same area_id (see core/frame.py:_area_meta's identical concern for
        # why area_id, not the ward name, is the thing that must be unique).
        slug = slugify(f"{state}-{lga}-{ward}") or f"ward-{len(areas) + 1}"
        areas.append(
            {
                "geometry": mapping(union_geom),
                "ward": ward,
                "lga": lga,
                "state": state,
                "area_id": f"mopup-{slug}",
            }
        )
    return areas


# ---------------------------------------------------------------------------
# ward_children_per_building
# ---------------------------------------------------------------------------

# The Health Service Delivery form's own display name — same constant value as
# connect_labs/workflow/flw_audit_compute.py:FORM_NAME and the explicit
# form.@name check pipeline 12968 "CHC Approved Visits" uses. Duplicated here
# (not imported) because flw_audit_compute.py lives in the `workflow` app and
# this is the `microplans` app's own boundary — keeping the two apps
# independently importable. Keep it in sync if the form is ever renamed.
HSD_FORM_NAME = "Health Service Delivery"

# Same coalesce order pipeline 12968 and the Phase-1 chc_mopup_visit_quality
# pipeline use: HSD visits carry it at form.work_area_info.wa_caseid; the No
# Children Found form stores it separately at the top-level form.wa_case_id.
_WA_CASE_ID_PATHS = ["form.work_area_info.wa_caseid", "form.wa_case_id"]
_CHILD_CASE_ID_PATH = "form.case.@case_id"
_FORM_NAME_PATH = "form.@name"


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _work_area_ids_for_ward(pipeline, opportunity_id: int, ward: str, lga: str, state: str) -> set[str]:
    """Every work-area case id in `ward` (matched on ward+lga+state, exact
    normalized match — these come from the same CommCare case data as the
    candidate work areas, not free-typed text, so the fuzzy admin-boundary
    name matching in core/admin_boundaries.py doesn't apply here) for one
    opportunity. Mirrors pipeline 12965 "CHC Work Areas" (cchq_cases,
    case_type=work-area) field-for-field.
    """
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="cchq_cases", case_type="work-area"),
        grouping_key="entity_id",
        terminal_stage="visit_level",
        fields=[
            FieldComputation(name="ward", path="case.properties.ward", aggregation="first"),
            FieldComputation(name="lga", path="case.properties.lga", aggregation="first"),
            FieldComputation(name="state", path="case.properties.state", aggregation="first"),
        ],
    )
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
    n_ward, n_lga, n_state = _norm(ward), _norm(lga), _norm(state)
    return {
        row.entity_id
        for row in result.rows
        if _norm(row.computed.get("ward")) == n_ward
        and _norm(row.computed.get("lga")) == n_lga
        and _norm(row.computed.get("state")) == n_state
        and row.entity_id
    }


def _hsd_registered_children_count(pipeline, opportunity_id: int, wa_ids: set[str]) -> int:
    """Distinct children (by child_case_id) with an approved Health Service
    Delivery visit at one of `wa_ids`, for one opportunity.

    Deliberately a DISTINCT-CHILD headcount, not a visit count: this feeds a
    population-style denominator (children per building, spread across new
    work areas as an expected-visit TARGET), where a child re-measured on a
    second visit this round must count once — unlike the Phase-1
    chc_mopup_visit_quality pipeline's numerator/denominator counts, which are
    deliberately VISIT counts (every visit is a legitimate data point for a
    data-quality RATE). Same HSD-form-only + approved-only guard as pipeline
    12968 / chc_mopup_visit_quality: status=approved is a schema-level filter,
    form.@name=="Health Service Delivery" is checked in Python below (visit-
    level pipelines don't filter form name server-side either — see
    chc_mopup_candidates.py's module docstring for why 12968 itself only
    exposes form_name as a field rather than a schema filter).
    """
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation

    config = AnalysisPipelineConfig(
        data_source=DataSourceConfig(type="connect_csv"),
        grouping_key="entity_id",
        terminal_stage="visit_level",
        filters={"status": ["approved"]},
        fields=[
            FieldComputation(name="form_name", path=_FORM_NAME_PATH, aggregation="first"),
            FieldComputation(name="wa_case_id", paths=_WA_CASE_ID_PATHS, aggregation="first"),
            FieldComputation(name="child_case_id", path=_CHILD_CASE_ID_PATH, aggregation="first"),
        ],
    )
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
    children: set[str] = set()
    for row in result.rows:
        c = row.computed
        if c.get("form_name") != HSD_FORM_NAME:
            continue
        if c.get("wa_case_id") not in wa_ids:
            continue
        if c.get("child_case_id"):
            children.add(c["child_case_id"])
    return len(children)


def ward_children_per_building(
    ward: str,
    lga: str,
    state: str,
    opportunity_ids: list[int],
    *,
    request: HttpRequest | None = None,
    pipeline=None,
) -> float:
    """Ward-wide HSD-registered-children count / ward-wide Overture building
    count, for spreading a mop-up ward's EVC target across its new work areas.

    "Ward-wide" means EVERY work area in the ward across `opportunity_ids` —
    not just the locked mop-up candidates — matching the plan's explicit ask
    ("total across the whole ward, not just candidate WAs"): a ward's true
    child-per-building density shouldn't be biased by which specific work
    areas happened to fail their first round.

    Deviation from the approved plan's literal signature: this needs a
    `request` (or a pre-built `pipeline`) to authenticate the underlying
    Connect + CommCare HQ API calls — `connect_labs.labs.analysis.AnalysisPipeline`
    has no headless/tokenless mode, and the plan's `ward_children_per_building(
    ward, lga, state, opportunity_ids)` signature didn't specify how a token
    would reach it. Pass one of `request` (the Django view's request, read from
    its session same as every other labs_oauth-backed call in this codebase)
    or `pipeline` (an already-constructed `AnalysisPipeline`, e.g. for reuse
    across many wards in one request, or for tests). Raises ValueError if
    neither is given.

    Building-count resolution: the ward's FULL boundary polygon is looked up
    by name via `core.admin_boundaries.find_ward_boundary_geometry` — the same
    (state, lga, ward)-by-name matching microplans' own ward search/upload
    path already uses (see that function's docstring for the tiered,
    ambiguity-safe matching rules) — then fetched via
    `core.footprints.fetch_buildings`, the same footprint fetch (and its PG
    cache) every other coverage/sampling area uses. Returns 0.0 (not a
    ZeroDivisionError, and not None — the plan calls for "a plain float going
    into area_targets") when the boundary can't be resolved or has no
    buildings.

    NOT validated against live opportunity data in the session that wrote
    this function: the cchq_cases work-area lookup requires a live CommCare HQ
    OAuth token bound to an authenticated Labs web session, which the
    validation tooling available at the time had no access to (same blocker
    hit validating Phase 1's delivered_visit_count cross-check — see that
    phase's notes). Smoke-test this against a real ward before relying on it.
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.microplans.core.admin_boundaries import find_ward_boundary_geometry

    if pipeline is None:
        if request is None:
            raise ValueError("ward_children_per_building requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    total_children = 0
    for opp_id in opportunity_ids:
        wa_ids = _work_area_ids_for_ward(pipeline, opp_id, ward, lga, state)
        if not wa_ids:
            continue
        total_children += _hsd_registered_children_count(pipeline, opp_id, wa_ids)

    geometry = find_ward_boundary_geometry(state, lga, ward)
    if geometry is None:
        logger.warning("ward_children_per_building: no boundary match for %s/%s/%s", state, lga, ward)
        return 0.0

    from shapely.geometry import shape

    buildings = fetch_buildings(shape(geometry))
    building_count = len(buildings)
    if building_count == 0:
        return 0.0
    return total_children / building_count
