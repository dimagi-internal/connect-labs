"""Audit PAR — multi-opp + saved-runs program report over weekly dual-track audits.

Rolls up the weekly_dual_track_audit creator's runs into a week x opp grid of
audit results (MUAC census + sampled remainder), drillable to per-FLW audits.
See docs/superpowers/specs/2026-06-30-audit-program-report-design.md.
"""

import logging

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
