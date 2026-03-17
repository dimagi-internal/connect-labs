"""
Adapter to make WorkflowRunRecord look like a monitoring session for the MBW dashboard.

The dashboard was built to consume AuditSessionRecord-like objects with properties
like selected_flw_usernames, flw_results, get_monitoring_progress_stats(), etc.
This adapter wraps a WorkflowRunRecord and exposes the same interface.
"""

import logging
from datetime import datetime, timezone

from commcare_connect.workflow.data_access import WorkflowDataAccess, WorkflowRunRecord

logger = logging.getLogger(__name__)

VALID_FLW_RESULTS = ("eligible_for_renewal", "probation", "suspended")


class WorkflowMonitoringSession:
    """Adapter wrapping WorkflowRunRecord for the MBW dashboard."""

    def __init__(self, run):
        """
        Args:
            run: WorkflowRunRecord from WorkflowDataAccess.get_run()
        """
        self.run = run
        self._state = run.data.get("state", {})

    @property
    def id(self):
        return self.run.id

    @property
    def pk(self):
        return self.run.id

    @property
    def title(self):
        return self._state.get("title", "MBW Monitoring")

    @property
    def tag(self):
        return self._state.get("tag", "")

    @property
    def status(self):
        return self.run.data.get("status", "in_progress")

    @property
    def overall_result(self):
        return self._state.get("overall_result")

    @property
    def session_type(self):
        return "mbw_monitoring"

    @property
    def is_monitoring(self):
        return True

    @property
    def opportunity_id(self):
        return self.run.data.get("opportunity_id") or self.run.opportunity_id

    @property
    def opportunity_name(self):
        return self._state.get("opportunity_name", "")

    @property
    def selected_flw_usernames(self):
        return self._state.get("selected_workers", self._state.get("selected_flws", []))

    @property
    def flw_results(self):
        return self._state.get("worker_results", self._state.get("flw_results", {}))

    @property
    def gs_app_id(self):
        return self._state.get("gs_app_id")

    @property
    def dashboard_snapshot(self):
        """Return the stored dashboard snapshot, or None if not yet captured."""
        return self.run.data.get("snapshot")

    @property
    def snapshot_timestamp(self):
        """Return the ISO timestamp of the last snapshot, or None."""
        snapshot = self.dashboard_snapshot
        return snapshot.get("timestamp") if snapshot else None

    @property
    def description(self):
        count = len(self.selected_flw_usernames)
        return f"MBW Monitoring: {count} FLWs"

    @property
    def data(self):
        """Compatibility: some code reads .data directly."""
        return {
            "session_type": "mbw_monitoring",
            "title": self.title,
            "tag": self.tag,
            "status": self.status,
            "selected_flw_usernames": self.selected_flw_usernames,
            "flw_results": self.flw_results,
            "opportunity_id": self.opportunity_id,
            "opportunity_name": self.opportunity_name,
            "overall_result": self.overall_result,
        }

    def get_flw_result(self, username):
        return self.flw_results.get(username)

    def get_monitoring_progress_stats(self):
        total = len(self.selected_flw_usernames)
        assessed = sum(1 for u in self.selected_flw_usernames if self.flw_results.get(u, {}).get("result"))
        percentage = round((assessed / total) * 100) if total > 0 else 0
        return {"percentage": percentage, "assessed": assessed, "total": total}

    def to_summary_dict(self):
        """Same output as AuditSessionRecord.to_summary_dict() for monitoring."""
        return {
            "id": self.id,
            "title": self.title,
            "tag": self.tag,
            "status": self.status,
            "overall_result": self.overall_result,
            "session_type": "mbw_monitoring",
            "opportunity_id": self.opportunity_id,
            "opportunity_name": self.opportunity_name,
            "monitoring_progress": self.get_monitoring_progress_stats(),
            "selected_flw_usernames": self.selected_flw_usernames,
            "run_id": self.id,
        }


