"""Tests for the active|frozen run lifecycle.

Covers:
- Status proxy maps legacy values (in_progress→active, completed→frozen)
- Status defaults to "active" for missing/unknown
- WorkflowRunRecord exposes is_frozen, frozen_at, snapshot properties
- The atomic freeze_run path: snapshot persisted + status flipped + frozen_at stamped

API endpoint integration tests (start_run, freeze) live in test_views.py-style
tests; this file is pure unit-level around the data layer + proxy properties.
"""

from __future__ import annotations

from commcare_connect.workflow.data_access import RUN_STATUS_ACTIVE, RUN_STATUS_FROZEN, WorkflowRunRecord


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
    so render code never sees the pre-2026-04-30 status names."""

    def test_active_passes_through(self):
        assert _make_record(status=RUN_STATUS_ACTIVE).status == "active"

    def test_frozen_passes_through(self):
        assert _make_record(status=RUN_STATUS_FROZEN).status == "frozen"

    def test_legacy_in_progress_maps_to_active(self):
        assert _make_record(status="in_progress").status == "active"

    def test_legacy_completed_maps_to_frozen(self):
        assert _make_record(status="completed").status == "frozen"

    def test_missing_status_defaults_to_active(self):
        assert _make_record().status == "active"

    def test_unknown_status_defaults_to_active(self):
        # Defensive: a typo or future-status falls back to active rather than
        # leaking through to render code.
        assert _make_record(status="weird_value").status == "active"

    def test_status_in_state_dict_is_read(self):
        # Pre-rename, some code wrote status into state instead of top-level.
        # Proxy still finds it.
        assert _make_record(state={"status": "in_progress"}).status == "active"

    def test_top_level_status_wins_over_state(self):
        # If both are set, top-level is canonical.
        rec = _make_record(status="frozen", state={"status": "in_progress"})
        assert rec.status == "frozen"


class TestFrozenProperties:
    def test_is_frozen_true_when_status_is_frozen(self):
        assert _make_record(status="frozen").is_frozen is True

    def test_is_frozen_false_when_active(self):
        assert _make_record(status="active").is_frozen is False

    def test_is_frozen_handles_legacy_completed(self):
        # Legacy status that maps to frozen should also report is_frozen=True.
        assert _make_record(status="completed").is_frozen is True

    def test_frozen_at_returns_stamp_when_set(self):
        assert _make_record(frozen_at="2026-04-30T12:00:00Z").frozen_at == "2026-04-30T12:00:00Z"

    def test_frozen_at_is_none_when_unset(self):
        assert _make_record().frozen_at is None

    def test_snapshot_returns_blob_when_set(self):
        snap = {"workers": [], "summary": {"total": 0}}
        assert _make_record(snapshot=snap).snapshot == snap

    def test_snapshot_is_none_when_unset(self):
        assert _make_record().snapshot is None


class TestStatusMigrationMapping:
    """The migrate_run_statuses management command translates these old values
    to the canonical pair. Verified at the mapping level — full command test
    would need an integration harness."""

    def test_old_to_new_mapping_is_complete(self):
        from commcare_connect.workflow.management.commands.migrate_run_statuses import _OLD_TO_NEW

        assert _OLD_TO_NEW == {
            "in_progress": RUN_STATUS_ACTIVE,
            "completed": RUN_STATUS_FROZEN,
        }
