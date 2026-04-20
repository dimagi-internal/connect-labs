"""Tests for workflow_* MCP tools.

Mocks WorkflowDataAccess to avoid hitting the real Connect API.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.fixture
def auth_user(db):
    """A user with a PAT AND a UserConnectToken (fully set up for tool calls)."""
    user = User.objects.create(username="wftest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    resp = client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_pat}",
    )
    return resp.json()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_list_returns_workflows_for_opportunity(mock_wda, client, auth_user):
    _, raw = auth_user

    mock_def = MagicMock()
    mock_def.id = 42
    mock_def.name = "Perf Review"
    mock_def.description = "Test"
    mock_def.template_type = "performance_review"
    mock_def.pipeline_sources = [{"pipeline_id": 1, "alias": "d"}]

    mock_instance = MagicMock()
    mock_instance.list_definitions.return_value = [mock_def]
    mock_wda.return_value = mock_instance

    data = _call_tool(client, raw, "workflow_list", {"opportunity_id": 123})
    assert data["result"]["isError"] is False, data
    workflows = data["result"]["structuredContent"]["workflows"]
    assert len(workflows) == 1
    assert workflows[0]["id"] == 42
    assert workflows[0]["pipeline_source_count"] == 1


@pytest.mark.django_db
def test_workflow_list_rejects_missing_scope(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "workflow_list", {})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_workflow_list_rejects_multiple_scopes(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "workflow_list", {"opportunity_id": 1, "program_id": 2})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_workflow_list_rejects_user_without_connect_token(client, db):
    """A user with a PAT but no UserConnectToken row gets PERMISSION_DENIED."""
    user = User.objects.create(username="notoken")
    _, raw = MCPAccessToken.create_token(user, name="t")
    data = _call_tool(client, raw, "workflow_list", {"opportunity_id": 1})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.PipelineDataAccess")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_get_returns_full_bundle(mock_wda_cls, mock_pda_cls, client, auth_user):
    _, raw = auth_user

    mock_def = MagicMock(
        id=42,
        description="Test",
        data={
            "pipeline_sources": [{"pipeline_id": 7, "alias": "data"}],
            "statuses": [{"key": "open", "label": "Open"}],
            "config": {"templateType": "perf"},
        },
    )
    # MagicMock(name=...) sets the mock's internal name, not an attribute — override explicitly.
    mock_def.name = "My Workflow"
    mock_def.template_type = "perf"
    mock_wda_cls.return_value.get_definition.return_value = mock_def
    mock_wda_cls.return_value.get_render_code.return_value = MagicMock(
        component_code="function WorkflowUI(){}",
        version=3,
    )

    mock_pipeline = MagicMock(
        data={"schema": {"fields": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}},
    )
    # MagicMock auto-creates .name as Mock; override explicitly to a string.
    mock_pipeline.name = "P1"
    mock_pda_cls.return_value.get_definition.return_value = mock_pipeline

    data = _call_tool(client, raw, "workflow_get", {"workflow_id": 42, "opportunity_id": 100})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["render_code"] == "function WorkflowUI(){}"
    assert content["render_code_version"] == 3
    assert content["template_type"] == "perf"
    assert content["pipeline_sources"][0]["pipeline_id"] == 7
    assert content["pipeline_sources"][0]["alias"] == "data"
    assert content["pipeline_sources"][0]["name"] == "P1"
    assert content["pipeline_sources"][0]["schema_summary"]["field_count"] == 3


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_get_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_definition.return_value = None

    data = _call_tool(client, raw, "workflow_get", {"workflow_id": 999, "opportunity_id": 100})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_workflow_get_rejects_user_without_connect_token(client, db):
    user = User.objects.create(username="no-connect")
    _, raw = MCPAccessToken.create_token(user, name="t")
    data = _call_tool(client, raw, "workflow_get", {"workflow_id": 1, "opportunity_id": 100})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_workflow_get_rejects_missing_required_args(client, auth_user):
    _, raw = auth_user
    # Missing opportunity_id
    data = _call_tool(client, raw, "workflow_get", {"workflow_id": 42})
    # Missing a required positional arg raises TypeError which the transport catches
    # as an unhandled exception and returns a JSON-RPC level error (data["error"])
    # rather than a tool-level isError result.  Either format signals failure.
    assert "error" in data or data.get("result", {}).get("isError") is True


VALID_JSX = "function WorkflowUI(props) { var x = 1; return null; }"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_render_code_happy_path(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_render_code.return_value = MagicMock(version=3)
    mock_wda_cls.return_value.save_render_code.return_value = MagicMock(version=4)

    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": VALID_JSX,
            "expected_version": 3,
        },
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["new_version"] == 4
    # Private _version_* keys must NOT leak to the client
    assert "_version_before" not in content
    assert "_version_after" not in content


@pytest.mark.django_db
def test_update_render_code_rejects_missing_workflowui(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": "function NotWorkflowUI() { var x = 1; }",
            "expected_version": 1,
        },
    )
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_JSX"


@pytest.mark.django_db
def test_update_render_code_rejects_const_let(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": "function WorkflowUI() { const x = 1; }",
            "expected_version": 1,
        },
    )
    assert data["result"]["isError"] is True
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_JSX"
    assert "const" in err["message"]


@pytest.mark.django_db
def test_update_render_code_rejects_empty(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": "   ",
            "expected_version": 1,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_JSX"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_render_code_version_conflict(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_render_code.return_value = MagicMock(version=5)
    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": VALID_JSX,
            "expected_version": 3,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "VERSION_CONFLICT"
    assert err["details"]["server_version"] == 5
    assert err["details"]["expected"] == 3


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_render_code_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_render_code.return_value = None
    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 999,
            "opportunity_id": 100,
            "component_code": VALID_JSX,
            "expected_version": 1,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_render_code_audits_version_transition(mock_wda_cls, client, auth_user):
    """The transport's audit hook should capture _version_before/_version_after."""
    from commcare_connect.mcp.models import MCPAuditLog

    user, raw = auth_user
    mock_wda_cls.return_value.get_render_code.return_value = MagicMock(version=7)
    mock_wda_cls.return_value.save_render_code.return_value = MagicMock(version=8)

    _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": VALID_JSX,
            "expected_version": 7,
        },
    )
    log = MCPAuditLog.objects.get(user=user, tool_name="workflow_update_render_code")
    assert log.success is True
    assert log.is_write is True
    assert log.version_before == 7
    assert log.version_after == 8
