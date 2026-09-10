"""Celery tasks for the CHC mop-up feature.

The one expensive job: pulling a whole opportunity's work-area case data,
approved visit-form data, and work-area geometry, then assembling it into
the per-WA row list `core.indicators.evaluate_run` evaluates against.
Offloaded to Celery because a synchronous web request doing this for a real
production opportunity (tens of thousands of visits) reliably hits the
gateway timeout — confirmed directly against program 217 this session.

Every threshold/granularity tweak on the analysis screen re-runs only
`evaluate_run()` over this task's already-fetched result (pure Python, no
network calls) — it does NOT re-dispatch this task. See
`connect_labs.mopup.views.MopupCandidatesView` for how a run's cached task
result is read back.
"""

from __future__ import annotations

import logging

from config import celery_app
from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError, get_valid_cchq_access_token
from connect_labs.utils.celery import set_task_progress

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def fetch_evaluation_data(self, program_id: int, run_id: int, user_id: int) -> dict:
    """Fetch + assemble one run's evaluation rows; the task's return value
    (`{"rows": [...]}`) IS the durable result — read back via
    `AsyncResult(task_id).result`, Celery's own result backend, same
    convention `connect_labs.workflow`/`connect_labs.audit` already use for
    poll-first progress (see `connect_labs/utils/celery.py`,
    `connect_labs/labs/analysis/sse_streaming.py:build_task_progress`).

    Both Connect and CommCare HQ tokens are resolved fresh from the user's
    persisted tokens (silently refreshed if expired) — a Celery task has no
    HTTP session to read a cached one from. Either token being unusable
    (dead refresh token, never authorized) fails the task with a message
    the UI shows verbatim; re-authorizing is the user's own action
    (`/labs/commcare/initiate/` for CCHQ), not something this task can do.
    """
    from django.contrib.auth import get_user_model

    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.mopup.core.candidates import build_evaluation_input
    from connect_labs.mopup.core.data_access import MopupRunDataAccess

    set_task_progress(self, "Starting…")

    user = get_user_model().objects.get(pk=user_id)

    try:
        access_token = get_valid_access_token(user)
    except ConnectTokenError as e:
        raise RuntimeError(f"Connect authorization needed: {e}") from e

    try:
        cchq_access_token = get_valid_cchq_access_token(user)
    except CCHQTokenError as e:
        # Hard-fail, unlike run_scheduled_workflow's best-effort CCHQ token:
        # this task's own work-area pull is a cchq_cases source and cannot
        # produce a usable result without it.
        raise RuntimeError(
            f"CommCare HQ authorization needed — visit /labs/commcare/initiate/ to reconnect: {e}"
        ) from e

    da = MopupRunDataAccess(program_id, access_token=access_token)
    run = da.get_run(run_id)
    if run is None:
        raise RuntimeError(f"Mop-up run {run_id} not found.")

    pipeline = AnalysisPipeline(access_token=access_token, cchq_access_token=cchq_access_token)

    def on_stage(label: str) -> None:
        set_task_progress(self, label)

    rows = build_evaluation_input(
        run.target_opportunity_id,
        run.selected_wards,
        pipeline=pipeline,
        on_stage=on_stage,
    )

    result = {"rows": rows}
    # `result=` passed explicitly to set_task_progress (not left to the bare
    # return value) — matches the established convention (e.g.
    # connect_labs/audit/tasks.py's audit-creation task) that
    # build_task_progress relies on to find the payload at meta["result"].
    set_task_progress(self, "Done", is_complete=True, result=result)
    return result


