from unittest import mock

import pytest

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User
from connect_labs.workflow.views import WorkflowListView


def _fake_def(def_id, template_type):
    return mock.Mock(id=def_id, template_type=template_type, pipeline_sources=[], name=f"W{def_id}")


def _fake_run(run_id, period_start, period_end, window_start=None, window_end=None):
    data = {}
    if window_start or window_end:
        data["state"] = {"window_start": window_start, "window_end": window_end}
    return mock.Mock(id=run_id, data=data, period_start=period_start, period_end=period_end)


@pytest.mark.django_db
def test_build_row_marks_schedulable_and_attaches_schedule():
    user = User.objects.create(username="alice")
    WorkflowSchedule.objects.create(
        definition_id=42,
        opportunity_id=1237,
        owner=user,
        definition_name="W42",
        cadence="weekly",
        hour=6,
        day_of_week=0,
    )
    view = WorkflowListView()

    schedules_by_def = {42: WorkflowSchedule.objects.get(definition_id=42)}
    with mock.patch(
        "connect_labs.workflow.views.template_supports_default_run",
        side_effect=lambda t: t == "program_audit_creator",
    ):
        row = view._build_workflow_row(_fake_def(42, "program_audit_creator"), [], mock.Mock(), {}, schedules_by_def)
        row_other = view._build_workflow_row(_fake_def(7, "performance_review"), [], mock.Mock(), {}, schedules_by_def)

    assert row["schedulable"] is True
    assert row["schedule"]["cadence"] == "weekly"
    assert row_other["schedulable"] is False
    assert row_other["schedule"] is None


def test_build_row_prefers_fired_window_over_frozen_shell_period():
    """A run's period_start/period_end are frozen at create_run time (often the
    generic '+Create Run' button's ISO-week default) and update_run_state
    deliberately never touches them once a batch actually fires with a
    different window (audit-window templates persist that into
    state.window_start/window_end instead — see _build_workflow_row's
    comment). The list view's displayed period should reflect what was
    actually audited, not the stale creation-time shell period."""
    view = WorkflowListView()
    run = _fake_run(13021, "2026-08-10", "2026-08-16", window_start="2026-08-12", window_end="2026-08-12")

    row = view._build_workflow_row(_fake_def(12705, "weekly_dual_track_audit"), [run], mock.Mock(), {}, {})

    fired = row["runs"][0]
    assert fired.display_period_start == "2026-08-12"
    assert fired.display_period_end == "2026-08-12"
    # The underlying stored period is untouched -- only the display field changes.
    assert fired.period_start == "2026-08-10"
    assert fired.period_end == "2026-08-16"


def test_build_row_falls_back_to_shell_period_when_nothing_has_fired():
    """A run with no fired batch yet (or a template that never writes
    state.window_start/window_end) shows its shell period unchanged."""
    view = WorkflowListView()
    run = _fake_run(13022, "2026-08-10", "2026-08-16")

    row = view._build_workflow_row(_fake_def(12705, "weekly_dual_track_audit"), [run], mock.Mock(), {}, {})

    fired = row["runs"][0]
    assert fired.display_period_start == "2026-08-10"
    assert fired.display_period_end == "2026-08-16"
