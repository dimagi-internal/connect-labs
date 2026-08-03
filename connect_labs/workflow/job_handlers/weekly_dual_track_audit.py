"""Weekly Dual-Track Audit batch-creation job handler.

Triggered from the creator render code's "Create this week's audits" button via
actions.startJob(run_id, {job_type: "weekly_dual_track_audit_create", run_id,
opportunity_id}). Loops the definition's opportunity_ids x 2 tracks and invokes
run_audit_creation synchronously for each. Schedulable: a cron can call the same
handler with the same job_config.

Scope: opportunity_id for an opp-owned run, program_id for a program-owned run
(run_workflow_job injects program_id into job_config — see tasks.py). Both must
be threaded into WorkflowDataAccess or a program-owned run's get_run() 404s.
"""

import logging

from connect_labs.audit.data_access import (
    AuditCriteria,
    AuditDataAccess,
    create_mock_request,
    is_audit_creation_cancelled,
)
from connect_labs.audit.tasks import run_audit_creation
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


def _coerce_max_flws(value):
    """Coerce a max_flws input to a positive int, or None (no cap) if it's
    missing, non-numeric, or not positive. This field's whole purpose is a
    safe way to shrink a run for faster iteration, so a bad input (a decimal,
    a negative, a stray string) falls back to "no cap" rather than crashing
    the task or -- worse -- silently truncating from the wrong end (a naive
    ``[:max_flws]`` slice with a negative value would drop the LAST N FLWs
    instead of capping to the first N)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _resolve_flw_cap(access_token, opportunity_ids, window_start, window_end, max_flws):
    """Resolve max_flws into a concrete, sorted username list ONCE across
    every opportunity in the batch, from an UNSAMPLED visit fetch.

    This runs before build_track_audit_calls / the per-call loop specifically
    so every opp x track call in the batch gets the SAME selected_flw_user_ids
    -- otherwise each call would independently derive its own first-N-usernames
    from its OWN filtered visit set, which could differ: Track A (100% sample)
    and Track B (10% sample, by default) can see different FLWs in their
    respective samples, and get_visit_ids_for_audit already applies
    sample_percentage server-side before any caller sees the visit list. An
    unsampled (sample_percentage=100) discovery fetch, resolved once, avoids
    both the per-track divergence and the sampling-order dependency.

    Returns the resolved usernames via the SAME AuditCriteria.selected_flw_user_ids
    pushdown filter every other FLW-scoping caller already uses (see
    connect_labs.audit.data_access), not a new mechanism.
    """
    data_access = AuditDataAccess(
        opportunity_id=opportunity_ids[0], request=create_mock_request(access_token, opportunity_ids[0])
    )
    try:
        _, visits = data_access.get_visit_ids_for_audit(
            opportunity_ids,
            criteria=AuditCriteria(
                audit_type="date_range", start_date=window_start, end_date=window_end, sample_percentage=100
            ),
            return_visits=True,
        )
    finally:
        data_access.close()
    usernames = sorted({v["username"] for v in visits if v.get("username")})
    return usernames[:max_flws]


@register_job_handler("weekly_dual_track_audit_create")
def weekly_dual_track_audit_create(job_config: dict, access_token: str, progress_callback=None) -> dict:
    run_id = job_config.get("run_id")
    opportunity_id = job_config.get("opportunity_id")
    program_id = job_config.get("program_id")
    # run_workflow_job's own (fresh, single-use) Celery task id -- injected into
    # job_config by run_workflow_job itself. This is exactly the id the browser
    # already has (returned by start_job_api, passed to actions.cancelJob), so
    # it's the identifier "Cancel" can actually target -- unlike run_id, which
    # is a long-lived DB id that gets reused on every future run of this same
    # workflow instance and would leave a stale cancel flag behind for retries.
    task_id = job_config.get("_task_id")
    if not run_id:
        raise ValueError("weekly_dual_track_audit_create requires run_id in job_config")

    from connect_labs.workflow.templates.weekly_dual_track_audit import build_track_audit_calls

    def _progress(msg, processed=0, total=0):
        if progress_callback:
            progress_callback(msg, processed=processed, total=total)

    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id, program_id=program_id)
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

        # Per-run audit-quality filters (PR #884): pass threshold, deliver unit
        # type, visit status. Same window_start/window_end fallback pattern —
        # prefer the job payload, fall back to whatever was last persisted onto
        # run state (so a per-opp re-run without a fresh payload stays consistent).
        pass_threshold = job_config.get("pass_threshold", state.get("pass_threshold"))
        deliver_unit_types = job_config.get("deliver_unit_types", state.get("deliver_unit_types"))
        visit_statuses = job_config.get("visit_statuses", state.get("visit_statuses"))
        enable_time_gap = job_config.get("enable_time_gap", state.get("enable_time_gap"))
        time_gap_minutes = job_config.get("time_gap_minutes", state.get("time_gap_minutes"))
        enable_distance = job_config.get("enable_distance", state.get("enable_distance"))
        distance_meters = job_config.get("distance_meters", state.get("distance_meters"))
        enable_duplicate_detection = job_config.get(
            "enable_duplicate_detection", state.get("enable_duplicate_detection")
        )
        max_flws = _coerce_max_flws(job_config.get("max_flws", state.get("max_flws")))
        opportunity_ids = definition.data.get("opportunity_ids") or [opportunity_id]

        selected_flw_user_ids = None
        if max_flws:
            selected_flw_user_ids = _resolve_flw_cap(access_token, opportunity_ids, window_start, window_end, max_flws)
            logger.info(
                "[WeeklyDualTrackAudit] run %s: max_flws=%d resolved to %d FLW(s)",
                run_id,
                max_flws,
                len(selected_flw_user_ids),
            )

        calls = build_track_audit_calls(
            opportunity_ids=opportunity_ids,
            opp_names=batch.get("opp_names", {}),
            per_opp=batch.get("per_opp", {}),
            track_a=track_a,
            track_b=track_b,
            window_start=window_start,
            window_end=window_end,
            username=run.username or job_config.get("username", ""),
            workflow_run_id=run_id,
            pass_threshold=pass_threshold,
            deliver_unit_types=deliver_unit_types,
            visit_statuses=visit_statuses,
            enable_time_gap=enable_time_gap,
            time_gap_minutes=time_gap_minutes,
            enable_distance=enable_distance,
            distance_meters=distance_meters,
            enable_duplicate_detection=enable_duplicate_detection,
            selected_flw_user_ids=selected_flw_user_ids,
        )

        from connect_labs.utils.progress_relays import pop_relay, register_relay

        successful, failed, sessions_created = 0, 0, 0
        for idx, call in enumerate(calls):
            # Cooperative cancellation (the "Cancel" button on the run).
            # Checked between calls (not just inside AI review) so a cancel
            # while call #1 is still reviewing skips starting #2, #3, ... too.
            if task_id and is_audit_creation_cancelled(task_id):
                logger.info(
                    "[WeeklyDualTrackAudit] run %s: cancelled, stopping before call %d/%d",
                    run_id,
                    idx + 1,
                    len(calls),
                )
                break
            opp = call["opportunities"][0]
            tag = call["criteria"]["tag"]

            # Relay run_audit_creation's fine-grained per-FLW / per-image progress up
            # so a program-creator row GLIDES (e.g. "muac · 7/20 field workers")
            # instead of stepping per track. Register the relay in the in-process
            # registry keyed by this run — NOT via .apply() kwargs, which the eager
            # path serializes (a closure there breaks audit creation entirely).
            def _track_progress(msg, processed=0, total=0, _tag=tag):
                _progress(f"{_tag} · {msg}", processed=processed, total=total)

            register_relay(run_id, _track_progress)
            try:
                eager = run_audit_creation.apply(kwargs={"access_token": access_token, "cancel_key": task_id, **call})
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
            finally:
                pop_relay(run_id)

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
            {
                "window_start": window_start,
                "window_end": window_end,
                "last_batch": last_batch,
                "pass_threshold": pass_threshold,
                "deliver_unit_types": deliver_unit_types,
                "visit_statuses": visit_statuses,
                "enable_time_gap": enable_time_gap,
                "time_gap_minutes": time_gap_minutes,
                "enable_distance": enable_distance,
                "distance_meters": distance_meters,
                "enable_duplicate_detection": enable_duplicate_detection,
                "max_flws": max_flws,
            },
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
