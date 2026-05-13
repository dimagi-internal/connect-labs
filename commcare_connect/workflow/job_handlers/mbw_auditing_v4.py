"""
MBW Auditing V4 job handler.

Pipeline-native: reads from 3 SQL-computed pipelines (GPS/visit-level visits,
registrations with schedules extractor, GS forms). No form_json reads.

Pipeline aliases (matching the workflow's pipeline_sources):
  - visits:        visit-level rows with GPS coords, form_name, bf_status,
                   and distance_from_prev_case_visit_m from lag_haversine
  - registrations: per-mother rows with schedules list (mbw_visit_schedules
                   extractor) and eligible_full_intervention_bonus
  - gs_forms:      per-GS-visit rows with gs_score and user_connect_id

All metrics are computed in Python from these rows. The server_fetch_pipelines
flag in the startJob call causes the task framework to auto-fetch pipeline data
server-side — no browser round-trip of large row sets.
"""

import logging
import math
from datetime import date, datetime, timedelta, timezone

from commcare_connect.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)

_GRACE_PERIOD_DAYS = 5  # v1's IS_DUE_PAST_GRACE: visit is due if scheduled >= 5d ago


def _get_open_tasks(access_token: str, opportunity_id: int) -> dict:
    """Return the most recent non-closed task per FLW for this opportunity."""
    try:
        from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
        from commcare_connect.tasks.models import TaskRecord

        with LabsRecordAPIClient(access_token=access_token, opportunity_id=opportunity_id) as client:
            # Filter by data.opportunity_id server-side; without this the query returns
            # all tasks across every opportunity (tasks use data FK, not record FK).
            tasks = client.get_records(
                experiment="tasks",
                type="Task",
                model_class=TaskRecord,
                opportunity_id=opportunity_id,  # → data__opportunity_id server-side filter
            )

        open_tasks = [t for t in tasks if t.data.get("status") != "closed"]

        by_username: dict[str, dict] = {}
        for task in open_tasks:
            username = (task.data.get("username") or "").lower()
            if not username:
                continue
            created_at = ""
            for event in task.data.get("events", []):
                if event.get("event_type") == "created":
                    created_at = event.get("timestamp") or ""
                    break
            existing = by_username.get(username)
            if not existing or created_at > existing.get("triggered_at", ""):
                by_username[username] = {
                    "task_id": task.id,
                    "status": task.data.get("status", "investigating"),
                    "triggered_at": created_at,
                    "title": task.data.get("title", ""),
                }
        return by_username
    except Exception:
        logger.exception("Failed to fetch open tasks")
        return {}


def _get_prev_categories(access_token: str, opportunity_id: int, workflow_definition_id: int) -> dict:
    """Fetch worker_results from the most recent completed run for this workflow."""
    try:
        from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
        from commcare_connect.workflow.data_access import WorkflowRunRecord

        with LabsRecordAPIClient(access_token=access_token, opportunity_id=opportunity_id) as client:
            runs = client.get_records(
                experiment="workflow",
                type="workflow_run",
                model_class=WorkflowRunRecord,
                status="completed",
            )
        completed = [r for r in runs if r.data.get("definition_id") == workflow_definition_id]
        if not completed:
            return {}
        completed.sort(key=lambda r: r.data.get("created_at") or "", reverse=True)
        state = completed[0].data.get("state") or {}
        return state.get("worker_results") or {}
    except Exception:
        logger.exception("Failed to fetch previous run categories")
        return {}


