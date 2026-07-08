from unittest import mock

import pytest

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User
from connect_labs.workflow.views import WorkflowListView


def _fake_def(def_id, template_type):
    return mock.Mock(id=def_id, template_type=template_type, pipeline_sources=[], name=f"W{def_id}")


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
