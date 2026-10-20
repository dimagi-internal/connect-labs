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


class TestNameProxy:
    """`.name` is a plain top-level label (never wiped by state.* merges,
    never blocked by the completed-run write guard) -- see rename_run."""

    def test_name_passes_through_when_set(self):
        assert _make_record(name="Week 30 Audit").name == "Week 30 Audit"

    def test_name_is_empty_string_when_unset(self):
        assert _make_record().name == ""


class TestStateWritesAfterCompletion:
    """update_run_state refuses a completed run -- except a write of nothing but the
    follow-up links (POST_COMPLETION_STATE_KEYS), which leaves the run completed."""

    def _wda(self):
        from unittest import mock

        from connect_labs.workflow.data_access import WorkflowDataAccess

        wda = WorkflowDataAccess(access_token="t", opportunity_id=42)
        wda.labs_api = mock.Mock()
        wda.labs_api.update_record.return_value = mock.Mock(
            id=1, experiment="workflow", type="workflow_run", data={}, opportunity_id=42
        )
        return wda

    def _completed(self):
        return _make_record(
            definition_id=3,
            status=RUN_STATUS_COMPLETED,
            completed_at="2026-10-19T09:00:00Z",
            snapshot={"audit_summary": {"by_tag": {}}},
            state={"avg_passed": 80},
        )

    def test_task_links_are_merged_into_a_completed_run_without_reopening_it(self):
        wda = self._wda()

        result = wda.update_run_state(1, {"flw_tasks": {"flw_a": 42}, "tasks_created": 1}, run=self._completed())

        assert result is not None
        written = wda.labs_api.update_record.call_args.kwargs["data"]
        assert written["status"] == RUN_STATUS_COMPLETED
        assert written["completed_at"] == "2026-10-19T09:00:00Z"
        assert written["snapshot"] == {"audit_summary": {"by_tag": {}}}
        assert written["state"] == {"avg_passed": 80, "flw_tasks": {"flw_a": 42}, "tasks_created": 1}

    def test_any_other_key_on_a_completed_run_is_refused(self):
        wda = self._wda()

        assert wda.update_run_state(1, {"flw_tasks": {}, "avg_passed": 10}, run=self._completed()) is None
        assert wda.update_run_state(1, {}, run=self._completed()) is None
        wda.labs_api.update_record.assert_not_called()
