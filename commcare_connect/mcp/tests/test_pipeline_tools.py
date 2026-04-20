# commcare_connect/mcp/tests/test_pipeline_tools.py
"""Tests for pipeline_* MCP tools.

Mirror the workflow tool test pattern: mock PipelineDataAccess, avoid
hitting the real API.
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
    """User with a PAT AND a UserConnectToken (fully set up)."""
    user = User.objects.create(username="pltest")
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
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_list_returns_pipelines_for_opportunity(mock_pda_cls, client, auth_user):
    _, raw = auth_user

    mock_def = MagicMock(
        id=7,
        description="Test",
        version=2,
        data={"version": 2, "schema": {"fields": [{"name": "a"}]}, "updated_at": None},
    )
    mock_def.name = "MyPipeline"
    mock_pda_cls.return_value.list_definitions.return_value = [mock_def]

    data = _call_tool(client, raw, "pipeline_list", {"opportunity_id": 100})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert len(content["pipelines"]) == 1
    assert content["pipelines"][0]["id"] == 7
    assert content["pipelines"][0]["name"] == "MyPipeline"
    assert content["pipelines"][0]["version"] == 2


@pytest.mark.django_db
def test_pipeline_list_rejects_missing_scope(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "pipeline_list", {})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_pipeline_list_rejects_multiple_scopes(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "pipeline_list", {"opportunity_id": 1, "program_id": 2})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_get_returns_full_schema(mock_pda_cls, client, auth_user):
    _, raw = auth_user

    mock_def = MagicMock(
        id=7,
        description="Test",
        version=3,
        schema={
            "fields": [
                {"name": "visits", "aggregation": "count"},
                {"name": "flw", "aggregation": "count_distinct"},
            ]
        },
        data={
            "version": 3,
            "schema": {
                "fields": [
                    {"name": "visits", "aggregation": "count"},
                    {"name": "flw", "aggregation": "count_distinct"},
                ]
            },
        },
    )
    mock_def.name = "Perf Pipeline"
    mock_pda_cls.return_value.get_definition.return_value = mock_def

    data = _call_tool(client, raw, "pipeline_get", {"pipeline_id": 7, "opportunity_id": 100})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 7
    assert content["version"] == 3
    assert len(content["schema"]["fields"]) == 2


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_get_not_found(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = None
    data = _call_tool(client, raw, "pipeline_get", {"pipeline_id": 999, "opportunity_id": 100})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_pipeline_tools_reject_user_without_connect_token(client, db):
    user = User.objects.create(username="no-connect-pl")
    _, raw = MCPAccessToken.create_token(user, name="t")
    data = _call_tool(client, raw, "pipeline_list", {"opportunity_id": 1})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
