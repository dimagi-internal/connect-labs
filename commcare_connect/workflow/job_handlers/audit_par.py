"""Audit PAR rollup job handler (mirrors program_admin_rollup)."""

import logging

from commcare_connect.workflow.data_access import WorkflowDataAccess
from commcare_connect.workflow.tasks import register_job_handler
from commcare_connect.workflow.templates.audit_par import compute_audit_par_rollup

logger = logging.getLogger(__name__)


@register_job_handler("audit_par_rollup")
def audit_par_rollup(job_config: dict, access_token: str, progress_callback=None) -> dict:
    """Compute and persist the watched-source rollup for a live audit_par run.

    job_config keys:
      - run_id (required): the audit_par run to roll up. Its state must already
        contain window_start / window_end / watched_source.
      - opportunity_id (injected by the framework): the run's primary opp.
    """
    run_id = job_config.get("run_id")
    opportunity_id = job_config.get("opportunity_id")
    if not run_id:
        raise ValueError("audit_par_rollup requires run_id in job_config")

    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if run.is_completed:
            raise ValueError(f"run {run_id} is completed; its rollup is frozen in the snapshot")

        rollup = compute_audit_par_rollup(
            state=run.data.get("state", {}),
            access_token=access_token,
            progress_callback=progress_callback,
        )
        if rollup.get("error"):
            raise ValueError(f"rollup failed: {rollup['error']} — set window_start/window_end first")

        wda.update_run_state(run_id, rollup)
    finally:
        wda.close()

    sources = rollup.get("watched_summary", [])
    logger.info("[AuditParRollup] run %s: rolled up %d opp(s)", run_id, len(sources))
    return {"successful": len(sources), "failed": 0, **rollup}