def load_monitoring_run(request, run_id):
    """Load a WorkflowRunRecord and wrap it as WorkflowMonitoringSession.

    Args:
        request: HttpRequest (for OAuth token)
        run_id: WorkflowRun ID (int)

    Returns:
        WorkflowMonitoringSession or None if not found / not a monitoring run
    """
    data_access = None
    try:
        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(int(run_id))

        if not run:
            return None

        # Verify it's a monitoring run (has selected_flws in state)
        state = run.data.get("state", {})
        if "selected_flws" not in state and "selected_workers" not in state:
            logger.warning(f"Run {run_id} is not a monitoring session (no selected_flws/selected_workers)")
            return None

        return WorkflowMonitoringSession(run)
    except Exception as e:
        logger.warning(f"Failed to load monitoring run {run_id}: {e}")
        return None
    finally:
        if data_access:
            data_access.close()


def save_flw_result(request, run_id, username, result, notes, assessed_by):
    """Save a FLW assessment result to the workflow run state.

    Uses update_run_state() which does a SHALLOW merge on state.
    We must pass the entire flw_results dict to avoid losing other FLWs.

    Args:
        request: HttpRequest
        run_id: WorkflowRun ID
        username: FLW username
        result: One of VALID_FLW_RESULTS or None
        notes: Assessment notes
        assessed_by: User ID of assessor

    Returns:
        WorkflowMonitoringSession or None on failure
    """
    data_access = None
    try:
        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(int(run_id))
        if not run:
            return None

        current_state = run.data.get("state", {})
        current_results = current_state.get("worker_results", current_state.get("flw_results", {}))

        # Build updated flw_results (full dict, not just the changed entry)
        updated_results = {
            **current_results,
            username: {
                "result": result,
                "notes": notes,
                "assessed_by": assessed_by,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        # Shallow merge: pass entire worker_results dict
        updated_run = data_access.update_run_state(
            run_id,
            {
                "worker_results": updated_results,
            },
            run=run,
        )

        if updated_run:
            return WorkflowMonitoringSession(updated_run)
        return None
    except Exception as e:
        logger.error(f"Failed to save FLW result for run {run_id}: {e}", exc_info=True)
        return None
    finally:
        if data_access:
            data_access.close()


def complete_monitoring_run(request, run_id, overall_result="completed", notes=""):
    """Mark a monitoring workflow run as completed.

    Since WorkflowDataAccess has no complete_run() method, we update run.data.status
    directly via labs_api.update_record().

    Args:
        request: HttpRequest
        run_id: WorkflowRun ID
        overall_result: Completion result string
        notes: Completion notes

    Returns:
        WorkflowMonitoringSession or None on failure
    """
    data_access = None
    try:
        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(int(run_id))
        if not run:
            return None

        # Update status at top level + store result/notes in state
        current_state = run.data.get("state", {})
        updated_data = {
            **run.data,
            "status": "completed",
            "state": {
                **current_state,
                "overall_result": overall_result,
                "notes": notes,
            },
        }

        # Trim snapshot: remove flw_drilldown on completion (200KB vs 2.2MB)
        snapshot = updated_data.get("snapshot")
        if snapshot and "followup_data" in snapshot:
            followup = snapshot["followup_data"]
            if "flw_drilldown" in followup:
                del followup["flw_drilldown"]

        result = data_access.labs_api.update_record(
            record_id=run_id,
            experiment=data_access.EXPERIMENT,
            type="workflow_run",
            data=updated_data,
        )

        if result:
            updated_run = WorkflowRunRecord(
                {
                    "id": result.id,
                    "experiment": result.experiment,
                    "type": result.type,
                    "data": result.data,
                    "opportunity_id": result.opportunity_id,
                }
            )
            return WorkflowMonitoringSession(updated_run)
        return None
    except Exception as e:
        logger.error(f"Failed to complete monitoring run {run_id}: {e}", exc_info=True)
        return None
    finally:
        if data_access:
            data_access.close()


def save_dashboard_snapshot(request, run_id, snapshot_data):
    """Save computed dashboard data as a snapshot on the workflow run.

    Writes to run.data["snapshot"] (sibling of "state", not inside it).

    Args:
        request: HttpRequest
        run_id: WorkflowRun ID
        snapshot_data: Dict containing gps_data, followup_data, overview_data, etc.

    Returns:
        True on success, False on failure
    """
    data_access = None
    try:
        data_access = WorkflowDataAccess(request=request)
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **snapshot_data,
        }
        result = data_access.save_run_snapshot(int(run_id), snapshot)
        return result is not None
    except Exception as e:
        logger.error(f"Failed to save dashboard snapshot for run {run_id}: {e}", exc_info=True)
        return False
    finally:
        if data_access:
            data_access.close()
