"""Data access layer for Decisions.

Mirrors the pattern in commcare_connect/tasks/data_access.py:
- Wraps LabsRecordAPIClient for read/write to production Connect's LabsRecord API
- Validates inputs at the boundary (decision_type enum, action_taken requires reason)
- Returns typed DecisionRecord proxies
"""

from datetime import datetime, timezone

from commcare_connect.decisions.models import DecisionRecord
from commcare_connect.workflow.data_access import BaseDataAccess

ALLOWED_DECISION_TYPES = ("no_issues", "action_taken")


class DecisionsDataAccess(BaseDataAccess):
    """Data access for Decision LabsRecords."""

    # ---- write ---------------------------------------------------------

    def create_decision(
        self,
        *,
        workflow_run_id: int,
        opportunity_id: int,
        flw_id: str,
        decision_type: str,
        reason_key: str | None = None,
        reason_label: str | None = None,
        kpi_snapshot: dict | None = None,
        audit_session_ids: list[int] | None = None,
        task_ids: list[int] | None = None,
        notes: str | None = None,
        decided_by: str | None = None,
        decided_at: str | None = None,
    ) -> DecisionRecord:
        """Create a new Decision. See spec §3.2 for field semantics."""
        if decision_type not in ALLOWED_DECISION_TYPES:
            raise ValueError(
                f"decision_type must be one of {ALLOWED_DECISION_TYPES}, got {decision_type!r}"
            )
        if not flw_id or not flw_id.strip():
            raise ValueError("flw_id is required")
        if decision_type == "action_taken" and not reason_key:
            raise ValueError("reason_key is required for decision_type='action_taken'")

        data = {
            "workflow_run_id": workflow_run_id,
            "opportunity_id": opportunity_id,
            "flw_id": flw_id,
            "decision_type": decision_type,
            "reason_key": reason_key,
            "reason_label": reason_label,
            "kpi_snapshot": kpi_snapshot or {},
            "audit_session_ids": list(audit_session_ids or []),
            "task_ids": list(task_ids or []),
            "notes": notes,
            "decided_at": decided_at or datetime.now(timezone.utc).isoformat(),
            "decided_by": decided_by,
        }

        record = self.labs_api.create_record(
            experiment="decisions",
            type="Decision",
            data=data,
            username=flw_id,
        )
        return DecisionRecord(
            {
                "id": record.id,
                "experiment": record.experiment,
                "type": record.type,
                "data": record.data or data,
                "username": record.username,
                "opportunity_id": record.opportunity_id,
            }
        )

    # ---- read ----------------------------------------------------------

    def get_decision(self, decision_id: int) -> DecisionRecord | None:
        """Get one Decision by id, or None if not found."""
        return self.labs_api.get_record_by_id(
            record_id=decision_id,
            experiment="decisions",
            type="Decision",
            model_class=DecisionRecord,
        )

    def get_decisions_for_run(self, workflow_run_id: int) -> list[DecisionRecord]:
        """All Decisions created by the given workflow run.

        Filters server-side via the JSONField lookup on ``data.workflow_run_id``
        (same mechanism as ``TaskDataAccess.get_tasks_for_run``).
        """
        return self.labs_api.get_records(
            experiment="decisions",
            type="Decision",
            model_class=DecisionRecord,
            workflow_run_id=workflow_run_id,
        )
