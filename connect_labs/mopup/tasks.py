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
    include_planning_gaps: bool = False,
) -> dict:
    """Phase 3 hand-off, offloaded the same way as `fetch_evaluation_data` —
    confirmed this session that a real ward's `include_planning_gaps=True`
    hand-off (fetching + diffing thousands of Overture buildings) can take
    well over a minute synchronously, and mop-up is expected to run against
    several wards' candidates at once, which only grows that number. See
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

    # Only the planning-gap branch's existing-WA lookup
    # (core/gaps.py:work_area_boundaries_for_ward -> core/areas.py:
    # work_area_ids_for_ward) is cchq_cases-sourced — a carry-forward-only
    # hand-off never touches CommCare HQ, so don't demand a token it doesn't
    # need.
    cchq_access_token = None
    if include_planning_gaps:
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
        raise RuntimeError("Lock the run before creating a plan.")

    pipeline = AnalysisPipeline(access_token=access_token, cchq_access_token=cchq_access_token)

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
            include_planning_gaps=include_planning_gaps,
            on_stage=on_stage,
        )
    except HandoffError as e:
        result = {"status": "error", "detail": str(e)}
        set_task_progress(self, "Failed", is_complete=True, result=result)
        return result

    resp["status"] = "ok"
    set_task_progress(self, "Done", is_complete=True, result=resp)
    return resp
