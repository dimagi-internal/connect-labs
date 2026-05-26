"""Backend tests for WorkflowRunView decisions injection."""

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


@patch("commcare_connect.workflow.views.DecisionsDataAccess")
@patch("commcare_connect.workflow.views.WorkflowDataAccess")
@patch("commcare_connect.workflow.views.get_org_data", return_value={})
def test_workflow_run_view_injects_decisions(mock_org, MockWDA, MockDDA, rf):
    """When a real run_id is loaded, the run's Decisions are included in
    workflow_data["decisions"] for the frontend to consume."""
    from commcare_connect.decisions.models import DecisionRecord
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

    dda = MockDDA.return_value
    dda.get_decisions_for_run.return_value = [
        DecisionRecord(
            {
                "id": 11,
                "experiment": "decisions",
                "type": "Decision",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "workflow_run_id": 503,
                    "flw_id": "amina",
                    "decision_type": "action_taken",
                    "reason_key": "bad_muac_distribution",
                    "reason_label": "Bad MUAC pattern",
                    "audit_session_ids": [46],
                    "task_ids": [123],
                    "decided_at": "2025-11-11T11:42:00Z",
                },
            }
        ),
        DecisionRecord(
            {
                "id": 12,
                "experiment": "decisions",
                "type": "Decision",
                "username": "binta",
                "opportunity_id": 10001,
                "data": {
                    "workflow_run_id": 503,
                    "flw_id": "binta",
                    "decision_type": "no_issues",
                    "decided_at": "2025-11-11T11:43:00Z",
                },
            }
        ),
    ]

    view = WorkflowRunView()
    view.request = _request_for(rf, 503)
    view.kwargs = {"definition_id": 47}

    context = view.get_context_data()

    assert "workflow_data" in context, "expected workflow_data context key for a loaded run"
    decisions = context["workflow_data"]["decisions"]
    assert len(decisions) == 2
    assert decisions[0]["flw_id"] == "amina"
    assert decisions[0]["decision_type"] == "action_taken"
    assert decisions[0]["reason_key"] == "bad_muac_distribution"
    assert decisions[0]["audit_session_ids"] == [46]
    assert decisions[0]["task_ids"] == [123]
    assert decisions[1]["flw_id"] == "binta"
    assert decisions[1]["decision_type"] == "no_issues"
    dda.get_decisions_for_run.assert_called_once_with(503)


@patch("commcare_connect.workflow.views.DecisionsDataAccess")
@patch("commcare_connect.workflow.views.WorkflowDataAccess")
@patch("commcare_connect.workflow.views.get_org_data", return_value={})
def test_workflow_run_view_decisions_empty_when_load_fails(mock_org, MockWDA, MockDDA, rf):
    """If DecisionsDataAccess raises, the page must still render — decisions
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
    MockDDA.return_value.get_decisions_for_run.side_effect = RuntimeError("API down")

    view = WorkflowRunView()
    view.request = _request_for(rf, 503)
    view.kwargs = {"definition_id": 47}

    context = view.get_context_data()
    assert context["workflow_data"]["decisions"] == []