@celery_app.task(bind=True)
def create_mopup_plan(
    self,
    program_id: int,
    run_id: int,
    user_id: int,
    *,
    grouping: dict | None = None,
    group_id: int | None = None,
) -> dict:
    """Phase 3 hand-off, offloaded the same way as `fetch_evaluation_data` —
    kept async even though planning-gap computation moved to Phase 2's Step 2
    (`preview_planning_gaps`, so this hand-off is typically fast now): mop-up
    is expected to run against several wards' locked candidates at once,
    which is still worth guarding against a gateway timeout. See
    `connect_labs.mopup.views.MopupCreatePlanView`/
    `_create_plan_result_or_progress` for how this is dispatched/polled.

    Returns the SAME dict shape `create_plan_from_locked_run` always
    returned to the view — a caught `HandoffError` becomes a normal
    (non-exceptional) task result shaped like the view's old 400 response
    (`{"status": "error", "detail": ...}`), so a validation failure (e.g. "no
    locked candidates") still reads as a clean, non-alarming message rather
    than a Celery task failure. Only a genuinely unexpected error (a token
    problem, a missing run, an unhandled exception inside the hand-off)
    raises and becomes a real Celery FAILURE state.
    """
    from django.contrib.auth import get_user_model

    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.mopup.core.data_access import MopupRunDataAccess
    from connect_labs.mopup.core.handoff import HandoffError, create_plan_from_locked_run
    from connect_labs.mopup.core.models import STATUS_LOCKED

    set_task_progress(self, "Starting…")

    user = get_user_model().objects.get(pk=user_id)

    try:
        access_token = get_valid_access_token(user)
    except ConnectTokenError as e:
        raise RuntimeError(f"Connect authorization needed: {e}") from e

    da = MopupRunDataAccess(program_id, access_token=access_token)
    run = da.get_run(run_id)
    if run is None:
        raise RuntimeError(f"Mop-up run {run_id} not found.")
    if run.status != STATUS_LOCKED:
        raise RuntimeError("Lock the run before creating a plan.")

    # Carry-forward + ward_children_per_building's target-rate lookups are
    # Connect-export-sourced only — this hand-off never needs a CommCare HQ
    # token (that's only true for the planning-gap ward lookups, which moved
    # to Step 2's own preview_planning_gaps task).
    pipeline = AnalysisPipeline(access_token=access_token, cchq_access_token=None)

    def on_stage(label: str) -> None:
        set_task_progress(self, label)

    try:
        resp = create_plan_from_locked_run(
            run,
            program_id,
            pipeline=pipeline,
            access_token=access_token,
            grouping=grouping,
            group_id=group_id,
            on_stage=on_stage,
        )
    except HandoffError as e:
        result = {"status": "error", "detail": str(e)}
        set_task_progress(self, "Failed", is_complete=True, result=result)
        return result

    resp["status"] = "ok"
    set_task_progress(self, "Done", is_complete=True, result=resp)
    return resp


