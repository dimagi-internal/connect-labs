"""Audit-batch generation seam.

Shared helpers for the ``weekly_dual_track_audit`` creator's default-run hook:
- ``resolve_window`` maps a preset (``last_week`` …) to inclusive ISO dates,
  mirroring the render's ``calculateDateRange`` so the UI and the no-UI
  default-run path agree on what "last week" means.
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

from connect_labs.workflow.data_access import WorkflowDataAccess
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

    if preset == "last_week":
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


def create_batch_run(definition, window_start, window_end, *, access_token, sample_overrides=None):
    """Create ONE fresh audit-batch run for ``definition``'s opportunity and build
    the ``weekly_dual_track_audit_create`` job_config. Returns ``(opp_id, run,
    job_config)``. Shared by the eager (cron) path and the in-process, progress-
    relayed fan-out (program creator).
    """
    opp_id = definition.opportunity_id or definition.opportunity_ids[0]
    def_id = definition.id

    # Opp-scoped client — never an unscoped read (Global Constraint).
    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_id)
    try:
        run = wda.create_run(
            def_id,
            opportunity_id=opp_id,
            period_start=window_start,
            period_end=window_end,
            initial_state={"window_start": window_start, "window_end": window_end},
        )
    finally:
        wda.close()

    job_config = {
        "job_type": JOB_TYPE,
        "run_id": run.id,
        "opportunity_id": opp_id,
        "window_start": window_start,
        "window_end": window_end,
    }
    if sample_overrides:
        # {muac_sample_percentage, other_sample_percentage}
        job_config.update(sample_overrides)
    return opp_id, run, job_config


def run_this_week_batch(definition, window_start, window_end, *, access_token, sample_overrides=None):
    """Create a fresh audit-batch run and fire the batch job SYNCHRONOUSLY (eager).

    Used by the no-UI cron/default-run path. Returns
    ``{"run_id", "sessions_created", "status"}`` (``status`` is ``"failed"`` if the
    batch errored). NOT idempotent — every call creates + fires a new run.
    """
    opp_id, run, job_config = create_batch_run(
        definition, window_start, window_end, access_token=access_token, sample_overrides=sample_overrides
    )
    # bind=True Celery task; run synchronously in-process via .apply().
    eager = run_workflow_job.apply(
        kwargs={"job_config": job_config, "access_token": access_token, "run_id": run.id, "opportunity_id": opp_id}
    )
    succeeded = eager.successful()
    res = eager.result if (succeeded and isinstance(eager.result, dict)) else {}
    return {
        "run_id": run.id,
        "sessions_created": (res or {}).get("sessions_created", 0),
        "status": "ready" if succeeded else "failed",
    }


def run_batch_in_process(run, job_config, *, access_token, progress_callback=None):
    """Run the audit-creation handler for an already-created batch ``run``
    IN-PROCESS (not via Celery), forwarding its per-audit progress to
    ``progress_callback``.

    This is what the program creator uses so ONE program job/SSE stream can relay
    every opportunity's progress — the deployment's ASGI worker can only serve one
    long-lived SSE stream at a time, so a stream-per-opp starves; a single relayed
    stream does not. Returns ``{"run_id", "sessions_created", "status"}``.
    """
    from connect_labs.workflow.job_handlers.weekly_dual_track_audit import weekly_dual_track_audit_create

    try:
        res = weekly_dual_track_audit_create(job_config, access_token, progress_callback=progress_callback)
        return {"run_id": run.id, "sessions_created": (res or {}).get("sessions_created", 0), "status": "ready"}
    except Exception:
        logger.exception("[audit_generation] in-process batch failed for run %s", run.id)
        return {"run_id": run.id, "sessions_created": 0, "status": "failed"}
