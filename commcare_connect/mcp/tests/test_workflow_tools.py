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


# =============================================================================
# workflow_update_definition tests
# =============================================================================


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_definition_happy_path(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    current = MagicMock(
        id=42,
        description="old desc",
        data={"version": 3, "config": {"a": 1}, "statuses": [{"key": "old"}], "name": "Old Name"},
    )
    current.name = "Old Name"
    mock_wda_cls.return_value.get_definition.return_value = current
    updated = MagicMock(
        data={"version": 4, "config": {"a": 1, "b": 2}, "statuses": [{"key": "new"}]},
    )
    mock_wda_cls.return_value.update_definition.return_value = updated

    data = _call_tool(
        client,
        raw,
        "workflow_update_definition",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "patch": {"name": "New Name", "config": {"b": 2}, "statuses": [{"key": "new"}]},
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
def test_update_definition_rejects_unknown_patch_keys(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_update_definition",
        {"workflow_id": 42, "opportunity_id": 100, "patch": {"secret_field": "x"}, "expected_version": 1},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_definition_version_conflict(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_definition.return_value = MagicMock(
        data={"version": 7},
    )
    data = _call_tool(
        client,
        raw,
        "workflow_update_definition",
        {"workflow_id": 42, "opportunity_id": 100, "patch": {"name": "X"}, "expected_version": 3},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "VERSION_CONFLICT"
    assert err["details"]["server_version"] == 7


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_definition_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_definition.return_value = None
    data = _call_tool(
        client,
        raw,
        "workflow_update_definition",
        {"workflow_id": 999, "opportunity_id": 100, "patch": {"name": "X"}, "expected_version": 1},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# =============================================================================
# workflow_revert_render_code tests
# =============================================================================


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_revert_render_code_happy_path(mock_wda_cls, client, auth_user):
    _, raw = auth_user

    current = MagicMock(component_code="current code", version=5)
    mock_wda_cls.return_value.get_render_code.return_value = current
    mock_wda_cls.return_value.save_render_code.return_value = MagicMock(
        component_code="old code",
        version=6,
    )

    old_code = "function WorkflowUI(props) { var x = 'old'; return null; }"
    data = _call_tool(
        client,
        raw,
        "workflow_revert_render_code",
        {"workflow_id": 42, "opportunity_id": 100, "to_version": 2, "component_code": old_code},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["new_version"] == 6
    assert content["reverted_to_source_version"] == 2
    # save was called with the provided old code and a new version
    args, kwargs = mock_wda_cls.return_value.save_render_code.call_args
    assert kwargs["component_code"] == old_code
    assert kwargs["version"] == 6
    # Private _version_* keys must NOT leak to the client
    assert "_version_before" not in content
    assert "_version_after" not in content


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_revert_render_code_version_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_render_code.return_value = None
    data = _call_tool(
        client,
        raw,
        "workflow_revert_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "to_version": 99,
            "component_code": VALID_JSX,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_revert_render_code_rejects_invalid_jsx(mock_wda_cls, client, auth_user):
    """Revert validates JSX before saving."""
    _, raw = auth_user
    current = MagicMock(component_code="current code", version=5)
    mock_wda_cls.return_value.get_render_code.return_value = current
    data = _call_tool(
        client,
        raw,
        "workflow_revert_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "to_version": 2,
            "component_code": "function NotWorkflowUI() { var x = 1; }",
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_JSX"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_revert_render_code_rejects_future_version(mock_wda_cls, client, auth_user):
    """Cannot revert to a version >= current version."""
    _, raw = auth_user
    current = MagicMock(component_code="current code", version=3)
    mock_wda_cls.return_value.get_render_code.return_value = current
    data = _call_tool(
        client,
        raw,
        "workflow_revert_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "to_version": 5,
            "component_code": VALID_JSX,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    assert "current_version" in err["details"]


# =============================================================================
# workflow_create_from_template tests
# =============================================================================


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows._create_workflow_from_template")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_create_from_template_happy_path(mock_wda_cls, mock_create, client, auth_user):
    _, raw = auth_user
    mock_def = MagicMock(id=101, description="", data={"name": "Perf Review", "version": 1})
    mock_def.name = "Perf Review"
    mock_render = MagicMock(version=1)
    mock_create.return_value = (mock_def, mock_render, None)

    data = _call_tool(
        client,
        raw,
        "workflow_create_from_template",
        {"template_key": "performance_review", "opportunity_id": 100},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["workflow_id"] == 101
    assert content["render_code_version"] == 1
    assert content["pipeline_id"] is None
    assert "_version_before" not in content
    assert "_version_after" not in content


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows._create_workflow_from_template")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_create_from_template_with_name_override(mock_wda_cls, mock_create, client, auth_user):
    _, raw = auth_user
    mock_def = MagicMock(id=101, description="", data={"name": "Original Name", "version": 1})
    mock_def.name = "Original Name"
    mock_render = MagicMock(version=1)
    mock_create.return_value = (mock_def, mock_render, None)

    data = _call_tool(
        client,
        raw,
        "workflow_create_from_template",
        {"template_key": "performance_review", "opportunity_id": 100, "name": "My Custom Name"},
    )
    assert data["result"]["isError"] is False, data
    # update_definition should have been called to apply the name override
    mock_wda_cls.return_value.update_definition.assert_called_once()
    call_kwargs = mock_wda_cls.return_value.update_definition.call_args[1]
    assert call_kwargs["data"]["name"] == "My Custom Name"


@pytest.mark.django_db
def test_create_from_template_unknown_template(client, auth_user):
    """With no mocks, the real create_workflow_from_template raises ValueError for
    an unknown template key which we map to NOT_FOUND."""
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_create_from_template",
        {"template_key": "nonexistent-template-xyz", "opportunity_id": 100},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] in ("NOT_FOUND", "UPSTREAM_ERROR")
