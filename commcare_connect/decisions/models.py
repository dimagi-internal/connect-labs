"""Proxy model for Decision-type LocalLabsRecords.

A Decision records "the network manager looked at this FLW during a workflow
run and concluded X." It can spawn zero or more Tasks/AuditSessions; the
status of those is queried live off the Task/AuditSession itself, never
stored on the Decision. See docs/superpowers/specs/2026-05-25-program-admin-
report-design.md §3 for the contract.
"""

from commcare_connect.labs.models import LocalLabsRecord


class DecisionRecord(LocalLabsRecord):
    """Proxy model for Decision-type LabsRecords."""

    @property
    def workflow_run_id(self) -> int | None:
        return self.data.get("workflow_run_id")

    @property
    def flw_id(self) -> str | None:
        return self.data.get("flw_id")

    @property
    def reason_key(self) -> str | None:
        return self.data.get("reason_key")

    @property
    def reason_label(self) -> str | None:
        return self.data.get("reason_label")

    @property
    def decision_type(self) -> str:
        """One of: 'no_issues', 'action_taken'."""
        return self.data.get("decision_type", "no_issues")

    @property
    def kpi_snapshot(self) -> dict:
        return self.data.get("kpi_snapshot", {})

    @property
    def audit_session_ids(self) -> list[int]:
        return self.data.get("audit_session_ids", [])

    @property
    def task_ids(self) -> list[int]:
        return self.data.get("task_ids", [])

    @property
    def notes(self) -> str | None:
        return self.data.get("notes")

    @property
    def decided_at(self) -> str | None:
        return self.data.get("decided_at")

    @property
    def decided_by(self) -> str | None:
        return self.data.get("decided_by")
