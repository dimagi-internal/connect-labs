"""Monitoring pipeline orchestrator: canonical visits → dashboard payload.

Composes the pure stages (normalize → derive → rollups → duration → gps_issue)
into one call that returns a JSON-able dashboard payload. The data source
(Connect's export API) is injected as a DataFrame so the pipeline stays pure
and testable; see ingest.py for the live fetch.
"""

from __future__ import annotations

import pandas as pd

from connect_labs.microplans.monitoring.derive import add_attempt_index, derive_attempt_flags
from connect_labs.microplans.monitoring.duration import time_to_completion
from connect_labs.microplans.monitoring.gps_issue import build_gps_issue_report
from connect_labs.microplans.monitoring.normalize import normalize_visits
from connect_labs.microplans.monitoring.rollups import build_cluster_rollup, build_enum_daily


def compute_monitoring(raw_visits: pd.DataFrame, field_map: dict | None = None) -> dict:
    """raw_visits: a DataFrame of Connect/form rows. Returns a dashboard payload."""
    canonical = normalize_visits(raw_visits, field_map)
    flagged = add_attempt_index(derive_attempt_flags(canonical))

    cluster_rollup = build_cluster_rollup(flagged)
    enum_daily = build_enum_daily(flagged)
    gps_issues = build_gps_issue_report(flagged)

    total_attempts = len(flagged)
    completed = int(flagged["completed"].sum()) if total_attempts else 0
    reached = int(flagged["reached_le15"].sum()) if total_attempts else 0
    gps_issue = int(flagged["proceed_when_believed"].sum()) if total_attempts else 0

    by_arm = {}
    if "arm" in flagged.columns and total_attempts:
        for arm, sub in flagged.groupby("arm"):
            by_arm[str(arm)] = {
                "attempts": int(len(sub)),
                "completed": int(sub["completed"].sum()),
                "reached_le15": int(sub["reached_le15"].sum()),
            }

    return {
        "totals": {
            "attempts": total_attempts,
            "completed": completed,
            "reached_le15": reached,
            "gps_issue": gps_issue,
            "gps_accuracy_rate": round(100.0 * reached / total_attempts, 1) if total_attempts else 0.0,
            "completion_rate": round(100.0 * completed / total_attempts, 1) if total_attempts else 0.0,
        },
        "by_arm": by_arm,
        "cluster_rollup": cluster_rollup.to_dict("records") if not cluster_rollup.empty else [],
        "enum_daily": enum_daily.to_dict("records") if not enum_daily.empty else [],
        "time_to_completion": time_to_completion(canonical),
        "gps_issues": gps_issues.to_dict("records") if not gps_issues.empty else [],
    }