@register_job_handler("mbw_auditing_v4")
def handle_mbw_auditing_v4_job(job_config: dict, access_token: str, progress_callback) -> dict:
    """
    Handle MBW Auditing V4 job.

    Receives pipeline_data auto-fetched via server_fetch_pipelines=True:
      - visits:        visit-level rows (form_name, mother_case_id, visit_datetime,
                       bf_status, latitude, longitude, distance_from_prev_case_visit_m)
      - registrations: per-mother rows (mother_case_id, eligible_full_intervention_bonus,
                       schedules list from mbw_visit_schedules extractor)
      - gs_forms:      GS visit rows (user_connect_id, gs_score)

    Optional job_config keys:
      - task_filters: {username: triggered_at_isostr} — when set, only visits
        submitted AFTER the trigger date are included (Tab 2 improvement analysis)
      - workflow_definition_id: int — used to fetch previous run categories

    Returns:
        {"flw_summaries": [...], "prev_categories": {...}}
    """
    pipeline_data = job_config.get("pipeline_data", {})
    active_usernames = {u.lower() for u in job_config.get("active_usernames", [])}
    flw_names = job_config.get("flw_names", {})
    task_filters: dict = job_config.get("task_filters") or {}
    opportunity_id: int | None = job_config.get("opportunity_id")
    workflow_definition_id: int | None = job_config.get("workflow_definition_id")

    visits_rows: list[dict] = pipeline_data.get("visits", {}).get("rows", [])
    reg_rows: list[dict] = pipeline_data.get("registrations", {}).get("rows", [])
    gs_rows: list[dict] = pipeline_data.get("gs_forms", {}).get("rows", [])

    progress_callback("Processing visit data…")

    # ── Single pass over visits: attribution, GPS distances, EBF%, mother sets ──
    # Sort chronologically once so last-write-wins attribution is correct.
    visits_sorted = sorted(visits_rows, key=lambda r: r.get("visit_datetime") or "")

    mother_to_flw: dict[str, str] = {}
    visits_by_mother: dict[str, dict[str, str]] = {}  # mid → {form_name → date}
    gps_distances: dict[str, list[float]] = {}
    ebf_count_by_flw: dict[str, int] = {}
    bf_count_by_flw: dict[str, int] = {}
    mother_sets_by_flw: dict[str, set] = {}

    for row in visits_sorted:
        username = (row.get("username") or row.get("_username") or "").lower()
        if not username:
            continue

        vdt = (row.get("visit_datetime") or "")[:10]

        # For Tab 2: skip visits submitted before the task trigger date
        if task_filters and username in task_filters:
            trigger = (task_filters[username] or "")[:10]
            if vdt and trigger and vdt < trigger:
                continue

        mid = (row.get("mother_case_id") or "").lower()
        form_name = row.get("form_name") or ""

        if mid:
            mother_to_flw[mid] = username
            mother_sets_by_flw.setdefault(username, set()).add(mid)
            if form_name and vdt:
                visits_by_mother.setdefault(mid, {})[form_name] = vdt

        dist = row.get("distance_from_prev_case_visit_m")
        if dist is not None:
            try:
                dist_f = float(dist)
                if not math.isnan(dist_f):
                    gps_distances.setdefault(username, []).append(dist_f)
            except (TypeError, ValueError):
                pass

        bf_status = (row.get("bf_status") or "").strip()
        if bf_status:
            bf_count_by_flw[username] = bf_count_by_flw.get(username, 0) + 1
            if "ebf" in bf_status.split():
                ebf_count_by_flw[username] = ebf_count_by_flw.get(username, 0) + 1

    # ── Registrations: schedules + eligibility per mother ──
    progress_callback("Processing registration data…")

    mother_schedules: dict[str, list] = {}
    mother_eligibility: dict[str, bool] = {}

    for row in reg_rows:
        schedules = row.get("schedules") or []
        if not schedules or not isinstance(schedules, list):
            continue
        # Extract mother_case_id from first schedule entry (set by extractor)
        mid = ""
        for s in schedules:
            if isinstance(s, dict):
                mid = (s.get("mother_case_id") or "").lower()
                if mid:
                    break
        if not mid:
            mid = (row.get("mother_case_id") or "").lower()
        if not mid:
            continue
        mother_schedules[mid] = schedules
        elig = str(row.get("eligible_full_intervention_bonus") or "").strip()
        mother_eligibility[mid] = elig == "1"

    # ── GS scores: max score per user (keyed by user_connect_id) ──
    gs_by_user: dict[str, float] = {}
    for row in gs_rows:
        cid = (row.get("user_connect_id") or row.get("username") or "").lower()
        raw = row.get("gs_score")
        if not cid or raw is None:
            continue
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        if cid not in gs_by_user or score > gs_by_user[cid]:
            gs_by_user[cid] = score

    # ── Follow-up rate and eligibility computation ──
    progress_callback("Computing follow-up metrics…")

    now = datetime.now(tz=timezone.utc).date()
    grace_cutoff = now - timedelta(days=_GRACE_PERIOD_DAYS)

    # flw_fu[username] = {total_eligible, filtered_completed, filtered_denominator, still_eligible}
    flw_fu: dict[str, dict] = {}

    for mid, schedules in mother_schedules.items():
        flw = mother_to_flw.get(mid)
        if not flw or (active_usernames and flw not in active_usernames):
            continue

        is_eligible = mother_eligibility.get(mid, False)
        mother_visits = visits_by_mother.get(mid, {})

        bucket = flw_fu.setdefault(flw, {
            "total_eligible": 0,
            "filtered_completed": 0,
            "filtered_denominator": 0,
            "still_eligible": 0,
        })

        if is_eligible:
            bucket["total_eligible"] += 1

        missed_count = 0
        for s in schedules:
            if not isinstance(s, dict):
                continue
            visit_type = s.get("visit_type", "")
            scheduled_str = (s.get("visit_date_scheduled") or "")
            expiry_str = (s.get("visit_expiry_date") or "")
            is_completed = bool(mother_visits.get(visit_type))

            past_grace = False
            if scheduled_str:
                try:
                    sched = date.fromisoformat(scheduled_str[:10])
                    past_grace = sched <= grace_cutoff
                except (ValueError, TypeError):
                    pass

            if not is_completed and expiry_str:
                try:
                    expiry = date.fromisoformat(expiry_str[:10])
                    if expiry < now:
                        missed_count += 1
                except (ValueError, TypeError):
                    pass

            if past_grace:
                bucket["filtered_denominator"] += 1
                if is_completed:
                    bucket["filtered_completed"] += 1

        if is_eligible and missed_count < 2:
            bucket["still_eligible"] += 1

    # ── Previous run categories ──
    progress_callback("Loading previous run data…")
    prev_categories: dict = {}
    if workflow_definition_id and opportunity_id and access_token:
        prev_categories = _get_prev_categories(access_token, opportunity_id, workflow_definition_id)

    # ── Open tasks across all runs ──
    progress_callback("Loading open tasks…")
    open_tasks: dict = {}
    if opportunity_id and access_token:
        open_tasks = _get_open_tasks(access_token, opportunity_id)

    # ── Build FLW summaries ──
    progress_callback("Building FLW summaries…")

    target_usernames = active_usernames or set(mother_to_flw.values())
    flw_summaries = []

    for username in sorted(target_usernames):
        u = username.lower()
        fu = flw_fu.get(u, {})
        dists = gps_distances.get(u, [])

        # Mother counts
        num_mothers = len(mother_sets_by_flw.get(u, set()))
        total_eligible = fu.get("total_eligible", 0)

        # EBF%
        bf_count = bf_count_by_flw.get(u, 0)
        ebf_count = ebf_count_by_flw.get(u, 0)
        ebf_pct = round(ebf_count / bf_count * 100) if bf_count > 0 else None

        # Follow-up rate
        denom = fu.get("filtered_denominator", 0)
        completed_fu = fu.get("filtered_completed", 0)
        followup_rate = round(completed_fu / denom * 100) if denom > 0 else None

        # % still eligible
        still_elig = fu.get("still_eligible", 0)
        pct_still_eligible = round(still_elig / total_eligible * 100) if total_eligible > 0 else None

        # GPS metrics
        if dists:
            mean_m = sum(dists) / len(dists)
            sorted_d = sorted(dists)
            median_m = float(sorted_d[len(sorted_d) // 2])
            revisit_m = round(mean_m)
            meter_per_visit = round(median_m)
            dist_ratio = round(mean_m / median_m, 2) if median_m > 0 else None
        else:
            revisit_m = meter_per_visit = dist_ratio = None

        # GS score (user_connect_id matches username in MBW context)
        gs_raw = gs_by_user.get(u)
        gs_score = round(gs_raw) if gs_raw is not None else None

        flw_summaries.append({
            "username": u,
            "display_name": flw_names.get(u) or flw_names.get(username) or u,
            "num_mothers": num_mothers,
            "num_mothers_eligible": total_eligible,
            "gs_score": gs_score,
            "followup_rate": followup_rate,
            "pct_still_eligible": pct_still_eligible,
            "ebf_pct": ebf_pct,
            "revisit_dist": revisit_m,
            "meter_per_visit": meter_per_visit,
            "dist_ratio": dist_ratio,
            "minute_per_visit": None,  # requires visit timeStart — not in current pipeline
        })

    return {
        "flw_summaries": flw_summaries,
        "prev_categories": prev_categories,
        "open_tasks": open_tasks,
    }
