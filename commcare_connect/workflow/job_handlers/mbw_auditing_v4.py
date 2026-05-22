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

# Maps form.@name values (as they appear in connect_csv) to the visit_type keys
# produced by the mbw_visit_schedules extractor. Handles trailing spaces and
# naming differences between the CCHQ form name and the registration schedule.
_FORM_NAME_ALIASES: dict[str, str] = {
    "Post delivery visit": "Postnatal Delivery Visit",
}


def _get_open_tasks(access_token: str, opportunity_id: int, progress_callback=None) -> tuple[dict, str]:
    """Return (tasks_by_username, debug_str) for the opportunity."""

    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    try:
        from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
        from commcare_connect.tasks.models import TaskRecord

        with LabsRecordAPIClient(access_token=access_token, opportunity_id=opportunity_id) as client:
            tasks = client.get_records(
                experiment="tasks",
                type="Task",
                model_class=TaskRecord,
            )

        first_opp = tasks[0].data.get("opportunity_id") if tasks else "n/a"
        debug = f"fetched {len(tasks)} total; first data.opportunity_id: {first_opp}"
        _progress(f"Fetched {len(tasks)} task record(s) for opportunity {opportunity_id}.")
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
        return by_username, debug
    except Exception as e:
        logger.exception("Failed to fetch open tasks: %s", e)
        _progress(f"Warning: failed to load open tasks — {e}")
        return {}, f"error: {e}"


