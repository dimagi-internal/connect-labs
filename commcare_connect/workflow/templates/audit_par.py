"""Audit PAR — multi-opp + saved-runs program report over weekly dual-track audits.

Rolls up the weekly_dual_track_audit creator's runs into a week x opp grid of
audit results (MUAC census + sampled remainder), drillable to per-FLW audits.
See docs/superpowers/specs/2026-06-30-audit-program-report-design.md.
"""

import logging

from commcare_connect.audit.data_access import AuditDataAccess
from commcare_connect.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)

_TAGS = ("muac", "rest")


def _empty_tag_summary():
    return {"sessions": 0, "pass": 0, "fail": 0, "pending": 0, "ai_flagged": 0}


def summarize_run_sessions(sessions, opportunity_id):
    """Roll one creator run's audit sessions (for one opp) into tag summaries +
    per-FLW rows. See plan Task 4 Interfaces for the return shape.

    Each cell in flw_rows carries a ``session_id`` for deep-linking to
    /audit/<session_id>/bulk/.
    """
    by_tag = {t: _empty_tag_summary() for t in _TAGS}
    rows = {}

    for s in sessions:
        if s.opportunity_id != opportunity_id:
            continue
        tag = s.tag if s.tag in _TAGS else None
        if tag is None:
            continue
        stats = s.get_assessment_stats() or {}
        cell = {
            "pass": stats.get("pass", 0),
            "fail": stats.get("fail", 0),
            "pending": stats.get("pending", 0),
            "ai_flagged": stats.get("ai_no_match", 0),
            "status": s.status,
            "session_id": s.id,
        }
        agg = by_tag[tag]
        agg["sessions"] += 1
        agg["pass"] += cell["pass"]
        agg["fail"] += cell["fail"]
        agg["pending"] += cell["pending"]
        agg["ai_flagged"] += cell["ai_flagged"]

        flw_id = s.flw_username or "unknown"
        row = rows.setdefault(flw_id, {"flw_id": flw_id, "flw_name": flw_id, "muac": None, "rest": None})
        name = getattr(s, "flw_display_name", None)
        if name and name != flw_id:
            row["flw_name"] = name
        row[tag] = cell

    return {"by_tag": by_tag, "flw_rows": list(rows.values())}


def _in_window(run_ws, win_start, win_end):
    return bool(run_ws) and win_start <= run_ws <= win_end


def compute_audit_par_rollup(*, state, request=None, access_token=None, progress_callback=None):
    """Window-scoped rollup of the creator's weekly runs into per-opp week cells.

    Reads sessions per-opp with an opp-scoped AuditDataAccess (the labs API
    enforces opp scope on every request — a single DAO returns 0 for non-primary
    opps; same lesson as program_admin_report).
    """
    win_start = state.get("window_start")
    win_end = state.get("window_end")
    source = state.get("watched_source") or {}
    if not win_start or not win_end:
        return {"watched_summary": [], "error": "missing_window"}

    creator_def_id = source.get("creator_definition_id")
    opportunity_ids = source.get("opportunity_ids", [])

    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    wda = WorkflowDataAccess(request=request, access_token=access_token)
    try:
        runs = wda.list_runs(creator_def_id) if creator_def_id else []
    finally:
        wda.close()

    # Keep only runs whose batch window falls inside the report window, sorted by week.
    weeks = []
    for run in runs:
        run_state = (run.data or {}).get("state", {})
        rws = run_state.get("window_start")
        if _in_window(rws, win_start, win_end):
            weeks.append((rws, run_state.get("window_end"), run))
    weeks.sort(key=lambda t: t[0])

    watched_summary = []
    for opp_id in opportunity_ids:
        _progress(f"Rolling up opportunity #{opp_id}…")
        ada = AuditDataAccess(request=request, access_token=access_token, opportunity_id=opp_id)
        try:
            opp_weeks = []
            for rws, rwe, run in weeks:
                sessions = ada.get_sessions_by_workflow_run(run.id)
                summary = summarize_run_sessions(sessions, opportunity_id=opp_id)
                opp_weeks.append(
                    {
                        "window_start": rws,
                        "window_end": rwe,
                        "run_id": run.id,
                        "by_tag": summary["by_tag"],
                        "flw_rows": summary["flw_rows"],
                    }
                )
        finally:
            ada.close()
        watched_summary.append({"opportunity_id": opp_id, "weeks": opp_weeks})

    return {
        "watched_summary": watched_summary,
        "window_start": win_start,
        "window_end": win_end,
        "watched_source": source,
    }
