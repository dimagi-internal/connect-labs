"""Phase 2 -> Phase 3 hand-off: turn a run's locked candidate set into a
Draft microplans coverage plan, ready for the existing, unmodified microplans
review page. Calls microplans' public functions directly (plain Python
imports) — this app makes zero changes to microplans' own files, per the
design brief's §3 architecture decision.

Mirrors the logic an earlier (abandoned) attempt's `ProgramCreateMopupPlanView`
had — that attempt's Workflow-shell dashboard didn't work for the reviewer,
but this specific hand-off logic (geometry union per ward, EVC-target-rate
scaling) is sound and worth carrying forward, adapted to read from a locked
run record instead of a raw POST payload.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest

from connect_labs.mopup.core.areas import build_mopup_areas, ward_children_per_building
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
) -> dict:
    """Build a Draft coverage plan from `run.candidate_work_areas` (the
    frozen candidate set from the lock action). Returns
    `{"plan_id", "urls", ..., "skipped_no_geometry": [wa_id, ...]}` — the
    same shape `serialization.plan_to_json` produces, plus `urls` and a list
    of any candidates that couldn't be included (no boundary geometry).

    Raises `HandoffError` for anything that should stop the hand-off and be
    shown to the reviewer (no candidates, no opportunity, grid/plan-creation
    failure) rather than a 500.
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.microplans import serialization
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
    from connect_labs.microplans.coverage.frame import CoverageConfig, generate_coverage_frame
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

    candidate_work_areas = [{**c, "geometry": c["boundary"]} for c in with_geometry]

    try:
        areas = build_mopup_areas(candidate_work_areas)
    except ValueError as e:
        raise HandoffError(f"Invalid candidate work areas: {e}") from e

    try:
        config = CoverageConfig.from_payload({})
    except (TypeError, ValueError) as e:
        raise HandoffError(f"Invalid coverage config: {e}") from e

    try:
        frame_result = generate_coverage_frame(areas, config)
    except ValueError as e:
        raise HandoffError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("mopup handoff: grid generation failed (program=%s run=%s)", program_id, run.id)
        raise HandoffError("Grid generation failed.") from e

    # Buildings actually gridded per area_id IN THIS PLAN (the mop-up grid
    # only covers the candidate footprint, a SUBSET of the ward — not the
    # ward's full building count). See core/areas.py's ward_children_per_building
    # docstring for why this matters (double-division gotcha).
    retained_buildings_by_area: dict[str, int] = {}
    for feat in frame_result.areas_geojson.get("features", []):
        props = feat.get("properties", {}) or {}
        aid = props.get("area_id")
        if aid:
            retained_buildings_by_area[aid] = retained_buildings_by_area.get(aid, 0) + int(
                props.get("building_count") or 0
            )

    pipeline = AnalysisPipeline(request=request) if request is not None else None
    area_targets: dict[str, float] = {}
    for a in areas:
        try:
            rate = ward_children_per_building(
                a["ward"], a["lga"], a["state"], opportunity_ids, request=request, pipeline=pipeline
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "mopup handoff: ward_children_per_building failed (program=%s run=%s ward=%s)",
                program_id,
                run.id,
                a["ward"],
            )
            continue
        retained = retained_buildings_by_area.get(a["area_id"], 0)
        area_targets[a["area_id"]] = rate * retained
        a["populations"] = {"hsd_children_per_building": rate}

    states = {a["state"] for a in areas if a.get("state")}
    region = run.name or "CHC Mop-up"
    lga = ", ".join(sorted({a["lga"] for a in areas if a.get("lga")})) or region
    state = next(iter(states)) if len(states) == 1 else ""

    empty_fc = {"type": "FeatureCollection", "features": []}
    da = ProgramPlanDataAccess(program_id, request=request)
    try:
        plan = da.create_plan(
            region=region,
            name=region,
            mode="coverage",
            pins=empty_fc,
            hulls=frame_result.areas_geojson,
            input_areas=areas,
            grouping=grouping or {},
            lga=lga,
            state=state,
            area_targets=area_targets,
            coverage_config=config.__dict__,
            coverage_stats=frame_result.stats,
            run_meta={"source": "chc_mopup", "opportunity_ids": opportunity_ids, "mopup_run_id": run.id},
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
    if group_warning:
        resp["group_warning"] = group_warning
    return resp
