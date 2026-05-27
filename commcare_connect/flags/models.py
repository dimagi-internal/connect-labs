"""Proxy model for Flag-type LocalLabsRecords.

A Flag records "the system (or, less commonly, a human) observed a finding
on this FLW during a workflow run." Findings are derived from the metrics;
each is its own record, so a (run, flw) pair can carry zero or many flags.

Flags are NOT judgments and do not carry audit/task linkage — those live on
the Audit/Task records themselves, scoped by workflow_run_id.
"""

from commcare_connect.labs.models import LocalLabsRecord


class FlagRecord(LocalLabsRecord):
    """Proxy model for Flag-type LabsRecords."""

    @property
    def workflow_run_id(self) -> int | None:
        return self.data.get("workflow_run_id")

    @property
    def flw_id(self) -> str | None:
        return self.data.get("flw_id")

    @property
    def flag_key(self) -> str:
        """Stable identifier for this finding (e.g. 'sam_low', 'gender_skew')."""
        return self.data.get("flag_key", "")

    @property
    def flag_label(self) -> str:
        """Human-readable label rendered in the UI pill."""
        return self.data.get("flag_label", "")

    @property
    def evidence(self) -> dict:
        """Snapshot of the metric values that triggered this flag."""
        return self.data.get("evidence", {})

    @property
    def source(self) -> str:
        """'auto' (computed from data) or 'manual' (human-added)."""
        return self.data.get("source", "auto")

    @property
    def flagged_at(self) -> str | None:
        return self.data.get("flagged_at")

    @property
    def flagged_by(self) -> str | None:
        return self.data.get("flagged_by")
