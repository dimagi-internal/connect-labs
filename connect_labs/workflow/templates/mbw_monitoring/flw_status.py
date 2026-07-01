"""
Latest-known FLW assessment status lookup.

Shared between V1's MBWMonitoringStreamView (which calls this inline before
running the dashboard) and the V2 job handler (which calls this when the FE
job_config doesn't carry pre-populated flw_statuses).

V2 originally read `instance.state?.flw_statuses` which was empty until a
user clicked through assessments — meaning Performance-by-Status started
empty on every fresh run. V1 always populated it from audit sessions +
workflow run state, which is what this helper does.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_latest_flw_statuses(
    request,
    active_usernames: set[str],
) -> dict[str, str]:
    """Get the latest known assessment status for each FLW.

    Checks two sources (same logic as flw_api._build_flw_history):
    1. Traditional audit sessions (AuditDataAccess)
    2. All workflow monitoring runs (flw_results in run state)

    Returns dict mapping username (lowercase) → status key.
    FLWs in active_usernames with no assessment get "none".

    Failures in either source are logged but do not raise — partial data
    is preferred over an empty result.
    """
    # Track latest per FLW: {username: (date_str, result)}
    latest: dict[str, tuple[str, str]] = {}

    def _update(uname: str, date_str: str, result: str):
        uname = uname.lower()
        prev = latest.get(uname)
        if prev is None or (date_str and date_str > prev[0]):
            latest[uname] = (date_str or "", result)

    # 1. Traditional audit sessions
    try:
        from connect_labs.audit.data_access import AuditDataAccess

        audit_access = AuditDataAccess(request=request)
        try:
            for session in audit_access.get_audit_sessions():
                username = session.flw_username
                result = session.overall_result
                if not username or not result:
                    continue
                session_date = session.data.get("created_at") or session.data.get("start_date") or ""
                _update(username, session_date, result.lower())
        finally:
            audit_access.close()
    except Exception as e:
        logger.warning("[FLW Status] Failed to fetch audit sessions: %s", e)

    # 2. All workflow monitoring runs (including in-progress)
    try:
        from connect_labs.workflow.data_access import WorkflowDataAccess

        wf_access = WorkflowDataAccess(request=request)
        try:
            for run in wf_access.list_runs():
                state = run.data.get("state", {})
                flw_results = state.get("worker_results", state.get("flw_results", {}))
                for username, result_data in flw_results.items():
                    if not isinstance(result_data, dict):
                        continue
                    result = result_data.get("result")
                    if not result:
                        continue
                    _update(username, result_data.get("assessed_at", ""), result)
        finally:
            wf_access.close()
    except Exception as e:
        logger.warning("[FLW Status] Failed to fetch workflow runs: %s", e)

    # Build final mapping: all active usernames get a status
    return {username: latest[username][1] if username in latest else "none" for username in active_usernames}
