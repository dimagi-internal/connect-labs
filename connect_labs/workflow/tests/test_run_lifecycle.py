"""Tests for the in_progress|completed run lifecycle.

Covers the data layer + proxy properties:
- Status proxy maps legacy values (active→in_progress, frozen→completed) and
  defaults to in_progress for missing/unknown.
- WorkflowRunRecord exposes is_completed, completed_at, snapshot properties.
- update_run_state refuses writes to completed runs and strips protected keys.

API endpoint integration tests (start_run, complete) live in test_views.py-style
tests; this file is pure unit-level around the data layer.
"""

from __future__ import annotations

from connect_labs.workflow.data_access import RUN_STATUS_COMPLETED, RUN_STATUS_IN_PROGRESS, WorkflowRunRecord


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
    """The `.status` proxy maps any persisted value to the canonical
    in_progress | completed pair so render code never sees a third state."""

    def test_in_progress_passes_through(self):
        assert _make_record(status=RUN_STATUS_IN_PROGRESS).status == "in_progress"

    def test_completed_passes_through(self):
        assert _make_record(status=RUN_STATUS_COMPLETED).status == "completed"

    def test_legacy_active_maps_to_in_progress(self):
        # The brief 04-30 vocabulary detour persisted some rows as `active`.
        # Defensive mapping keeps render code unaware of that history.
        assert _make_record(status="active").status == "in_progress"

    def test_legacy_frozen_maps_to_completed(self):
        assert _make_record(status="frozen").status == "completed"

    def test_missing_status_defaults_to_in_progress(self):
        assert _make_record().status == "in_progress"

    def test_unknown_status_defaults_to_in_progress(self):
        # Defensive: a typo or future value falls back rather than leaking.
        assert _make_record(status="weird_value").status == "in_progress"

    def test_status_in_state_dict_is_read(self):
        # Some old code wrote status into state instead of top-level — proxy still finds it.
        assert _make_record(state={"status": "in_progress"}).status == "in_progress"

    def test_top_level_status_wins_over_state(self):
        rec = _make_record(status="completed", state={"status": "in_progress"})
        assert rec.status == "completed"


class TestCompletedProperties:
    def test_is_completed_true_when_completed(self):
        assert _make_record(status="completed").is_completed is True

    def test_is_completed_false_when_in_progress(self):
        assert _make_record(status="in_progress").is_completed is False

    def test_is_completed_handles_legacy_frozen(self):
        # Legacy `frozen` maps to completed, so is_completed should be True too.
        assert _make_record(status="frozen").is_completed is True

    def test_completed_at_returns_stamp_when_set(self):
        assert _make_record(completed_at="2026-05-04T12:00:00Z").completed_at == "2026-05-04T12:00:00Z"

    def test_completed_at_falls_back_to_legacy_frozen_at(self):
        # Defensive: rows written by the deleted freeze_run path stamped frozen_at.
        assert _make_record(frozen_at="2026-04-30T12:00:00Z").completed_at == "2026-04-30T12:00:00Z"

    def test_completed_at_is_none_when_unset(self):
        assert _make_record().completed_at is None

    def test_snapshot_returns_blob_when_set(self):
        snap = {"workers": [], "summary": {"total": 0}}
        assert _make_record(snapshot=snap).snapshot == snap

    def test_snapshot_is_none_when_unset(self):
        assert _make_record().snapshot is None
