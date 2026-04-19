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
