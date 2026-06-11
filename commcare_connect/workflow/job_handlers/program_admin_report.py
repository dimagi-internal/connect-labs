"""Program Admin Report rollup job handler.

Computes the cross-opportunity rollup (per-FLW flags + audits + tasks from
the watched sources) and writes it into the run's **state** while the run is
live. Completion then captures that state declaratively via the template's
``snapshot_inputs`` manifest — there is no ``build_snapshot`` hook anymore,
so program_admin_report instances own their completion contract like every
other saved-runs template.

Triggered from the render code's "Refresh data" button via
``actions.startJob(run_id, {job_type: "program_admin_rollup", run_id, ...})``.
"""

import logging

from commcare_connect.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


@register_job_handler("program_admin_rollup")
def program_admin_rollup(job_config: dict, access_token: str, progress_callback=None) -> dict:
    """Compute and persist the watched-sources rollup for a live run.

    job_config keys:
      - run_id (required): the program_admin_report run to roll up. Its state
        must already contain window_start / window_end / watched_sources.
      - opportunity_id (injected by the framework): the run's primary opp.
    """
    from commcare_connect.workflow.data_access import WorkflowDataAccess
    from commcare_connect.workflow.templates.program_admin_report import compute_program_admin_rollup

    run_id = job_config.get("run_id")
    opportunity_id = job_config.get("opportunity_id")
    if not run_id:
        raise ValueError("program_admin_rollup requires run_id in job_config")

    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if run.is_completed:
            raise ValueError(f"run {run_id} is completed; its rollup is frozen in the snapshot")

        state = run.data.get("state", {})
        rollup = compute_program_admin_rollup(
            state=state,
            access_token=access_token,
            progress_callback=progress_callback,
        )
        if rollup.get("error"):
            raise ValueError(f"rollup failed: {rollup['error']} — set window_start/window_end in run state first")

        # Persist into run state so the data survives reloads and conclude
        # captures it via the declarative manifest.
        wda.update_run_state(run_id, rollup)
    finally:
        wda.close()

    sources = rollup.get("watched_summary", [])
    logger.info("[ProgramAdminRollup] run %s: rolled up %d watched source(s)", run_id, len(sources))
    return {
        "successful": len(sources),
        "failed": 0,
        **rollup,
    }
