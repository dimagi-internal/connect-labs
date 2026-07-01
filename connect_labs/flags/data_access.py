"""Data access layer for Flags.

Mirrors the pattern in connect_labs/tasks/data_access.py:
- Wraps LabsRecordAPIClient for read/write to production Connect's LabsRecord API
- Validates inputs at the boundary (flag_key required, flw_id required)
- Returns typed FlagRecord proxies

A Flag is a finding (auto or manual) attached to one FLW within one
workflow run. Multiple flags can exist for the same (run, flw); the
framework dedups by (workflow_run_id, flw_id, flag_key) on writes from
view.ensureAutoFlags so calling it on every render is safe.
"""

from datetime import datetime, timezone

from connect_labs.flags.models import FlagRecord
from connect_labs.workflow.data_access import BaseDataAccess

ALLOWED_SOURCES = ("auto", "manual")


class FlagsDataAccess(BaseDataAccess):
    """Data access for Flag LabsRecords."""

    # ---- write ---------------------------------------------------------

    def create_flag(
        self,
        *,
        workflow_run_id: int,
        opportunity_id: int,
        flw_id: str,
        flag_key: str,
        flag_label: str | None = None,
        evidence: dict | None = None,
        source: str = "auto",
        flagged_by: str | None = None,
        flagged_at: str | None = None,
    ) -> FlagRecord:
        """Create a new Flag. One record per (run, flw, flag_key)."""
        if not flw_id or not flw_id.strip():
            raise ValueError("flw_id is required")
        if not flag_key or not flag_key.strip():
            raise ValueError("flag_key is required")
        if source not in ALLOWED_SOURCES:
            raise ValueError(f"source must be one of {ALLOWED_SOURCES}, got {source!r}")

        data = {
            "workflow_run_id": workflow_run_id,
            "opportunity_id": opportunity_id,
            "flw_id": flw_id,
            "flag_key": flag_key,
            "flag_label": flag_label or flag_key,
            "evidence": evidence or {},
            "source": source,
            "flagged_at": flagged_at or datetime.now(timezone.utc).isoformat(),
            "flagged_by": flagged_by,
        }

        record = self.labs_api.create_record(
            experiment="flags",
            type="Flag",
            data=data,
            username=flw_id,
        )
        return FlagRecord(
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

    def get_flag(self, flag_id: int) -> FlagRecord | None:
        """Get one Flag by id, or None if not found."""
        return self.labs_api.get_record_by_id(
            record_id=flag_id,
            experiment="flags",
            type="Flag",
            model_class=FlagRecord,
        )

    def get_flags_for_run(self, workflow_run_id: int) -> list[FlagRecord]:
        """All Flags attached to the given workflow run.

        Filters server-side via the JSONField lookup on ``data.workflow_run_id``
        (same mechanism as ``TaskDataAccess.get_tasks_for_run``).
        """
        return self.labs_api.get_records(
            experiment="flags",
            type="Flag",
            model_class=FlagRecord,
            workflow_run_id=workflow_run_id,
        )
