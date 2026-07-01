"""Audit-batch generation seam.

Shared helpers for the ``weekly_dual_track_audit`` creator's default-run hook:
- ``resolve_window`` maps a preset (``last_week`` …) to inclusive ISO dates,
  mirroring the render's ``calculateDateRange`` so the UI and the no-UI
  default-run path agree on what "last week" means.
- ``run_this_week_batch`` creates (or reuses) one audit-batch run for a single
  ``weekly_dual_track_audit`` definition's opportunity and fires the batch job
  synchronously — idempotent per (opportunity, window).

Global constraints honoured here:
- **Opp-scoping:** every read/write goes through a `WorkflowDataAccess` scoped
  to the definition's single owning opportunity — never one unscoped client.
  (Root cause of PRs #777/#779/#783.)
- **Idempotency:** we never create a second batch for an (opp, window) that
  already has a run whose ``state.window_start`` matches.

The heavy lifting (building the per-track audit calls, creating sessions) lives
in the registered ``weekly_dual_track_audit_create`` job handler; here we only
create/reuse the run and fire that job synchronously. Program-wide fan-out is
now the ``audit_par`` report's default-run hook, which calls
``run_default_for_definition`` once per watched per-opp creator instance.
"""

from __future__ import annotations

from datetime import date, timedelta

from commcare_connect.workflow.data_access import WorkflowDataAccess
from commcare_connect.workflow.tasks import run_workflow_job

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


def _run_has_window(run, window_start):
    return ((run.data or {}).get("state", {}) or {}).get("window_start") == window_start


def run_this_week_batch(
    definition,
    window_start,
    window_end,
    *,
    access_token,
    sample_overrides=None,
):
    """Create (or reuse) one audit-batch run for ``definition``'s opportunity and
    fire the batch job synchronously.

    ``definition`` is a ``weekly_dual_track_audit`` creator instance; its owning
    opportunity is ``opportunity_id`` (falling back to the first of
    ``opportunity_ids``). Returns::

        {"run_id": int, "created": bool, "sessions_created": int}

    Idempotent: if the opp's scoped runs already include one whose
    ``state.window_start`` matches, that run is reused (``created=False``) and the
    job is NOT re-fired.
    """
    opp_id = definition.opportunity_id or definition.opportunity_ids[0]
    def_id = definition.id

    # Opp-scoped client — never an unscoped read (Global Constraint).
    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_id)
    try:
        existing = next(
            (r for r in wda.list_runs(def_id) if _run_has_window(r, window_start)),
            None,
        )
        if existing is not None:  # idempotent per (opp, window)
            return {"run_id": existing.id, "created": False, "sessions_created": 0}

        run = wda.create_run(
            def_id,
            opp_id,
            window_start,
            window_end,
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

    # run_workflow_job(self, job_config, access_token, run_id, opportunity_id)
    # — bind=True Celery task; run synchronously in-process via .apply().
    eager = run_workflow_job.apply(
        kwargs={
            "job_config": job_config,
            "access_token": access_token,
            "run_id": run.id,
            "opportunity_id": opp_id,
        }
    )
    res = eager.result if isinstance(eager.result, dict) else {}
    return {
        "run_id": run.id,
        "created": True,
        "sessions_created": (res or {}).get("sessions_created", 0),
    }
