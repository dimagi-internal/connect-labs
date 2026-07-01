"""Program Audit Creator generation job handler.

Triggered from the program creator render code's "Generate this week's audits"
button via actions.startJob(run_id, {job_type: "program_audit_generate", run_id,
opportunity_id, window_start, window_end}). Loads the program creator definition
and fans out to every configured per-opp creator instance via
``fan_out_generate``, forwarding progress to the job's progress stream.
"""

import logging

from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


@register_job_handler("program_audit_generate")
def program_audit_generate(job_config: dict, access_token: str, progress_callback=None) -> dict:
    run_id = job_config.get("run_id")
    opportunity_id = job_config.get("opportunity_id")
    if not run_id:
        raise ValueError("program_audit_generate requires run_id in job_config")

    from connect_labs.workflow.templates.program_audit_creator import fan_out_generate

    # Opp-scoped read of the program run + its definition (Global Constraint).
    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        definition = wda.get_definition(run.definition_id)
        if definition is None:
            raise ValueError(f"definition {run.definition_id} not found")
        state = run.data.get("state", {})
        window_start = job_config.get("window_start") or state.get("window_start")
        window_end = job_config.get("window_end") or state.get("window_end")
    finally:
        wda.close()

    def _progress(msg, processed=0, total=0):
        if progress_callback:
            progress_callback(msg, processed=processed, total=total)

    result = fan_out_generate(
        definition=definition,
        run_id=run_id,
        access_token=access_token,
        window=(window_start, window_end),
        progress_callback=_progress,
    )

    per_opp = result.get("per_opp", {})
    logger.info("[ProgramAuditCreator] run %s: fanned out to %d opp(s)", run_id, len(per_opp))
    return {"successful": len(per_opp), "failed": 0, **result}
