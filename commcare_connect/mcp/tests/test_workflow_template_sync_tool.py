"""Tests for workflow_sync_from_template_file MCP tool.

Mocks WorkflowDataAccess and PipelineDataAccess so we don't hit the real
Connect API. Calls go through the JSON-RPC transport, same as the rest of
the MCP test suite — keeps schema validation and error formatting honest.
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
    user = User.objects.create(username="synctest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw, arguments):
    resp = client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "workflow_sync_from_template_file",
                    "arguments": arguments,
                },
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    return resp.json()


_SIMPLE_TEMPLATE_SOURCE = """
DEFINITION = {"name": "X", "statuses": [], "pipeline_sources": [], "version": 1}
RENDER_CODE = "function WorkflowUI() { return null; }"
TEMPLATE = {"key": "x", "definition": DEFINITION, "render_code": RENDER_CODE}
"""


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.workflow_template_sync.WorkflowDataAccess")
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
