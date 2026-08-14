"""Audit-batch generation seam.

Shared helpers for the ``weekly_dual_track_audit`` and ``program_audit_creator``
default-run hooks:
- ``resolve_window`` maps a preset (``last_week`` …) to inclusive ISO dates,
  mirroring the render's ``calculateDateRange`` so the UI and the no-UI
  default-run path agree on what "last week" means. ``yesterday`` has no
  render-side equivalent — it exists only for ``window_preset_for_cadence``,
  since the manual UI has no daily-cadence concept to derive it from.
- ``window_preset_for_cadence`` maps a ``WorkflowSchedule`` cadence (see
  ``connect_labs.workflow.schedules``) to the preset a scheduled default-run
  should resolve when it wasn't given an explicit ``window`` — daily/weekdays
  get a single rolling ``yesterday`` day instead of the fixed weekly bucket,
  so a schedule firing every day doesn't re-audit the same week repeatedly.
- ``sample_overrides_for`` / ``clustering_overrides_for`` extract a creator
  definition's pinned sampling rates and visit-clustering settings, shared by
  both the single-opp default-run and the program creator's per-opp fan-out
  so a definition behaves identically whichever path fires it.
- ``run_this_week_batch`` creates ONE fresh audit-batch run for a single
  ``weekly_dual_track_audit`` definition's opportunity and fires the batch job
  synchronously.

Global constraints honoured here:
- **Opp-scoping:** every read/write goes through a `WorkflowDataAccess` scoped
  to the definition's single owning opportunity — never one unscoped client.
  (Root cause of PRs #777/#779/#783.)
- **Fire = execute, no reuse:** every call creates a new run and fires a new
  batch. There is deliberately no "does a run already exist for this window?"
  lookup — firing is an explicit execution. The program creator gates a program
  run to a single fire and offers per-opp re-run for recovery, so single-fire is
  enforced by the caller, not by dedup here.

The heavy lifting (building the per-track audit calls, creating sessions) lives
in the registered ``weekly_dual_track_audit_create`` job handler; here we only
create the run and fire that job synchronously. Program-wide fan-out is the
program creator's ``fan_out_generate``, which calls ``run_default_for_definition``
once per per-opp creator instance.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from connect_labs.workflow import schedules
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.program_view import program_id_of
from connect_labs.workflow.tasks import run_workflow_job

logger = logging.getLogger(__name__)

JOB_TYPE = "weekly_dual_track_audit_create"


def resolve_window(preset: str, today: date) -> tuple[str, str]:
    """Resolve a window preset to ``(start_iso, end_iso)`` inclusive dates.

    Mirrors the render's ``calculateDateRange`` (weekly_dual_track_audit.py) so
    the cron/API path and the UI agree on what "last week" means. ``today``'s
    JS ``getDay()`` (Sun=0) is reproduced via ``isoweekday() % 7``.
    """
    dow = today.isoweekday() % 7  # JS getDay(): Sunday == 0

    if preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset == "last_week":
        this_sun = today - timedelta(days=dow)
        end = this_sun - timedelta(days=1)
        start = this_sun - timedelta(days=7)
    elif preset == "last_7_days":
        end = today - timedelta(days=1)
        start = end - timedelta(days=6)
    elif preset == "last_14_days":
        end = today - timedelta(days=1)
        start = end - timedelta(days=13)
    elif preset == "last_30_days":
        end = today - timedelta(days=1)
        start = end - timedelta(days=29)
    elif preset == "last_month":
        start = today.replace(day=1) - timedelta(days=1)
        start = start.replace(day=1)
        end = today.replace(day=1) - timedelta(days=1)
    else:
        raise ValueError(f"unknown window preset: {preset!r}")

    return start.isoformat(), end.isoformat()


# Cadence -> window preset for scheduled default-runs that don't get an explicit
# `window`. Keyed on connect_labs.workflow.schedules' plain cadence constants
# (not connect_labs.labs.models.WorkflowSchedule) to avoid a Django-model import
# in a module that templates without a schedule also use.
_WINDOW_PRESET_BY_CADENCE = {
    schedules.DAILY: "yesterday",
    schedules.WEEKDAYS: "yesterday",
    schedules.WEEKLY: "last_week",
    schedules.MONTHLY: "last_month",
}


def window_preset_for_cadence(cadence):
    """Resolve-window preset for a scheduled default-run's cadence.

    ``cadence`` is ``None`` for a manual "Run now" / no-schedule call, which
    resolves to ``"last_week"`` same as before this existed. An unrecognized
    non-``None`` cadence also falls back to ``"last_week"`` but is logged —
    silently drifting to the wrong window for a scheduling feature is worse
    than a loud fallback.
    """
    if cadence is None:
        return "last_week"
    preset = _WINDOW_PRESET_BY_CADENCE.get(cadence)
    if preset is None:
        logger.warning("unmapped schedule cadence %r; defaulting window to last_week", cadence)
        return "last_week"
    return preset


def sample_overrides_for(definition):
    """Extract the MUAC / Other sampling percentages from a creator definition's
    ``config.audit_batch`` (the same defaults the UI pre-fills)."""
    batch = (definition.data.get("config") or {}).get("audit_batch") or {}
    track_a = batch.get("track_a") or {}
    track_b = batch.get("track_b") or {}
    return {
        "muac_sample_percentage": track_a.get("sample_percentage", 100),
        "other_sample_percentage": track_b.get("sample_percentage", 10),
    }


CLUSTERING_OVERRIDE_KEYS = (
    "enable_time_gap",
    "time_gap_minutes",
    "enable_distance",
    "distance_meters",
    "enable_duplicate_detection",
)


def clustering_overrides_for(definition):
    """Extract the pinned visit-clustering / duplicate-detection settings from a
    creator definition's ``config.audit_batch.visit_clustering`` — only the keys
    actually present (a definition created before this block existed contributes
    none, leaving the ``weekly_dual_track_audit_create`` handler's own
    ``state``-fallback in charge).

    Mirrors the render's own guard (see its ``enableDuplicateDetection`` effect):
    duplicate detection has no groupings to check across when both clustering
    gates are explicitly off, so a pinned config carrying that nonsensical
    combination is corrected here rather than passed through. A gate key that's
    simply ABSENT (not explicitly ``False``) is left alone — that's the
    handler's state-fallback's call to make, not this function's.
    """
    batch = (definition.data.get("config") or {}).get("audit_batch") or {}
    visit_clustering = batch.get("visit_clustering") or {}
    overrides = {key: visit_clustering[key] for key in CLUSTERING_OVERRIDE_KEYS if key in visit_clustering}
    if (
        overrides.get("enable_duplicate_detection") is True
        and overrides.get("enable_time_gap") is False
        and overrides.get("enable_distance") is False
    ):
        overrides["enable_duplicate_detection"] = False
    return overrides


def create_batch_run(
    definition, window_start, window_end, *, access_token, sample_overrides=None, criteria_overrides=None
):
    """Create ONE fresh audit-batch run for ``definition``'s owner and build
    the ``weekly_dual_track_audit_create`` job_config. Returns ``(owner_kwargs,
    run, job_config)`` where ``owner_kwargs`` is exactly one of
    ``{"opportunity_id": ...}`` / ``{"program_id": ...}``. Shared by the eager
    (cron) path and the in-process, progress-relayed fan-out (program creator).

    Ownership follows the definition's own record FK (``program_id_of``, same
    helper ``program_audit_creator``'s ``_program_owner`` uses) — NOT a guess
    at ``opportunity_ids[0]``. A program-owned multi-opp instance (e.g. a
    program-level Weekly Dual-Track Image Audit, scheduled directly rather
    than via the program creator's per-opp fan-out) must get a program-scoped
    ``WorkflowDataAccess`` and a program-owned run: scoping it to
    ``opportunity_ids[0]`` instead sends the job handler's own
    ``get_definition()`` looking for a program-owned definition through an
    opp-scoped client (404 → job fails), creates the run as opp-owned instead
    of program-owned, and leaves it invisible to a program-scoped "Open" link
    (and to an opp-scoped one, since it isn't actually that opp's run either).

    ``criteria_overrides`` (optional dict) rides through job_config to the
    ``weekly_dual_track_audit_create`` handler as-is. Originally
    ``pass_threshold``/``deliver_unit_types``/``visit_statuses`` (PR #884);
    the default-run hook also uses it to carry the pinned
    ``enable_time_gap``/``time_gap_minutes``/``enable_distance``/
    ``distance_meters``/``enable_duplicate_detection`` visit-clustering
    settings, since the handler already reads all of these keys straight off
    ``job_config`` with a ``state`` fallback.
    """
    def_id = definition.id
    program_id = program_id_of(definition)
    owner_kwargs = (
        {"program_id": program_id}
        if program_id is not None
        else {"opportunity_id": definition.opportunity_id or definition.opportunity_ids[0]}
    )

    # Scoped client matching the definition's actual owner — never an
    # unscoped read (Global Constraint).
    wda = WorkflowDataAccess(access_token=access_token, **owner_kwargs)
    try:
        run = wda.create_run(
            def_id,
            period_start=window_start,
            period_end=window_end,
            initial_state={"window_start": window_start, "window_end": window_end},
            **owner_kwargs,
        )
    finally:
        wda.close()

    job_config = {
        "job_type": JOB_TYPE,
        "run_id": run.id,
        "window_start": window_start,
        "window_end": window_end,
        **owner_kwargs,
    }
    if sample_overrides:
        # {muac_sample_percentage, other_sample_percentage}
        job_config.update(sample_overrides)
    if criteria_overrides:
        # see create_batch_run's docstring above for what this carries
        job_config.update(criteria_overrides)
    return owner_kwargs, run, job_config


def run_this_week_batch(
    definition, window_start, window_end, *, access_token, sample_overrides=None, criteria_overrides=None
):
    """Create a fresh audit-batch run and fire the batch job SYNCHRONOUSLY (eager).

    Used by the no-UI cron/default-run path. Returns
    ``{"run_id", "sessions_created", "status"}`` (``status`` is ``"failed"`` if the
    batch errored). NOT idempotent — every call creates + fires a new run.
    """
    owner_kwargs, run, job_config = create_batch_run(
        definition,
        window_start,
        window_end,
        access_token=access_token,
        sample_overrides=sample_overrides,
        criteria_overrides=criteria_overrides,
    )
    # bind=True Celery task; run synchronously in-process via .apply().
    eager = run_workflow_job.apply(
        kwargs={"job_config": job_config, "access_token": access_token, "run_id": run.id, **owner_kwargs}
    )
    succeeded = eager.successful()
    res = eager.result if (succeeded and isinstance(eager.result, dict)) else {}
    return {
        "run_id": run.id,
        "sessions_created": (res or {}).get("sessions_created", 0),
        "status": "ready" if succeeded else "failed",
    }


def resume_batch_run(definition, run, *, access_token):
    """Re-fire the batch job against an EXISTING run, instead of creating a new
    one -- for resuming a run whose process died mid-batch (deploy kill, OOM,
    crash) before it finished every (opportunity, track) call.

    Re-derives ``window_start``/``window_end`` from ``run.data.state`` (written
    once at ``create_run`` time by ``create_batch_run``, so it survives even a
    kill on the very first call) and sampling/clustering overrides from the
    definition's CURRENT pinned config via the same ``sample_overrides_for`` /
    ``clustering_overrides_for`` helpers ``create_batch_run`` uses -- a resumed
    invocation applies identically to what a fresh one would.

    Idempotency (skipping (opportunity, track) calls that already produced a
    session on a prior invocation for this run_id) lives in the
    ``weekly_dual_track_audit_create`` handler itself, not here -- this
    function only re-fires the SAME job against the SAME run_id; it is what
    makes any repeat call safe. Always dispatches async (``.delay``), matching
    ``dispatch_batch`` -- a resumed run can have hundreds of calls left and
    must not block the caller.

    Raises ``ValueError`` if the run has no persisted window (nothing was ever
    created for it — resuming isn't meaningful, a fresh run is what's needed).
    """
    state = run.data.get("state", {}) or {}
    window_start = state.get("window_start")
    window_end = state.get("window_end")
    if not window_start or not window_end:
        raise ValueError(f"run {run.id} has no window_start/window_end in state; nothing to resume")

    program_id = program_id_of(definition)
    owner_kwargs = (
        {"program_id": program_id}
        if program_id is not None
        else {"opportunity_id": definition.opportunity_id or definition.opportunity_ids[0]}
    )

    job_config = {
        "job_type": JOB_TYPE,
        "run_id": run.id,
        "window_start": window_start,
        "window_end": window_end,
        **owner_kwargs,
        **sample_overrides_for(definition),
        **clustering_overrides_for(definition),
    }
    task = run_workflow_job.delay(job_config=job_config, access_token=access_token, run_id=run.id, **owner_kwargs)
    return {"run_id": run.id, "task_id": task.id, "status": "running"}


def dispatch_batch(
    definition, window_start, window_end, *, access_token, sample_overrides=None, criteria_overrides=None
):
    """Create a fresh audit-batch run and DISPATCH its batch job ASYNC (``.delay``),
    returning immediately without waiting.

    This is the SAME ``weekly_dual_track_audit_create`` job the per-opp workflow
    page runs — the program creator fans these out one per opportunity and each
    row polls its own task's status (poll-first, holds no connection), so the
    opportunities run and glide in PARALLEL (governed by the worker pool). Returns
    ``{"run_id", "task_id", "status": "running"}``.
    """
    owner_kwargs, run, job_config = create_batch_run(
        definition,
        window_start,
        window_end,
        access_token=access_token,
        sample_overrides=sample_overrides,
        criteria_overrides=criteria_overrides,
    )
    task = run_workflow_job.delay(job_config=job_config, access_token=access_token, run_id=run.id, **owner_kwargs)
    return {"run_id": run.id, "task_id": task.id, "status": "running"}