def _get_prev_categories(access_token: str, opportunity_id: int, workflow_definition_id: int) -> dict:
    """Fetch the most recent performance category per FLW across all runs for this definition.

    Merges worker_results from every candidate run so a FLW categorised in any
    prior run (not just the single most-recent one) gets a Prev value. For each
    FLW the entry with the latest assessed_at timestamp wins; falls back to the
    run's created_at when assessed_at is absent.
    """
    try:
        from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
        from commcare_connect.workflow.data_access import WorkflowRunRecord

        with LabsRecordAPIClient(access_token=access_token, opportunity_id=opportunity_id) as client:
            runs = client.get_records(
                experiment="workflow",
                type="workflow_run",
                model_class=WorkflowRunRecord,
            )
        candidates = [
            r
            for r in runs
            if r.data.get("definition_id") == workflow_definition_id
            and (r.data.get("state") or {}).get("worker_results")
        ]
        if not candidates:
            return {}

        # Merge: for each FLW keep the entry with the latest assessed_at.
        # Fall back to the run's created_at so older runs without assessed_at
        # still lose to newer ones.
        merged: dict[str, tuple[str, dict]] = {}  # username → (timestamp, entry)
        for run in candidates:
            run_ts = run.data.get("created_at") or ""
            results = (run.data.get("state") or {}).get("worker_results") or {}
            for username, entry in results.items():
                if not isinstance(entry, dict):
                    continue
                entry_ts = entry.get("assessed_at") or run_ts
                existing = merged.get(username)
                if existing is None or entry_ts > existing[0]:
                    merged[username] = (entry_ts, entry)

        return {u: v for u, (_, v) in merged.items()}
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
        submitted AFTER the trigger date are included (Tab 2 improvement analysis).
        Also triggers per-FLW baseline follow-up rate computation at trigger time.
      - workflow_definition_id: int — used to fetch previous run categories
      - current_date: YYYY-MM-DD string — override today's date for historical analysis

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
    visits_agg_rows: list[dict] = pipeline_data.get("visits_agg", {}).get("rows", [])
    reg_rows: list[dict] = pipeline_data.get("registrations", {}).get("rows", [])
    gs_rows: list[dict] = pipeline_data.get("gs_forms", {}).get("rows", [])

    progress_callback("Processing visit data…")

    # ── Single pass over visits: attribution, GPS distances, EBF%, mother sets ──
    # Sort chronologically in-place (no copy) so last-write-wins attribution is correct.
    visits_rows.sort(key=lambda r: r.get("visit_datetime") or "")

    # When visits_agg rows are available and no per-visit date filter is active
    # (Tab 1), use pre-aggregated SQL counts for num_mothers/bf_count/ebf_count
    # instead of building Python sets and scanning bf_status on every row.
    use_agg_counts = bool(visits_agg_rows) and not task_filters

    mother_to_flw: dict[str, str] = {}
    visits_by_mother: dict[str, dict[str, str]] = {}  # mid → {form_name → date} post-trigger only
    visits_by_mother_all: dict[str, dict[str, str]] = {}  # mid → {form_name → earliest date} all visits
    gps_distances: dict[str, list[float]] = {}
    visit_durations: dict[str, list[float]] = {}
    inter_visit_gaps: dict[str, list[float]] = {}
    last_visit_end: dict[str, str] = {}  # username → ts_end of most recent processed visit
    visits_completed_by_flw: dict[str, int] = {}
    ebf_count_by_flw: dict[str, int] = {}
    bf_count_by_flw: dict[str, int] = {}
    mother_sets_by_flw: dict[str, set] = {}  # used for eligible_mothers_visited regardless of use_agg_counts
    num_mothers_by_flw: dict[str, int] = {}  # only populated when use_agg_counts
    anc_ok_mothers: set[str] = set()  # mothers with antenatal_visit_completion == "ok"

    if use_agg_counts:
        for row in visits_agg_rows:
            u = (row.get("username") or row.get("_username") or "").lower()
            if not u:
                continue
            try:
                num_mothers_by_flw[u] = int(float(row.get("num_mothers") or 0))
            except (TypeError, ValueError):
                pass
            try:
                bf_count_by_flw[u] = int(float(row.get("bf_count") or 0))
            except (TypeError, ValueError):
                pass
            try:
                ebf_count_by_flw[u] = int(float(row.get("ebf_count") or 0))
            except (TypeError, ValueError):
                pass

    total_visits = len(visits_rows)
    for visit_idx, row in enumerate(visits_rows):
        if visit_idx > 0 and visit_idx % 5000 == 0:
            progress_callback(f"Processing visits ({visit_idx:,}/{total_visits:,})…")
        username = (row.get("username") or row.get("_username") or "").lower()
        if not username:
            continue

        vdt = (row.get("visit_datetime") or "")[:10]
        mid = (row.get("mother_case_id") or "").lower()
        # Normalize form name to match visit_type keys produced by mbw_visit_schedules extractor
        raw_form_name = (row.get("form_name") or "").strip()
        form_name = _FORM_NAME_ALIASES.get(raw_form_name, raw_form_name)

        # Always update attribution and all-time visit history (needed for baseline follow-up
        # rate computation even when task_filters skips this visit below).
        if mid:
            mother_to_flw[mid] = username
            if form_name and vdt:
                mid_dict = visits_by_mother_all.setdefault(mid, {})
                if form_name not in mid_dict or vdt < mid_dict[form_name]:
                    mid_dict[form_name] = vdt  # keep earliest visit date per (mother, visit_type)
            if (row.get("antenatal_visit_completion") or "").strip() == "ok":
                anc_ok_mothers.add(mid)

        # For Tab 2: skip visits submitted before the task trigger date
        if task_filters and username in task_filters:
            trigger = (task_filters[username] or "")[:10]
            if vdt and trigger and vdt < trigger:
                continue

        visits_completed_by_flw[username] = visits_completed_by_flw.get(username, 0) + 1

        if mid:
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

        ts_start = row.get("time_start") or ""
        ts_end = row.get("visit_datetime") or ""
        if ts_start and ts_end:
            try:
                t0 = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
                mins = (t1 - t0).total_seconds() / 60
                if 0 < mins < 300:  # sanity: 0–5 hours
                    visit_durations.setdefault(username, []).append(mins)
                # Inter-visit gap: time from previous visit end to this visit start.
                # visits_rows is sorted chronologically so last_visit_end is always
                # the immediately preceding visit for this FLW.
                prev_end = last_visit_end.get(username)
                if prev_end:
                    try:
                        t_prev = datetime.fromisoformat(prev_end.replace("Z", "+00:00"))
                        gap_mins = (t0 - t_prev).total_seconds() / 60
                        if 0 < gap_mins < 480:  # sanity: 0–8 hours (cross-day gaps excluded)
                            inter_visit_gaps.setdefault(username, []).append(gap_mins)
                    except (ValueError, TypeError):
                        pass
                last_visit_end[username] = ts_end
            except (ValueError, TypeError):
                pass

        if not use_agg_counts:
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

    now = (
        date.fromisoformat(job_config["current_date"])
        if job_config.get("current_date")
        else datetime.now(tz=timezone.utc).date()
    )
    grace_cutoff = now - timedelta(days=_GRACE_PERIOD_DAYS)

    # flw_fu[username] = {total_eligible, filtered_completed, filtered_denominator, still_eligible}
    flw_fu: dict[str, dict] = {}

    for mid, schedules in mother_schedules.items():
        flw = mother_to_flw.get(mid)
        if not flw or (active_usernames and flw not in active_usernames):
            continue

        is_eligible = mother_eligibility.get(mid, False) and mid in anc_ok_mothers
        mother_visits = visits_by_mother.get(mid, {})

        bucket = flw_fu.setdefault(
            flw,
            {
                "total_eligible": 0,
                "filtered_completed": 0,
                "filtered_denominator": 0,
                "still_eligible": 0,
            },
        )

        if is_eligible:
            bucket["total_eligible"] += 1

        missed_count = 0
        for s in schedules:
            if not isinstance(s, dict):
                continue
            visit_type = s.get("visit_type", "")
            # ANC visit is already a denominator condition — skip it here
            if visit_type == "ANC Visit":
                continue
            scheduled_str = s.get("visit_date_scheduled") or ""
            expiry_str = s.get("visit_expiry_date") or ""
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

    # ── Baseline follow-up rates at trigger time (Tab 2 only) ──
    # For each FLW in task_filters, compute the follow-up rate as of their trigger date
    # using only visits submitted before that date. This gives the "rate at trigger time"
    # shown in the Tab 2 parenthetical: e.g. "86% (▲ from 82%)".
    baseline_followup_rates: dict[str, int | None] = {}
    if task_filters:
        for flw_username, triggered_at_str in task_filters.items():
            trigger_date_str = (triggered_at_str or "")[:10]
            if not trigger_date_str:
                continue
            try:
                trigger_date = date.fromisoformat(trigger_date_str)
            except ValueError:
                continue
            trigger_grace_cutoff = trigger_date - timedelta(days=_GRACE_PERIOD_DAYS)

            baseline_completed = 0
            baseline_denominator = 0

            for mid, schedules in mother_schedules.items():
                if mother_to_flw.get(mid) != flw_username:
                    continue
                if not (mother_eligibility.get(mid, False) and mid in anc_ok_mothers):
                    continue

                mother_visits = visits_by_mother_all.get(mid, {})

                for s in schedules:
                    if not isinstance(s, dict):
                        continue
                    visit_type = s.get("visit_type", "")
                    if visit_type == "ANC Visit":
                        continue
                    scheduled_str = s.get("visit_date_scheduled") or ""

                    visit_date = mother_visits.get(visit_type, "")
                    is_completed_at_trigger = bool(visit_date) and visit_date[:10] <= trigger_date_str

                    past_grace = False
                    if scheduled_str:
                        try:
                            sched = date.fromisoformat(scheduled_str[:10])
                            past_grace = sched <= trigger_grace_cutoff
                        except (ValueError, TypeError):
                            pass

                    if past_grace:
                        baseline_denominator += 1
                        if is_completed_at_trigger:
                            baseline_completed += 1

            baseline_followup_rates[flw_username] = (
                round(baseline_completed / baseline_denominator * 100)
                if baseline_denominator > 0
                else None
            )

    # ── Previous run categories ──
    progress_callback("Loading previous run data…")
    prev_categories: dict = {}
    if workflow_definition_id and opportunity_id and access_token:
        prev_categories = _get_prev_categories(access_token, opportunity_id, workflow_definition_id)

    # ── Open tasks across all runs ──
    progress_callback("Loading open tasks…")
    open_tasks: dict = {}
    open_tasks_debug = "skipped (no opportunity_id or access_token)"
    if opportunity_id and access_token:
        open_tasks, open_tasks_debug = _get_open_tasks(access_token, opportunity_id, progress_callback)
        progress_callback(f"Found {len(open_tasks)} open task(s) across {len(open_tasks)} FLW(s).")

    # ── Build FLW summaries ──
    progress_callback("Building FLW summaries…")

    target_usernames = active_usernames or set(mother_to_flw.values())
    flw_summaries = []

    for username in sorted(target_usernames):
        u = username.lower()
        fu = flw_fu.get(u, {})
        dists = gps_distances.get(u, [])

        # Mother counts
        mothers_visited = mother_sets_by_flw.get(u, set())
        num_mothers = len(mothers_visited)
        total_eligible = fu.get("total_eligible", 0)
        eligible_mothers_visited = sum(1 for mid in mothers_visited if mother_eligibility.get(mid, False))
        visits_completed = visits_completed_by_flw.get(u, 0)

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

        # Visit duration (median minutes per visit)
        durations = visit_durations.get(u, [])
        if durations:
            sorted_dur = sorted(durations)
            minute_per_visit = round(sorted_dur[len(sorted_dur) // 2])
        else:
            minute_per_visit = None

        # Inter-visit travel time (median minutes between end of one visit and start of next)
        gaps = inter_visit_gaps.get(u, [])
        if gaps:
            sorted_gaps = sorted(gaps)
            travel_time = round(sorted_gaps[len(sorted_gaps) // 2])
        else:
            travel_time = None

        flw_summaries.append(
            {
                "username": u,
                "display_name": flw_names.get(u) or flw_names.get(username) or u,
                "num_mothers": num_mothers,
                "num_mothers_eligible": total_eligible,
                "num_eligible_mothers_visited": eligible_mothers_visited,
                "visits_completed": visits_completed,
                "gs_score": gs_score,
                "followup_rate": followup_rate,
                "followup_rate_denom": denom,
                "followup_rate_at_trigger": baseline_followup_rates.get(u) if task_filters else None,
                "pct_still_eligible": pct_still_eligible,
                "ebf_pct": ebf_pct,
                "ebf_denom": bf_count,
                "revisit_dist": revisit_m,
                "gps_denom": len(dists),
                "meter_per_visit": meter_per_visit,
                "dist_ratio": dist_ratio,
                "minute_per_visit": minute_per_visit,
                "duration_denom": len(durations),
                "travel_time": travel_time,
                "travel_time_denom": len(gaps),
            }
        )

    return {
        "flw_summaries": flw_summaries,
        "prev_categories": prev_categories,
        "open_tasks": open_tasks,
        "open_tasks_debug": open_tasks_debug,
    }
