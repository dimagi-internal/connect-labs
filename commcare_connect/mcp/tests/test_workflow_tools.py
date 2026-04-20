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
@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_render_code_accepts_const_let(mock_wda_cls, client, auth_user):
    """Modern JS declarations are fine — the browser's Babel step handles them.
    We used to reject them server-side; that was policy, not a real syntax
    check, and it blocked legitimate modern code."""
    _, raw = auth_user
    mock_render = MagicMock()
    mock_render.version = 1
    mock_wda_cls.return_value.get_render_code.return_value = mock_render
    new_record = MagicMock(version=2)
    mock_wda_cls.return_value.save_render_code.return_value = new_record

    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": "function WorkflowUI() { const x = 1; let y = 2; return null; }",
            "expected_version": 1,
        },
    )
    assert data["result"]["isError"] is False, data


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_render_code_accepts_arrow_component(mock_wda_cls, client, auth_user):
    """Arrow-function components are also OK now. If the caller gets the
    contract wrong (e.g. function name mismatch), Babel will tell them at
    render time with a clearer error than a server-side regex."""
    _, raw = auth_user
    mock_render = MagicMock()
    mock_render.version = 1
    mock_wda_cls.return_value.get_render_code.return_value = mock_render
    mock_wda_cls.return_value.save_render_code.return_value = MagicMock(version=2)

    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": "var WorkflowUI = (props) => null;",
            "expected_version": 1,
        },
    )
    assert data["result"]["isError"] is False, data