@celery_app.task(bind=True)
def preview_planning_gaps(
    self,
    program_id: int,
    run_id: int,
    user_id: int,
    *,
    mode: str = "overture",
    building_sources: list[str] | None = None,
    min_confidence: float | None = None,
    min_buildings_per_cell: int = 1,
    cell_size_m: float = 100.0,
) -> dict:
    """Phase 2 Step 2 (locked runs only): for every distinct ward among the
    run's locked candidates, find buildings never covered by any existing
    work area and grid them into gap-fill candidate work areas — the real,
    potentially slow building fetch (confirmed this session: well over a
    minute against real ward data, for the Overture mode) that used to
    happen silently at Phase 3 hand-off time. Offloaded and polled the same
    way as `fetch_evaluation_data`/`create_mopup_plan`.

    `mode` picks where the buildings come from:
      * `"skip"` — no new work areas; returns immediately with empty
        features, right after the run/lock checks below (before any of the
        per-ward work, which skip mode has no use for) — but still behind
        the same Connect/CommCare HQ token resolution every mode goes
        through, so an auth problem surfaces identically regardless of mode.
      * `"overture"` (default) — today's behavior: `building_sources`/
        `min_confidence` control `microplans.core.footprints.fetch_buildings`.
      * `"upload"` — reads `run.uploaded_buildings_csv` (already shrunk to
        this run's own ward(s) at upload time by
        `MopupUploadBuildingsView`/`core.gaps.filter_upload_to_wards`) ONCE
        for the whole run, then `core.gaps.buildings_from_upload` filters it
        down to each ward's own rows. `building_sources`/`min_confidence`
        are ignored in this mode (no such concept for user-supplied data).
        Unlike Overture, this mode also keeps every individual building
        position (`result["building_points"]`) for Step 2's map to plot —
        an Overture-fetched remainder can be far larger, so that's skipped
        for the other modes rather than bloating the run record.

    Does NOT persist its own result — `MopupPlanningGapsView` stores the
    returned features/config/warnings onto the run once this returns, so a
    caller can inspect the response before committing to it if it ever
    needs to (today it always stores it).

    Building-fetch caching (Overture mode only) is inherited for free from
    `microplans.core.footprints.fetch_buildings`'s own `FootprintArea`/
    `FootprintBuilding` Postgres cache (7-day TTL) — `core.gaps.buildings_not_covered`
    already calls that function directly, so a second Recompute with the
    same ward boundary is fast without any mop-up-side cache of its own.

    Re-fetches this run's evaluation rows (`core.candidates.build_evaluation_input`)
    for the ward-level visits-per-building EVC estimate (`core.gaps.ward_visits_per_building`)
    rather than relying on the original Phase 2 fetch's Celery result still
    being around — that data pull is itself cached at the SQL layer
    (`AnalysisPipeline`'s own RawVisitCache/ComputedVisitCache), so this is a
    fast cache hit in practice, not a second slow pull.
    """
    from django.contrib.auth import get_user_model

    from connect_labs.mopup.core.data_access import MopupRunDataAccess
    from connect_labs.mopup.core.models import STATUS_LOCKED

    config = {
        "mode": mode,
        "building_sources": building_sources,
        "min_confidence": min_confidence,
        "min_buildings_per_cell": min_buildings_per_cell,
        "cell_size_m": cell_size_m,
    }

    set_task_progress(self, "Starting…")

    user = get_user_model().objects.get(pk=user_id)

    try:
        access_token = get_valid_access_token(user)
    except ConnectTokenError as e:
        raise RuntimeError(f"Connect authorization needed: {e}") from e

    # work_area_ids_for_ward (via work_area_boundaries_for_ward) is
    # cchq_cases-sourced -- every mode resolves this upfront (even "skip",
    # which doesn't end up needing it) so a broken CCHQ session always
    # surfaces the same way regardless of mode, matching every other check
    # in this task running before the mode-specific branches below.
    try:
        cchq_access_token = get_valid_cchq_access_token(user)
    except CCHQTokenError as e:
        raise RuntimeError(
            f"CommCare HQ authorization needed — visit /labs/commcare/initiate/ to reconnect: {e}"
        ) from e

    da = MopupRunDataAccess(program_id, access_token=access_token)
    run = da.get_run(run_id)
    if run is None:
        raise RuntimeError(f"Mop-up run {run_id} not found.")
    if run.status != STATUS_LOCKED:
        raise RuntimeError("Lock the run before previewing planning gaps.")

    if mode == "skip":
        result = {"status": "ok", "features": [], "cells_added": 0, "warnings": {}, "config": config}
        set_task_progress(self, "Done", is_complete=True, result=result)
        return result

    from shapely.geometry import shape

    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.mopup.core.areas import carry_forward_features, distinct_wards
    from connect_labs.mopup.core.candidates import build_evaluation_input
    from connect_labs.mopup.core.gaps import (
        buildings_from_upload,
        planning_gap_features,
        ward_visits_per_building,
        work_area_boundaries_for_ward,
    )

    candidates = run.candidate_work_areas
    with_geometry = [c for c in candidates if c.get("boundary")]
    if not with_geometry:
        raise RuntimeError("None of the locked candidates have boundary geometry.")
    wards = distinct_wards(carry_forward_features(with_geometry))

    pipeline = AnalysisPipeline(access_token=access_token, cchq_access_token=cchq_access_token)

    set_task_progress(self, "Fetching this run's visit history for the EVC estimate…")
    all_rows = build_evaluation_input(run.target_opportunity_id, run.selected_wards, pipeline=pipeline)

    uploaded_df = None
    if mode == "upload":
        if not run.uploaded_buildings_csv:
            raise RuntimeError("Upload a building-data file before recomputing.")
        import io

        import pandas as pd

        set_task_progress(self, "Reading the uploaded building file…")
        uploaded_df = pd.read_csv(io.StringIO(run.uploaded_buildings_csv))

    gap_features: list[dict] = []
    building_points: list[dict] = []
    warnings: dict[str, str] = {}
    for i, w in enumerate(wards, start=1):
        set_task_progress(self, f"Checking planning gaps for {w['ward']} ({i}/{len(wards)})…")
        try:
            from connect_labs.microplans.core.admin_boundaries import find_ward_boundary_geometry

            existing_boundaries = work_area_boundaries_for_ward(
                pipeline, run.target_opportunity_id, w["ward"], w["lga"], w["state"]
            )
            ward_boundary = find_ward_boundary_geometry(w["state"], w["lga"], w["ward"])
            if ward_boundary is None:
                warnings[w["ward"]] = "no ward boundary match — skipped"
                continue
            rate = ward_visits_per_building(all_rows, w["ward"])
            ward_buildings = (
                buildings_from_upload(uploaded_df, w["ward"], w["lga"], w["state"]) if mode == "upload" else None
            )
            features, points = planning_gap_features(
                w["ward"],
                w["lga"],
                w["state"],
                w["area_id"],
                shape(ward_boundary),
                existing_boundaries,
                cell_size_m=cell_size_m,
                min_confidence=min_confidence,
                sources=building_sources,
                min_buildings_per_cell=min_buildings_per_cell,
                visits_per_building=rate,
                buildings=ward_buildings,
            )
            gap_features += features
            if mode == "upload":
                building_points += points
        except Exception as e:  # noqa: BLE001
            warnings[w["ward"]] = str(e)
            logger.exception(
                "mopup planning-gap preview: failed (program=%s run=%s ward=%s)", program_id, run_id, w["ward"]
            )

    result = {
        "status": "ok",
        "features": gap_features,
        "cells_added": len(gap_features),
        "building_points": building_points,
        "warnings": warnings,
        "config": config,
    }
    set_task_progress(self, "Done", is_complete=True, result=result)
    return result
