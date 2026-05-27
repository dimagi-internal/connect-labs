"""Backend tests for WorkflowRunView flags injection."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


@pytest.fixture
def rf():
    return RequestFactory()


def _request_for(rf, run_id):
    req = rf.get(f"/labs/workflow/0/run/?run_id={run_id}")
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    req.user = MagicMock(username="jane_okeke")
    req.labs_context = {"opportunity_id": 10001, "opportunity": {"name": "x"}}
    return req


@patch("commcare_connect.workflow.views.TaskDataAccess")
@patch("commcare_connect.workflow.views.AuditDataAccess")
@patch("commcare_connect.workflow.views.FlagsDataAccess")
@patch("commcare_connect.workflow.views.WorkflowDataAccess")
@patch("commcare_connect.workflow.views.get_org_data", return_value={})
def test_workflow_run_view_injects_flags(mock_org, MockWDA, MockFDA, MockADA, MockTDA, rf):
    """When a real run_id is loaded, the run's Flags are included in
    workflow_data["flags"] for the frontend to consume."""
    from commcare_connect.flags.models import FlagRecord
    from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
    from commcare_connect.workflow.views import WorkflowRunView

    wda = MockWDA.return_value
    wda.get_definition.return_value = WorkflowDefinitionRecord(
        {
            "id": 47,
            "experiment": "workflows",
            "type": "WorkflowDefinition",
            "opportunity_id": 10001,
            "data": {"name": "CHC", "opportunity_ids": [], "config": {}},
        }
    )
    wda.get_render_code.return_value = None
    wda.get_run.return_value = WorkflowRunRecord(
        {
            "id": 503,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": 10001,
            "data": {"status": "completed", "definition_id": 47, "state": {}},
        }
    )
    wda.get_workers.return_value = []

    MockADA.return_value.get_sessions_by_workflow_run.return_value = []
    MockTDA.return_value.get_tasks_for_run.return_value = []

    fda = MockFDA.return_value
    fda.get_flags_for_run.return_value = [
        FlagRecord(
            {
                "id": 11,
                "experiment": "flags",
                "type": "Flag",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "workflow_run_id": 503,
                    "flw_id": "amina",
                    "flag_key": "sam_low",
                    "flag_label": "SAM rate low",
                    "evidence": {"sam_pct": 0.1},
                    "source": "auto",
                    "flagged_at": "2025-11-11T11:42:00Z",
                },
            }
        ),
        FlagRecord(
            {
                "id": 12,
                "experiment": "flags",
                "type": "Flag",
                "username": "binta",
                "opportunity_id": 10001,
                "data": {
                    "workflow_run_id": 503,
                    "flw_id": "binta",
                    "flag_key": "gender_skew",
                    "flag_label": "Gender split outside 40-60%",
                    "source": "auto",
                    "flagged_at": "2025-11-11T11:43:00Z",
                },
            }
        ),
    ]

    view = WorkflowRunView()
    view.request = _request_for(rf, 503)
    view.kwargs = {"definition_id": 47}

    context = view.get_context_data()

    assert "workflow_data" in context, "expected workflow_data context key for a loaded run"
    flags = context["workflow_data"]["flags"]
    assert len(flags) == 2
    assert flags[0]["flw_id"] == "amina"
    assert flags[0]["flag_key"] == "sam_low"
    assert flags[0]["evidence"] == {"sam_pct": 0.1}
    assert flags[0]["source"] == "auto"
    assert flags[1]["flw_id"] == "binta"
    assert flags[1]["flag_key"] == "gender_skew"
    fda.get_flags_for_run.assert_called_once_with(503)


@patch("commcare_connect.workflow.views.TaskDataAccess")
@patch("commcare_connect.workflow.views.AuditDataAccess")
@patch("commcare_connect.workflow.views.FlagsDataAccess")
@patch("commcare_connect.workflow.views.WorkflowDataAccess")
@patch("commcare_connect.workflow.views.get_org_data", return_value={})
def test_workflow_run_view_flags_empty_when_load_fails(mock_org, MockWDA, MockFDA, MockADA, MockTDA, rf):
    """If FlagsDataAccess raises, the page must still render — flags
    default to []."""
    from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
    from commcare_connect.workflow.views import WorkflowRunView

    wda = MockWDA.return_value
    wda.get_definition.return_value = WorkflowDefinitionRecord(
        {
            "id": 47,
            "experiment": "workflows",
            "type": "WorkflowDefinition",
            "opportunity_id": 10001,
            "data": {"name": "CHC", "opportunity_ids": [], "config": {}},
        }
    )
    wda.get_render_code.return_value = None
    wda.get_run.return_value = WorkflowRunRecord(
        {
            "id": 503,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": 10001,
            "data": {"status": "in_progress", "definition_id": 47, "state": {}},
        }
    )
    wda.get_workers.return_value = []
    MockFDA.return_value.get_flags_for_run.side_effect = RuntimeError("API down")
    MockADA.return_value.get_sessions_by_workflow_run.side_effect = RuntimeError("API down")
    MockTDA.return_value.get_tasks_for_run.side_effect = RuntimeError("API down")

    view = WorkflowRunView()
    view.request = _request_for(rf, 503)
    view.kwargs = {"definition_id": 47}

    context = view.get_context_data()
    assert context["workflow_data"]["flags"] == []
    # Same graceful-empty contract for the read-only audits + tasks
    # surfaces — a broken downstream API must never wedge the runner.
    assert context["workflow_data"]["audits"] == []
    assert context["workflow_data"]["tasks"] == []


@patch("commcare_connect.workflow.views.TaskDataAccess")
@patch("commcare_connect.workflow.views.AuditDataAccess")
@patch("commcare_connect.workflow.views.FlagsDataAccess")
@patch("commcare_connect.workflow.views.WorkflowDataAccess")
@patch("commcare_connect.workflow.views.get_org_data", return_value={})
def test_workflow_run_view_injects_audits_and_tasks(mock_org, MockWDA, MockFDA, MockADA, MockTDA, rf):
    """When a run has linked AuditSession + Task records, they ship to
    the frontend as workflow_data["audits"] / ["tasks"]. The shape
    mirrors PAR's build_snapshot per-FLW group so template render code
    can use the same field names whether it reads from the watched-
    workflow snapshot or from the runner's live `view.audits` array.
    """
    from commcare_connect.audit.data_access import AuditSessionRecord
    from commcare_connect.tasks.data_access import TaskRecord
    from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
    from commcare_connect.workflow.views import WorkflowRunView

    wda = MockWDA.return_value
    wda.get_definition.return_value = WorkflowDefinitionRecord(
        {
            "id": 47,
            "experiment": "workflows",
            "type": "WorkflowDefinition",
            "opportunity_id": 10001,
            "data": {"name": "CHC", "opportunity_ids": [], "config": {}},
        }
    )
    wda.get_render_code.return_value = None
    wda.get_run.return_value = WorkflowRunRecord(
        {
            "id": 503,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": 10001,
            "data": {"status": "completed", "definition_id": 47, "state": {}},
        }
    )
    wda.get_workers.return_value = []
    MockFDA.return_value.get_flags_for_run.return_value = []

    MockADA.return_value.get_sessions_by_workflow_run.return_value = [
        AuditSessionRecord(
            {
                "id": 77,
                "experiment": "audit",
                "type": "AuditSession",
                "opportunity_id": 10001,
                "username": "amina",
                "labs_record_id": 503,
                "data": {
                    "status": "completed",
                    "overall_result": "pass",
                    "image_results": {"pass": 5, "fail": 0, "pending": 0},
                },
            }
        ),
    ]
    MockTDA.return_value.get_tasks_for_run.return_value = [
        TaskRecord(
            {
                "id": 88,
                "experiment": "tasks",
                "type": "Task",
                "opportunity_id": 10001,
                "username": "amina",
                "data": {
                    "status": "closed",
                    "title": "Coaching: amina",
                    "priority": "medium",
                    "workflow_run_id": 503,
                    "resolution_details": {"official_action": "satisfactory"},
                },
            }
        ),
    ]

    view = WorkflowRunView()
    view.request = _request_for(rf, 503)
    view.kwargs = {"definition_id": 47}

    context = view.get_context_data()
    audits = context["workflow_data"]["audits"]
    tasks = context["workflow_data"]["tasks"]
    assert len(audits) == 1
    assert audits[0] == {
        "id": 77,
        "flw_id": "amina",
        "status": "completed",
        "overall_result": "pass",
        "pass_count": 5,
        "fail_count": 0,
        "pending_count": 0,
    }
    assert len(tasks) == 1
    assert tasks[0]["id"] == 88
    assert tasks[0]["flw_id"] == "amina"
    assert tasks[0]["status"] == "closed"
    assert tasks[0]["official_action"] == "satisfactory"
