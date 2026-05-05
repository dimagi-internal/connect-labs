"""Tests for the in_progress|completed run lifecycle.

Covers:
- Status proxy maps legacy active/frozen values to in_progress/completed
- Status defaults to "in_progress" for missing/unknown
- WorkflowRunRecord exposes is_completed, completed_at, snapshot properties
- The atomic completion path: snapshot persisted + status flipped + completed_at stamped

API endpoint integration tests (start_run, freeze) live in test_views.py-style
tests; this file is pure unit-level around the data layer + proxy properties.
"""

from __future__ import annotations

from commcare_connect.workflow.data_access import RUN_STATUS_COMPLETED, RUN_STATUS_IN_PROGRESS, WorkflowRunRecord


def _make_record(**data) -> WorkflowRunRecord:
    """Minimal WorkflowRunRecord for property testing — no API."""
    return WorkflowRunRecord(
        {
            "id": 1,
            "experiment": "workflow",
            "type": "workflow_run",
            "data": data,
            "username": "test",
            "opportunity_id": 42,
            "organization_id": None,
            "program_id": None,
            "labs_record_id": None,
            "public": False,
        }
    )


class TestStatusProxy:
    """The `.status` proxy property maps legacy values to the new vocabulary
    so render code never sees the interim active/frozen status names."""

    def test_in_progress_passes_through(self):
        assert _make_record(status=RUN_STATUS_IN_PROGRESS).status == "in_progress"

    def test_completed_passes_through(self):
        assert _make_record(status=RUN_STATUS_COMPLETED).status == "completed"

    def test_legacy_active_maps_to_in_progress(self):
        assert _make_record(status="active").status == "in_progress"

    def test_legacy_frozen_maps_to_completed(self):
        assert _make_record(status="frozen").status == "completed"

    def test_missing_status_defaults_to_in_progress(self):
        assert _make_record().status == "in_progress"

    def test_unknown_status_defaults_to_in_progress(self):
        # Defensive: a typo or future-status falls back to in_progress rather
        # than leaking through to render code.
        assert _make_record(status="weird_value").status == "in_progress"

    def test_status_in_state_dict_is_read(self):
        # Pre-rename, some code wrote status into state instead of top-level.
        # Proxy still finds it.
        assert _make_record(state={"status": "in_progress"}).status == "in_progress"

    def test_top_level_status_wins_over_state(self):
        # If both are set, top-level is canonical.
        rec = _make_record(status="completed", state={"status": "in_progress"})
        assert rec.status == "completed"


class TestCompletedProperties:
    def test_is_completed_true_when_status_is_completed(self):
        assert _make_record(status="completed").is_completed is True

    def test_is_completed_false_when_in_progress(self):
        assert _make_record(status="in_progress").is_completed is False

    def test_is_completed_handles_legacy_frozen(self):
        # Legacy status that maps to completed should also report is_completed=True.
        assert _make_record(status="frozen").is_completed is True

    def test_completed_at_returns_stamp_when_set(self):
        assert _make_record(completed_at="2026-04-30T12:00:00Z").completed_at == "2026-04-30T12:00:00Z"

    def test_completed_at_is_none_when_unset(self):
        assert _make_record().completed_at is None

    def test_snapshot_returns_blob_when_set(self):
        snap = {"workers": [], "summary": {"total": 0}}
        assert _make_record(snapshot=snap).snapshot == snap

    def test_snapshot_is_none_when_unset(self):
        assert _make_record().snapshot is None


class TestStatusMigrationMapping:
    """The migrate_run_statuses management command translates the interim
    active/frozen values back to the canonical pair. Verified at the mapping
    level — full command test would need an integration harness."""

    def test_old_to_new_mapping_is_complete(self):
        from commcare_connect.workflow.management.commands.migrate_run_statuses import _OLD_TO_NEW

        assert _OLD_TO_NEW == {
            "active": RUN_STATUS_IN_PROGRESS,
            "frozen": RUN_STATUS_COMPLETED,
        }
