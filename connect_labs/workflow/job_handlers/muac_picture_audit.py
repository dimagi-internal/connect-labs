"""Muac Picture Audit creation job handler.

Triggered from the creator render code's "Create Audit" button via
actions.startJob(run_id, {job_type: "muac_picture_audit_create", run_id,
opportunity_id, program_id, opportunities, criteria, visit_ids,
flw_visit_ids, image_audits, context_fields}). Invokes run_audit_creation
directly, once, with the render-assembled payload spanning every selected
opportunity -- mirroring the standalone /audit/create/ wizard's own
single-call submission. Granularity (including per_opp's no-op behavior,
faithfully replicated rather than fixed) is handled entirely inside
run_audit_creation itself; nothing is looped per-opportunity here.

Scope: opportunity_id for an opp-owned run, program_id for a program-owned
run (run_workflow_job injects program_id into job_config — see
connect_labs/workflow/tasks.py). Both must be threaded into
WorkflowDataAccess or a program-owned run's get_run() 404s.
"""

import logging

from connect_labs.audit.tasks import run_audit_creation
from connect_labs.utils.progress_relays import pop_relay, register_relay
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


@register_job_handler("muac_picture_audit_create")
def muac_picture_audit_create(job_config: dict, access_token: str, progress_callback=None) -> dict:
    run_id = job_config.get("run_id")
    opportunity_id = job_config.get("opportunity_id")
    program_id = job_config.get("program_id")
    if not run_id:
        raise ValueError("muac_picture_audit_create requires run_id in job_config")

    opportunities = job_config.get("opportunities") or []
    if not opportunities:
        raise ValueError("muac_picture_audit_create requires at least one opportunity in job_config")
    criteria = job_config.get("criteria") or {}

    def _progress(msg, processed=0, total=0):
        if progress_callback:
            progress_callback(msg, processed=processed, total=total)

    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id, program_id=program_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")

        # Relay run_audit_creation's fine-grained progress up via the in-process
        # registry keyed by run_id -- NOT via .apply() kwargs, which the eager
        # path serializes (a closure there breaks audit creation entirely).
        register_relay(run_id, _progress)
        try:
            eager = run_audit_creation.apply(
                kwargs={
                    "access_token": access_token,
                    "username": run.username or job_config.get("username", ""),
                    "opportunities": opportunities,
                    "criteria": criteria,
                    "visit_ids": job_config.get("visit_ids") or None,
                    "flw_visit_ids": job_config.get("flw_visit_ids") or None,
                    "workflow_run_id": run_id,
                    "image_audits": job_config.get("image_audits") or None,
                    "context_fields": job_config.get("context_fields") or None,
                }
            )
        finally:
            pop_relay(run_id)

        result = eager.result if isinstance(eager.result, dict) else {}
        sessions = result.get("sessions") or []
        sessions_created = len(sessions)

        last_batch = {
            "sessions_created": sessions_created,
            "opportunity_ids": [o["id"] for o in opportunities],
            "title": criteria.get("title"),
            "tag": criteria.get("tag"),
        }
        wda.update_run_state(run_id, {"last_batch": last_batch})
    finally:
        wda.close()

    logger.info("[MuacPictureAudit] run %s: %d sessions created", run_id, sessions_created)
    return {"sessions_created": sessions_created, "sessions": sessions, "last_batch": last_batch}
