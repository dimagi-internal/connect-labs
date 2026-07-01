"""Tests for workflow_sync_from_template_file MCP tool.

Mocks WorkflowDataAccess and PipelineDataAccess so we don't hit the real
Connect API. Calls go through the JSON-RPC transport, same as the rest of
the MCP test suite — keeps schema validation and error formatting honest.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User


@pytest.fixture
def auth_user(db):
    user = User.objects.create(username="synctest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw, arguments):
    # client unused — the MCP endpoint is now a FastMCP ASGI app; call_tool
    # drives the same in-process path and returns the JSON-RPC-shaped envelope.
    return call_tool(raw, "workflow_sync_from_template_file", arguments)


_SIMPLE_TEMPLATE_SOURCE = """
DEFINITION = {"name": "X", "statuses": [], "pipeline_sources": [], "version": 1}
RENDER_CODE = "function WorkflowUI() { return null; }"
TEMPLATE = {"key": "x", "definition": DEFINITION, "render_code": RENDER_CODE}
"""


_TEMPLATE_WITH_PIPELINE = """
DEFINITION = {
    "name": "X",
    "statuses": [],
    "pipeline_sources": [{"pipeline_id": 100, "alias": "visits"}],
    "version": 1,
}
RENDER_CODE = "function WorkflowUI() { return null; }"

VISITS_SCHEMA = {"fields": [{"name": "form_name", "path": "form.@name"}]}

PIPELINE_SCHEMAS = [
    {"alias": "visits", "name": "Visits", "schema": VISITS_SCHEMA},
]

TEMPLATE = {"key": "x", "definition": DEFINITION, "render_code": RENDER_CODE,
            "pipeline_schemas": PIPELINE_SCHEMAS}