@pytest.mark.django_db
def test_update_render_code_rejects_oversized(client, auth_user):
    """Size cap still enforced so no one accidentally uploads a minified
    bundle the size of the internet."""
    _, raw = auth_user
    huge = "function WorkflowUI(){return null;} /* " + ("x" * (512 * 1024 + 1)) + " */"
    data = _call_tool(
        client,
        raw,
        "workflow_update_render_code",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "component_code": huge,
            "expected_version": 1,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_JSX"
    assert "512" in err["message"]


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


# =============================================================================
# workflow_clone tests
# =============================================================================


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_clone_happy_path(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    src = MagicMock()
    dst = MagicMock()
    mock_wda_cls.side_effect = [src, dst]

    source_def = MagicMock(
        id=1,
        description="orig",
        data={
            "version": 5,
            "config": {},
            "statuses": [],
            "pipeline_sources": [],
            "opportunity_ids": [],
            "is_template": True,
            "template_scope": "global",
        },
    )
    source_def.name = "Template WF"
    src.get_definition.return_value = source_def
    src.get_render_code.return_value = MagicMock(
        component_code="function WorkflowUI(){}",
        version=3,
    )

    dst.create_definition.return_value = MagicMock(id=42)
    dst.save_render_code.return_value = MagicMock(version=1)

    data = _call_tool(
        client,
        raw,
        "workflow_clone",
        {
            "source_workflow_id": 1,
            "source_opportunity_id": 10,
            "target_opportunity_id": 20,
            "new_name": "My Copy",
        },
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["new_workflow_id"] == 42
    assert content["name"] == "My Copy"
    assert content["render_code_version"] == 1

    # Template flags must have been stripped — create_definition receives
    # explicit kwargs (statuses, config, etc.), not a raw data dict.
    create_call = dst.create_definition.call_args
    assert create_call.kwargs["name"] == "My Copy"
    # is_template and template_scope must NOT be forwarded as kwargs
    assert "is_template" not in create_call.kwargs
    assert "template_scope" not in create_call.kwargs


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_clone_without_new_name_uses_copy_suffix(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    src, dst = MagicMock(), MagicMock()
    mock_wda_cls.side_effect = [src, dst]
    source_def = MagicMock(
        id=1,
        description="",
        data={
            "version": 1,
            "statuses": [],
            "config": {},
            "pipeline_sources": [],
            "opportunity_ids": [],
        },
    )
    source_def.name = "Original"
    src.get_definition.return_value = source_def
    src.get_render_code.return_value = None
    dst.create_definition.return_value = MagicMock(id=99)

    data = _call_tool(
        client,
        raw,
        "workflow_clone",
        {
            "source_workflow_id": 1,
            "source_opportunity_id": 10,
            "target_opportunity_id": 20,
        },
    )
    content = data["result"]["structuredContent"]
    assert content["name"] == "Original (copy)"
    assert content["render_code_version"] is None


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_clone_source_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    src = MagicMock()
    mock_wda_cls.return_value = src
    src.get_definition.return_value = None

    data = _call_tool(
        client,
        raw,
        "workflow_clone",
        {
            "source_workflow_id": 999,
            "source_opportunity_id": 10,
            "target_opportunity_id": 20,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# =============================================================================
# workflow_set_template_flag tests
# =============================================================================


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_set_template_flag_org_scope_non_admin_ok(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    current = MagicMock(data={"version": 1})
    mock_wda_cls.return_value.get_definition.return_value = current

    data = _call_tool(
        client,
        raw,
        "workflow_set_template_flag",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "is_template": True,
            "template_scope": "org:7",
        },
    )
    assert data["result"]["isError"] is False
    content = data["result"]["structuredContent"]
    assert content["is_template"] is True
    assert content["template_scope"] == "org:7"


@pytest.mark.django_db
def test_set_template_flag_global_rejected_for_non_admin(client, auth_user):
    user, raw = auth_user
    assert user.is_staff is False
    data = _call_tool(
        client,
        raw,
        "workflow_set_template_flag",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "is_template": True,
            "template_scope": "global",
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_set_template_flag_global_accepted_for_admin(mock_wda_cls, client, db):
    from datetime import timedelta

    from django.utils import timezone

    from commcare_connect.labs.models import UserConnectToken
    from commcare_connect.mcp.models import MCPAccessToken
    from commcare_connect.users.models import User

    admin = User.objects.create(username="admin-user", is_staff=True)
    _, raw = MCPAccessToken.create_token(admin, name="t")
    UserConnectToken.objects.create(
        user=admin,
        access_token="ok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    mock_wda_cls.return_value.get_definition.return_value = MagicMock(data={"version": 1})

    data = _call_tool(
        client,
        raw,
        "workflow_set_template_flag",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "is_template": True,
            "template_scope": "global",
        },
    )
    assert data["result"]["isError"] is False


@pytest.mark.django_db
def test_set_template_flag_invalid_scope_string(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_set_template_flag",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "is_template": True,
            "template_scope": "group:7",
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_set_template_flag_missing_scope_when_flagging(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "workflow_set_template_flag",
        {"workflow_id": 42, "opportunity_id": 100, "is_template": True},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    assert "template_scope is required" in err["message"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_set_template_flag_unmark(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    current = MagicMock(data={"version": 1, "is_template": True, "template_scope": "org:7"})
    mock_wda_cls.return_value.get_definition.return_value = current

    data = _call_tool(
        client,
        raw,
        "workflow_set_template_flag",
        {"workflow_id": 42, "opportunity_id": 100, "is_template": False},
    )
    assert data["result"]["isError"] is False
    # update_definition was called with a data dict missing the template keys
    call_kwargs = mock_wda_cls.return_value.update_definition.call_args.kwargs
    assert "is_template" not in call_kwargs["data"]
    assert "template_scope" not in call_kwargs["data"]


# --- workflow_update_opportunity_ids ------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_opportunity_ids_happy_path(mock_wda_cls, mock_fetch, client, auth_user):
    """Happy path: all opps are in caller's access, update succeeds, version bumps."""
    _, raw = auth_user
    mock_fetch.return_value = {
        "opportunities": [{"id": 100}, {"id": 200}, {"id": 300}],
    }
    current = MagicMock(data={"version": 3, "name": "Perf", "statuses": [{"id": "x"}]})
    updated = MagicMock(
        data={"version": 4, "name": "Perf", "statuses": [{"id": "x"}], "opportunity_ids": [100, 200, 300]}
    )
    mock_wda_cls.return_value.get_definition.return_value = current
    mock_wda_cls.return_value.update_definition.return_value = updated

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "opportunity_ids": [100, 200, 300],
            "expected_version": 3,
        },
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["workflow_id"] == 42
    assert content["opportunity_ids"] == [100, 200, 300]
    assert content["new_version"] == 4

    # update_definition received the merged data — other fields preserved, version bumped.
    call_kwargs = mock_wda_cls.return_value.update_definition.call_args.kwargs
    assert call_kwargs["data"]["opportunity_ids"] == [100, 200, 300]
    assert call_kwargs["data"]["name"] == "Perf"  # preserved
    assert call_kwargs["data"]["version"] == 4


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_opportunity_ids_rejects_ids_outside_user_access(mock_wda_cls, mock_fetch, client, auth_user):
    """If any id isn't in the caller's access, reject with PERMISSION_DENIED."""
    _, raw = auth_user
    mock_fetch.return_value = {"opportunities": [{"id": 100}, {"id": 200}]}

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "opportunity_ids": [100, 999],  # 999 not in user's access
            "expected_version": 3,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "PERMISSION_DENIED"
    assert err["details"]["invalid_opportunity_ids"] == [999]
    # Did not touch the DB.
    mock_wda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_opportunity_ids_empty_list_skips_validation(mock_wda_cls, mock_fetch, client, auth_user):
    """An empty list is allowed (revert to single-opp) and doesn't need access validation."""
    _, raw = auth_user
    current = MagicMock(data={"version": 1, "name": "Perf"})
    updated = MagicMock(data={"version": 2, "name": "Perf", "opportunity_ids": []})
    mock_wda_cls.return_value.get_definition.return_value = current
    mock_wda_cls.return_value.update_definition.return_value = updated

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "opportunity_ids": [],
            "expected_version": 1,
        },
    )
    assert data["result"]["isError"] is False, data
    assert data["result"]["structuredContent"]["opportunity_ids"] == []
    mock_fetch.assert_not_called()  # no validation call for empty list


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_opportunity_ids_dedupes(mock_wda_cls, mock_fetch, client, auth_user):
    """Duplicate ids are collapsed while preserving first-seen order."""
    _, raw = auth_user
    mock_fetch.return_value = {"opportunities": [{"id": 100}, {"id": 200}]}
    current = MagicMock(data={"version": 1, "name": "Perf"})
    updated = MagicMock(data={"version": 2, "name": "Perf", "opportunity_ids": [100, 200]})
    mock_wda_cls.return_value.get_definition.return_value = current
    mock_wda_cls.return_value.update_definition.return_value = updated

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "opportunity_ids": [100, 200, 100, 200],
            "expected_version": 1,
        },
    )
    assert data["result"]["isError"] is False, data
    call_kwargs = mock_wda_cls.return_value.update_definition.call_args.kwargs
    assert call_kwargs["data"]["opportunity_ids"] == [100, 200]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_opportunity_ids_version_conflict(mock_wda_cls, mock_fetch, client, auth_user):
    """expected_version mismatch produces VERSION_CONFLICT."""
    _, raw = auth_user
    mock_fetch.return_value = {"opportunities": [{"id": 100}]}
    current = MagicMock(data={"version": 5, "name": "Perf"})
    mock_wda_cls.return_value.get_definition.return_value = current

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "opportunity_ids": [100],
            "expected_version": 3,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "VERSION_CONFLICT"
    mock_wda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_update_opportunity_ids_not_found(mock_wda_cls, mock_fetch, client, auth_user):
    """Missing workflow → NOT_FOUND."""
    _, raw = auth_user
    mock_fetch.return_value = {"opportunities": [{"id": 100}]}
    mock_wda_cls.return_value.get_definition.return_value = None

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 999,
            "opportunity_id": 100,
            "opportunity_ids": [100],
            "expected_version": 1,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
def test_update_opportunity_ids_upstream_failure_blocks_write(mock_fetch, client, auth_user):
    """If we can't fetch the user's opportunities, we must refuse to validate rather than persist blindly."""
    _, raw = auth_user
    mock_fetch.return_value = None

    data = _call_tool(
        client,
        raw,
        "workflow_update_opportunity_ids",
        {
            "workflow_id": 42,
            "opportunity_id": 100,
            "opportunity_ids": [100, 200],
            "expected_version": 1,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "UPSTREAM_ERROR"


# --- workflow_get include_render_code ---------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.PipelineDataAccess")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_get_omits_render_code_when_requested(mock_wda_cls, mock_pda_cls, client, auth_user):
    """include_render_code=false keeps render_code_version but drops the
    component_code string. Saves ~20 KB per call when the caller only wants
    metadata."""
    _, raw = auth_user
    mock_def = MagicMock(data={"pipeline_sources": []})
    mock_def.id = 1
    mock_def.name = "WF"
    mock_def.description = "d"
    mock_def.template_type = "performance_review"
    mock_wda_cls.return_value.get_definition.return_value = mock_def

    mock_rc = MagicMock()
    mock_rc.component_code = "function WorkflowUI(){}"
    mock_rc.version = 5
    mock_wda_cls.return_value.get_render_code.return_value = mock_rc
    mock_pda_cls.return_value.get_definition.return_value = None

    data = _call_tool(
        client,
        raw,
        "workflow_get",
        {"workflow_id": 1, "opportunity_id": 100, "include_render_code": False},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "render_code" not in content
    assert content["render_code_version"] == 5


# --- workflow_create_from_template with opportunity_ids ---------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
@patch("commcare_connect.mcp.tools.workflows._create_workflow_from_template")
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_create_from_template_forwards_opportunity_ids(mock_wda_cls, mock_create, mock_fetch, client, auth_user):
    """opportunity_ids validation mirrors workflow_update_opportunity_ids —
    each id must be in the caller's access."""
    _, raw = auth_user
    mock_fetch.return_value = {"opportunities": [{"id": 100}, {"id": 200}, {"id": 300}]}
    mock_def = MagicMock(id=42, name="N", data={"name": "N"})
    mock_render = MagicMock(version=1)
    mock_create.return_value = (mock_def, mock_render, None)

    data = _call_tool(
        client,
        raw,
        "workflow_create_from_template",
        {
            "template_key": "performance_review",
            "opportunity_id": 100,
            "opportunity_ids": [100, 200, 300],
        },
    )
    assert data["result"]["isError"] is False, data
    # cleaned list was forwarded to create_workflow_from_template
    kwargs = mock_create.call_args.kwargs
    assert kwargs["opportunity_ids"] == [100, 200, 300]
    assert data["result"]["structuredContent"]["opportunity_ids"] == [100, 200, 300]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.fetch_user_organization_data")
def test_create_from_template_rejects_unauthorized_opportunity_ids(mock_fetch, client, auth_user):
    _, raw = auth_user
    mock_fetch.return_value = {"opportunities": [{"id": 100}]}
    data = _call_tool(
        client,
        raw,
        "workflow_create_from_template",
        {
            "template_key": "performance_review",
            "opportunity_id": 100,
            "opportunity_ids": [100, 999],
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "PERMISSION_DENIED"
    assert err["details"]["invalid_opportunity_ids"] == [999]


# --- workflow_patch_render_code ---------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_patch_render_code_applies_unique_match(mock_wda_cls, client, auth_user):
    """Unique search → the patch is applied and version bumps."""
    _, raw = auth_user
    mock_rc = MagicMock()
    mock_rc.component_code = "function WorkflowUI(){ var x = 1; return x; }"
    mock_rc.version = 3
    mock_wda_cls.return_value.get_render_code.return_value = mock_rc
    mock_wda_cls.return_value.save_render_code.return_value = MagicMock(version=4)

    data = _call_tool(
        client,
        raw,
        "workflow_patch_render_code",
        {
            "workflow_id": 1,
            "opportunity_id": 100,
            "search": "var x = 1;",
            "replace": "var x = 42;",
            "expected_version": 3,
        },
    )
    assert data["result"]["isError"] is False, data
    call_kwargs = mock_wda_cls.return_value.save_render_code.call_args.kwargs
    assert "var x = 42;" in call_kwargs["component_code"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_patch_render_code_refuses_ambiguous_match(mock_wda_cls, client, auth_user):
    """Search string matching >1 times must be refused — we don't guess
    which occurrence the caller meant."""
    _, raw = auth_user
    mock_rc = MagicMock()
    mock_rc.component_code = "var x; var x;"
    mock_rc.version = 1
    mock_wda_cls.return_value.get_render_code.return_value = mock_rc

    data = _call_tool(
        client,
        raw,
        "workflow_patch_render_code",
        {
            "workflow_id": 1,
            "opportunity_id": 100,
            "search": "var x;",
            "replace": "var y;",
            "expected_version": 1,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_JSX"
    assert err["details"]["occurrences"] == 2


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_patch_render_code_zero_matches_is_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_rc = MagicMock()
    mock_rc.component_code = "function WorkflowUI(){}"
    mock_rc.version = 1
    mock_wda_cls.return_value.get_render_code.return_value = mock_rc

    data = _call_tool(
        client,
        raw,
        "workflow_patch_render_code",
        {
            "workflow_id": 1,
            "opportunity_id": 100,
            "search": "nonexistent",
            "replace": "anything",
            "expected_version": 1,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "NOT_FOUND"


# --- workflow_delete --------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_delete_returns_counts(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_definition.return_value = MagicMock(id=42)
    mock_wda_cls.return_value.delete_definition.return_value = {
        "definition": 1,
        "render_code": 1,
        "runs": 0,
        "audit_sessions": 0,
        "chat_history": 0,
    }
    data = _call_tool(
        client,
        raw,
        "workflow_delete",
        {"workflow_id": 42, "opportunity_id": 100},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["deleted"]["definition"] == 1
    mock_wda_cls.return_value.delete_definition.assert_called_once_with(42, delete_linked=False)


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_delete_cascade_with_delete_linked(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_definition.return_value = MagicMock(id=42)
    mock_wda_cls.return_value.delete_definition.return_value = {
        "definition": 1,
        "render_code": 1,
        "runs": 3,
        "audit_sessions": 2,
        "chat_history": 1,
    }
    data = _call_tool(
        client,
        raw,
        "workflow_delete",
        {"workflow_id": 42, "opportunity_id": 100, "delete_linked": True},
    )
    assert data["result"]["isError"] is False
    mock_wda_cls.return_value.delete_definition.assert_called_once_with(42, delete_linked=True)


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_delete_not_found(mock_wda_cls, client, auth_user):
    _, raw = auth_user
    mock_wda_cls.return_value.get_definition.return_value = None
    data = _call_tool(
        client,
        raw,
        "workflow_delete",
        {"workflow_id": 999, "opportunity_id": 100},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "NOT_FOUND"
