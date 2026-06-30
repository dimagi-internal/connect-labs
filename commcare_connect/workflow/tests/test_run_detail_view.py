"""Backend tests for WorkflowRunView flags injection."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


def _render_code(*helpers: str) -> MagicMock:
    """Build a get_render_code() return value whose component_code
    references the given view helpers.

    WorkflowRunView gates the flags / audits / tasks loads on whether the
    render code actually uses view.flagsFor / view.auditsFor /
    view.tasksFor (loading each is a full-table scan, pointless for a
    template that doesn't read that surface). Tests that want a given
    surface populated must therefore present render code that mentions
    the corresponding helper.
    """
    body = " ".join(f"view.{h}(r.username)" for h in helpers)
    rc = MagicMock()
    rc.data = {"component_code": f"function WorkflowUI() {{ {body} }}"}
    return rc


@pytest.fixture
def rf():
    return RequestFactory()


def _bare_request(rf, query):
    req = rf.get(f"/labs/workflow/47/run/{query}")
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    req.user = MagicMock(username="jane_okeke")
    req.labs_context = {"opportunity_id": 10001, "opportunity": {"name": "x"}}
    return req


def test_bare_run_url_redirects_to_list_with_highlight(rf):
    """A run URL with no run_id (and not edit-mode) no longer renders the
    deprecated picker — it bounces to the workflow LIST with this workflow's
    card highlighted. Run listing/creation live on the list page."""
    from django.http import HttpResponseRedirect

    from commcare_connect.workflow.views import WorkflowRunView

    view = WorkflowRunView()
    view.kwargs = {"definition_id": 47}
    resp = view.get(_bare_request(rf, "?opportunity_id=10001"))

    assert isinstance(resp, HttpResponseRedirect)
    assert resp.url == "/labs/workflow/?opportunity_id=10001&highlight=47#workflow-47"


def test_run_url_with_run_id_is_not_redirected_to_list(rf):
    """A run URL WITH a run_id is unaffected by the deprecation — it falls
    through to the normal render path (super().get), not the list redirect."""
    from commcare_connect.workflow import views as views_mod
    from commcare_connect.workflow.views import WorkflowRunView

    view = WorkflowRunView()
    view.kwargs = {"definition_id": 47}

    sentinel = object()
    with patch.object(views_mod.TemplateView, "get", return_value=sentinel) as mock_super:
        resp = view.get(_bare_request(rf, "?run_id=503&opportunity_id=10001"))

    assert resp is sentinel
    mock_super.assert_called_once()


def test_edit_mode_run_url_is_not_redirected_to_list(rf):
    """?edit=true (preview mode) must still render, not bounce to the list."""
    from commcare_connect.workflow import views as views_mod
    from commcare_connect.workflow.views import WorkflowRunView

    view = WorkflowRunView()
    view.kwargs = {"definition_id": 47}

    sentinel = object()
    with patch.object(views_mod.TemplateView, "get", return_value=sentinel):
        resp = view.get(_bare_request(rf, "?edit=true&opportunity_id=10001"))

    assert resp is sentinel


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
    wda.get_render_code.return_value = _render_code("flagsFor")
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
    wda.get_render_code.return_value = _render_code("flagsFor", "auditsFor", "tasksFor")
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
    wda.get_render_code.return_value = _render_code("auditsFor", "tasksFor")
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


@patch("commcare_connect.workflow.views.TaskDataAccess")
@patch("commcare_connect.workflow.views.AuditDataAccess")
@patch("commcare_connect.workflow.views.FlagsDataAccess")
@patch("commcare_connect.workflow.views.WorkflowDataAccess")
@patch("commcare_connect.workflow.views.get_org_data", return_value={})
def test_workflow_run_view_skips_loads_when_render_code_doesnt_use_helpers(
    mock_org, MockWDA, MockFDA, MockADA, MockTDA, rf
):
    """A template whose render code never references view.flagsFor /
    auditsFor / tasksFor (e.g. the Program Admin Report, which builds its
    own rollup from its snapshot) must NOT trigger the full-table scans.
    Each scan is expensive; loading data the template can't read is pure
    waste and was the cause of the multi-second blank screen at the top
    of the recorded PAR drill-through.
    """
    from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
    from commcare_connect.workflow.views import WorkflowRunView

    wda = MockWDA.return_value
    wda.get_definition.return_value = WorkflowDefinitionRecord(
        {
            "id": 65,
            "experiment": "workflows",
            "type": "WorkflowDefinition",
            "opportunity_id": 10001,
            "data": {"name": "PAR", "opportunity_ids": [], "config": {}},
        }
    )
    # Render code that reads its own snapshot rows (fr.flags / fr.audits)
    # but never calls the view.*For helpers.
    rc = MagicMock()
    rc.data = {"component_code": "function WorkflowUI() { return (run.flw_rows || []).map(fr => fr.audits); }"}
    wda.get_render_code.return_value = rc
    wda.get_run.return_value = WorkflowRunRecord(
        {
            "id": 901,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": 10001,
            "data": {"status": "completed", "definition_id": 65, "state": {}},
        }
    )
    wda.get_workers.return_value = []

    view = WorkflowRunView()
    view.request = _request_for(rf, 901)
    view.kwargs = {"definition_id": 65}

    context = view.get_context_data()

    # All three surfaces ship as empty lists...
    assert context["workflow_data"]["flags"] == []
    assert context["workflow_data"]["audits"] == []
    assert context["workflow_data"]["tasks"] == []
    # ...and crucially the expensive scans were never issued.
    MockFDA.return_value.get_flags_for_run.assert_not_called()
    MockADA.return_value.get_sessions_by_workflow_run.assert_not_called()
    MockTDA.return_value.get_tasks_for_run.assert_not_called()