"""


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_dry_run_returns_diff_without_writes(mock_wda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.id = 42
    current_def.data = {"name": "X-old", "statuses": [], "pipeline_sources": [], "version": 7}
    current_def.template_type = "x"

    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "function WorkflowUI() { return 'old'; }"

    instance = MagicMock()
    instance.get_definition.return_value = current_def
    instance.get_render_code.return_value = current_render
    mock_wda.return_value = instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _SIMPLE_TEMPLATE_SOURCE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,
            "dry_run": True,
        },
    )

    assert data["result"]["isError"] is False, data
    payload = data["result"]["structuredContent"]
    assert payload["workflow_id"] == 42
    assert payload["dry_run"] is True
    assert payload["render_code"]["version_before"] == 11
    assert payload["render_code"]["version_after"] == 11  # dry_run leaves it alone
    assert payload["render_code"]["changed"] is True
    assert "name" in payload["definition"]["changed_keys"]

    # No writes on dry_run.
    instance.update_definition.assert_not_called()
    instance.save_render_code.assert_not_called()


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_definition_version_conflict_rejected(mock_wda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.id = 42
    current_def.data = {"name": "X", "statuses": [], "pipeline_sources": [], "version": 999}
    current_def.template_type = "x"

    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "function WorkflowUI() { return null; }"

    instance = MagicMock()
    instance.get_definition.return_value = current_def
    instance.get_render_code.return_value = current_render
    mock_wda.return_value = instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _SIMPLE_TEMPLATE_SOURCE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,  # Mismatch: actual is 999
            "dry_run": True,
        },
    )

    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "VERSION_CONFLICT"
    assert "version 999" in data["result"]["structuredContent"]["error"]["message"]


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_render_code_version_conflict_rejected(mock_wda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.id = 42
    current_def.data = {"name": "X", "statuses": [], "pipeline_sources": [], "version": 7}
    current_def.template_type = "x"

    current_render = MagicMock()
    current_render.version = 999  # Mismatch
    current_render.component_code = "function WorkflowUI() { return null; }"

    instance = MagicMock()
    instance.get_definition.return_value = current_def
    instance.get_render_code.return_value = current_render
    mock_wda.return_value = instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _SIMPLE_TEMPLATE_SOURCE,
            "expected_render_code_version": 11,  # Mismatch: actual is 999
            "expected_definition_version": 7,
            "dry_run": True,
        },
    )

    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "VERSION_CONFLICT"
    assert "version 999" in data["result"]["structuredContent"]["error"]["message"]


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_template_key_mismatch_rejected(mock_wda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.id = 42
    current_def.data = {"name": "X", "statuses": [], "pipeline_sources": [], "version": 7}
    current_def.template_type = "old_key"  # Mismatch: template has "x"

    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "function WorkflowUI() { return null; }"

    instance = MagicMock()
    instance.get_definition.return_value = current_def
    instance.get_render_code.return_value = current_render
    mock_wda.return_value = instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _SIMPLE_TEMPLATE_SOURCE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,
            "dry_run": True,
        },
    )

    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "TEMPLATE_KEY_MISMATCH"
    assert "old_key" in data["result"]["structuredContent"]["error"]["message"]
    assert "'x'" in data["result"]["structuredContent"]["error"]["message"]


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_writes_happen_when_dry_run_false(mock_wda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.id = 42
    current_def.data = {"name": "X-old", "statuses": [], "pipeline_sources": [], "version": 7}
    current_def.template_type = "x"

    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "function WorkflowUI() { return 'old'; }"

    new_render = MagicMock()
    new_render.version = 12

    instance = MagicMock()
    instance.get_definition.return_value = current_def
    instance.get_render_code.return_value = current_render
    instance.save_render_code.return_value = new_render
    mock_wda.return_value = instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _SIMPLE_TEMPLATE_SOURCE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,
            "dry_run": False,  # Enable writes
        },
    )

    assert data["result"]["isError"] is False, data
    payload = data["result"]["structuredContent"]
    assert payload["workflow_id"] == 42
    assert payload["dry_run"] is False
    assert payload["render_code"]["version_after"] == 12
    assert payload["definition"]["version_after"] == 8

    # update_definition takes (definition_id=, data=); version is inside the data dict.
    instance.update_definition.assert_called_once()
    update_kwargs = instance.update_definition.call_args.kwargs
    assert update_kwargs["definition_id"] == 42
    assert update_kwargs["data"]["name"] == "X"
    assert update_kwargs["data"]["version"] == 8

    # save_render_code takes (definition_id=, component_code=, version=) — version is the new value.
    instance.save_render_code.assert_called_once()
    save_kwargs = instance.save_render_code.call_args.kwargs
    assert save_kwargs["definition_id"] == 42
    assert save_kwargs["component_code"] == "function WorkflowUI() { return null; }"
    assert save_kwargs["version"] == 12


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.PipelineDataAccess")
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_pipeline_schemas_updated_by_alias(mock_wda, mock_pda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.id = 42
    current_def.data = {
        "name": "X",
        "statuses": [],
        "pipeline_sources": [{"pipeline_id": 100, "alias": "visits"}],
        "version": 7,
    }
    current_def.template_type = "x"

    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "old"

    new_render = MagicMock()
    new_render.version = 12

    wda_instance = MagicMock()
    wda_instance.get_definition.return_value = current_def
    wda_instance.get_render_code.return_value = current_render
    wda_instance.save_render_code.return_value = new_render
    mock_wda.return_value = wda_instance

    current_pipe = MagicMock()
    current_pipe.id = 100
    current_pipe.version = 3
    current_pipe.data = {"schema": {"fields": []}}
    new_pipe = MagicMock()
    new_pipe.version = 4

    pda_instance = MagicMock()
    pda_instance.get_definition.return_value = current_pipe
    pda_instance.update_definition.return_value = new_pipe
    mock_pda.return_value = pda_instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _TEMPLATE_WITH_PIPELINE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,
            "dry_run": False,
        },
    )

    assert data["result"]["isError"] is False, data
    payload = data["result"]["structuredContent"]
    assert len(payload["pipelines"]) == 1
    p = payload["pipelines"][0]
    assert p["alias"] == "visits"
    assert p["pipeline_id"] == 100
    assert p["schema_version_before"] == 3
    assert p["schema_version_after"] == 4
    assert p["changed"] is True

    args, kwargs = pda_instance.update_definition.call_args
    assert kwargs["definition_id"] == 100
    assert kwargs["schema"] == {"fields": [{"name": "form_name", "path": "form.@name"}]}


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.PipelineDataAccess")
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_partial_sync_when_pipeline_update_fails(mock_wda, mock_pda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.data = {
        "name": "X",
        "statuses": [],
        "pipeline_sources": [{"pipeline_id": 100, "alias": "visits"}],
        "version": 7,
    }
    current_def.template_type = "x"

    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "old"
    new_render = MagicMock()
    new_render.version = 12

    wda_instance = MagicMock()
    wda_instance.get_definition.return_value = current_def
    wda_instance.get_render_code.return_value = current_render
    wda_instance.save_render_code.return_value = new_render
    mock_wda.return_value = wda_instance

    pda_instance = MagicMock()
    pda_instance.get_definition.side_effect = RuntimeError("upstream 502")
    mock_pda.return_value = pda_instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _TEMPLATE_WITH_PIPELINE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,
            "dry_run": False,
        },
    )

    err = data["result"]["structuredContent"]["error"]
    assert data["result"]["isError"] is True
    assert err["code"] == "PARTIAL_SYNC"
    # Definition + render_code went through before the pipeline failure.
    assert err["details"]["written"] == ["definition", "render_code"]
    assert err["details"]["failed_at"]["phase"] == "pipeline"
    assert err["details"]["failed_at"]["alias"] == "visits"


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflow_template_sync.WorkflowDataAccess")
def test_pipeline_alias_missing_from_workflow_rejects_pre_write(mock_wda, client, auth_user):
    _, raw = auth_user

    current_def = MagicMock()
    current_def.data = {
        "name": "X",
        "statuses": [],
        "pipeline_sources": [{"pipeline_id": 200, "alias": "other_alias"}],
        "version": 7,
    }
    current_def.template_type = "x"
    current_render = MagicMock()
    current_render.version = 11
    current_render.component_code = "old"

    instance = MagicMock()
    instance.get_definition.return_value = current_def
    instance.get_render_code.return_value = current_render
    mock_wda.return_value = instance

    data = _call_tool(
        client,
        raw,
        {
            "workflow_id": 42,
            "opportunity_id": 9,
            "template_source": _TEMPLATE_WITH_PIPELINE,
            "expected_render_code_version": 11,
            "expected_definition_version": 7,
            "dry_run": False,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert data["result"]["isError"] is True
    assert err["code"] == "PIPELINE_ALIAS_NOT_FOUND"
    # No writes should have happened.
    instance.update_definition.assert_not_called()
    instance.save_render_code.assert_not_called()
