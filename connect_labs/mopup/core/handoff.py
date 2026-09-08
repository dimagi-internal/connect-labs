"""Phase 2 -> Phase 3 hand-off: turn a run's locked candidate set into a
Draft microplans coverage plan, ready for the existing, unmodified microplans
review page. Calls microplans' public functions directly (plain Python
imports) — this app makes zero changes to microplans' own core logic, per
the design brief's §3 architecture decision.

Carries forward each locked candidate's OWN boundary as one WorkArea feature
(no union, no regridding — `core.areas.carry_forward_features`), optionally
adding freshly-gridded planning-gap cells for buildings never covered by any
existing WA in that ward (`core.gaps.planning_gap_features`, gated by
`include_planning_gaps` — no Phase 2 review step for these in this version;
see `core/gaps.py`'s module docstring). An earlier version of this module
unioned every candidate's polygon per ward and re-gridded the union from
scratch via `generate_coverage_frame` — replaced entirely by the
carry-forward redesign; see `core/areas.py`'s module docstring for why.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest

from connect_labs.mopup.core.areas import carry_forward_features, distinct_wards, ward_children_per_building
from connect_labs.mopup.core.gaps import planning_gap_features, work_area_boundaries_for_ward
from connect_labs.mopup.core.models import MopupRunRecord

logger = logging.getLogger(__name__)


class HandoffError(Exception):
    """A locked run couldn't be turned into a plan — see the message for why
    (always meant to be shown to the reviewer, never a stack trace)."""


def create_plan_from_locked_run(
    run: MopupRunRecord,
    program_id: int,
    *,
    request: HttpRequest | None = None,
    grouping: dict | None = None,
    group_id: int | None = None,
    include_planning_gaps: bool = False,
) -> dict:
    """Build a Draft coverage plan from `run.candidate_work_areas` (the
    frozen candidate set from the lock action). Returns
    `{"plan_id", "urls", ..., "skipped_no_geometry": [wa_id, ...],
    "planning_gap_cells_added": N}` — the same shape `serialization.plan_to_json`
    produces, plus hand-off bookkeeping.

    Raises `HandoffError` for anything that should stop the hand-off and be
    shown to the reviewer (no candidates, no opportunity, plan-creation
    failure) rather than a 500.
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.microplans import serialization
    from connect_labs.microplans.core.admin_boundaries import find_ward_boundary_geometry
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
    from connect_labs.microplans.view_helpers import _plan_scoped_urls

    candidates = run.candidate_work_areas
    if not candidates:
        raise HandoffError("This run has no locked candidates to create a plan from.")

    opportunity_id = run.target_opportunity_id
    if not opportunity_id:
        raise HandoffError("This run has no target opportunity.")
    opportunity_ids = [int(opportunity_id)]

    with_geometry = [c for c in candidates if c.get("boundary")]
    skipped = [c["wa_id"] for c in candidates if not c.get("boundary")]
    if not with_geometry:
        raise HandoffError("None of the locked candidates have boundary geometry — cannot build a plan.")

    try:
        cf_features = carry_forward_features(with_geometry)
    except ValueError as e:
        raise HandoffError(f"Invalid candidate work areas: {e}") from e

    wards = distinct_wards(cf_features)
    pipeline = AnalysisPipeline(request=request) if request is not None else None

    gap_features: list[dict] = []
    if include_planning_gaps:
        from shapely.geometry import shape

        for w in wards:
            try:
                existing_boundaries = work_area_boundaries_for_ward(
                    pipeline, opportunity_id, w["ward"], w["lga"], w["state"], request=request
                )
                ward_boundary = find_ward_boundary_geometry(w["state"], w["lga"], w["ward"])
                if ward_boundary is None:
                    logger.info(
                        "mopup handoff: no ward boundary match for planning gaps "
                        "(program=%s run=%s ward=%s) — skipping this ward",
                        program_id,
                        run.id,
                        w["ward"],
                    )
                    continue
                gap_features += planning_gap_features(
                    w["ward"], w["lga"], w["state"], w["area_id"], shape(ward_boundary), existing_boundaries
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "mopup handoff: planning-gap detection failed (program=%s run=%s ward=%s)",
                    program_id,
                    run.id,
                    w["ward"],
                )

    all_features = cf_features + gap_features

    # Buildings actually retained per area_id IN THIS PLAN — carry-forward and
    # gap-fill features in the same ward share one area_id (core/areas.py's
    # _area_id), so this pools both into the ward's single target-spread
    # denominator. See ward_children_per_building's docstring for why the
    # bare rate must be scaled by this before it's a valid area_targets entry.
    retained_buildings_by_area: dict[str, int] = {}
    for feat in all_features:
        props = feat.get("properties", {}) or {}
        aid = props.get("area_id")
        if aid:
            retained_buildings_by_area[aid] = retained_buildings_by_area.get(aid, 0) + int(
                props.get("building_count") or 0
            )

    area_targets: dict[str, float] = {}
    for w in wards:
        try:
            rate = ward_children_per_building(
                w["ward"], w["lga"], w["state"], opportunity_ids, request=request, pipeline=pipeline
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "mopup handoff: ward_children_per_building failed (program=%s run=%s ward=%s)",
                program_id,
                run.id,
                w["ward"],
            )
            continue
        retained = retained_buildings_by_area.get(w["area_id"], 0)
        area_targets[w["area_id"]] = rate * retained
        w["populations"] = {"hsd_children_per_building": rate}

    states = {w["state"] for w in wards if w.get("state")}
    region = run.name or "CHC Mop-up"
    lga = ", ".join(sorted({w["lga"] for w in wards if w.get("lga")})) or region
    state = next(iter(states)) if len(states) == 1 else ""

    empty_fc = {"type": "FeatureCollection", "features": []}
    merged_fc = {"type": "FeatureCollection", "features": all_features}
    da = ProgramPlanDataAccess(program_id, request=request)
    try:
        plan = da.create_plan(
            region=region,
            name=region,
            mode="coverage",
            pins=empty_fc,
            hulls=merged_fc,
            input_areas=wards,
            grouping=grouping or {},
            lga=lga,
            state=state,
            area_targets=area_targets,
            run_meta={
                "source": "chc_mopup",
                "opportunity_ids": opportunity_ids,
                "mopup_run_id": run.id,
                "include_planning_gaps": include_planning_gaps,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("mopup handoff: create_plan failed (program=%s run=%s)", program_id, run.id)
        raise HandoffError(f"Could not create the plan: {e}") from e

    group_warning = None
    if group_id is not None:
        try:
            da.add_plan_to_group(int(group_id), plan.id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "mopup handoff: add to group failed (program=%s group=%s plan=%s)", program_id, group_id, plan.id
            )
            group_warning = "added plan but not to group"

    resp = serialization.plan_to_json(plan)
    # plan_to_json's own "status" is the PLAN's lifecycle (draft/approved/…) —
    # rename before the view merges in its JSON-envelope "status" (ok/error),
    # same convention MopupRunDataAccess uses (run_status vs. envelope status).
    resp["plan_status"] = resp.pop("status", None)
    resp["plan_id"] = plan.id
    resp["urls"] = _plan_scoped_urls(program_id, plan.id)
    resp["skipped_no_geometry"] = skipped
    resp["planning_gap_cells_added"] = len(gap_features)
    if group_warning:
        resp["group_warning"] = group_warning
    return resp
