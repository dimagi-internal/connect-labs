"""Weekly Dual-Track Audit batch-creation job handler.

Triggered from the creator render code's "Create this week's audits" button via
actions.startJob(run_id, {job_type: "weekly_dual_track_audit_create", run_id,
opportunity_id}). Loops the definition's opportunity_ids x 2 tracks and invokes
run_audit_creation synchronously for each. Schedulable: a cron can call the same
handler with the same job_config.
"""

import logging

from commcare_connect.audit.tasks import run_audit_creation
from commcare_connect.workflow.data_access import WorkflowDataAccess
from commcare_connect.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


@register_job_handler("weekly_dual_track_audit_create")
def weekly_dual_track_audit_create(job_config: dict, access_token: str, progress_callback=None) -> dict:
    run_id = job_config.get("run_id")
    opportunity_id = job_config.get("opportunity_id")
    if not run_id:
        raise ValueError("weekly_dual_track_audit_create requires run_id in job_config")

    from commcare_connect.workflow.templates.weekly_dual_track_audit import build_track_audit_calls

    def _progress(msg, processed=0, total=0):
        if progress_callback:
            progress_callback(msg, processed=processed, total=total)

    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")

        # Prefer the window passed in the job payload (the render sends it), and
        # fall back to run state. This keeps audit creation working even when the
        # render's best-effort state write flaked — the window still reaches the
        # job via job_config.
        state = run.data.get("state", {})
        window_start = job_config.get("window_start") or state.get("window_start")
        window_end = job_config.get("window_end") or state.get("window_end")
        if not window_start or not window_end:
            raise ValueError("set window_start/window_end (in the job payload or run state) before creating the batch")

        definition = wda.get_definition(run.definition_id)
        if definition is None:
            raise ValueError(f"definition {run.definition_id} not found")
        batch = (definition.data.get("config") or {}).get("audit_batch") or {}

        # Per-run sampling override: the render can pass MUAC / Other sampling
        # percentages chosen for this run; fall back to the pinned config defaults.
        track_a = dict(batch["track_a"])
        track_b = dict(batch["track_b"])
        if job_config.get("muac_sample_percentage") is not None:
            track_a["sample_percentage"] = job_config["muac_sample_percentage"]
        if job_config.get("other_sample_percentage") is not None:
            track_b["sample_percentage"] = job_config["other_sample_percentage"]

        calls = build_track_audit_calls(
            opportunity_ids=definition.data.get("opportunity_ids") or [opportunity_id],
            opp_names=batch.get("opp_names", {}),
            per_opp=batch.get("per_opp", {}),
            track_a=track_a,
            track_b=track_b,
            window_start=window_start,
            window_end=window_end,
            username=run.username or job_config.get("username", ""),
            workflow_run_id=run_id,
        )

        successful, failed, sessions_created = 0, 0, 0
        for idx, call in enumerate(calls):
            opp = call["opportunities"][0]
            _progress(
                f"Creating audit {idx + 1}/{len(calls)} · opp {opp['id']} · {call['criteria']['tag']}",
                processed=idx,
                total=len(calls),
            )
            try:
                eager = run_audit_creation.apply(kwargs={"access_token": access_token, **call})
                res = eager.result if isinstance(eager.result, dict) else {}
                # run_audit_creation returns created sessions under "sessions"
                # (a list of {id, title, ...}); count those.
                sessions_created += len(res.get("sessions", []) or [])
                successful += 1
            except Exception:
                logger.warning(
                    "audit creation failed for opp %s tag %s",
                    opp["id"],
                    call["criteria"]["tag"],
                    exc_info=True,
                )
                failed += 1

        last_batch = {
            "window_start": window_start,
            "window_end": window_end,
            "calls": len(calls),
            "successful": successful,
            "failed": failed,
            "sessions_created": sessions_created,
        }
        # Persist the batch window onto the run so the render (on reload) and
        # the Audit PAR (week bucketing) can read it — the handler runs under the
        # run's owning opp, so this write is reliable, and it lets the render
        # skip its own fragile session-scoped state write.
        wda.update_run_state(
            run_id,
            {"window_start": window_start, "window_end": window_end, "last_batch": last_batch},
        )
    finally:
        wda.close()

    logger.info(
        "[WeeklyDualTrackAudit] run %s: %d calls, %d sessions",
        run_id,
        len(calls),
        sessions_created,
    )
    return {
        "successful": successful,
        "failed": failed,
        "sessions_created": sessions_created,
        "last_batch": last_batch,
    }
