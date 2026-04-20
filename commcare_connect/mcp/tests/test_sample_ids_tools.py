"""Tests for the migrated get_sample_ids MCP tool."""

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
    """User with a PAT AND a UserConnectToken (fully set up for tool calls)."""
    user = User.objects.create(username="sids-test")
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
@patch("commcare_connect.mcp.tools.sample_ids.LabsRecordAPIClient")
def test_get_sample_ids_happy_path(mock_client_cls, client, auth_user):
    """Tool returns a dict with funds/solicitations/programs lists from mocked API."""
    _, raw = auth_user

    # Build mock client with working http_client for the org-data call
    mock_client = MagicMock()
    mock_client.base_url = "https://connect.example.com"
    mock_client_cls.return_value = mock_client

    # Mock the /export/opp_org_program_list/ response via http_client
    mock_org_resp = MagicMock()
    mock_org_resp.json.return_value = {
        "programs": [{"id": 25, "name": "Test Program"}],
    }
    mock_client.http_client.get.return_value = mock_org_resp

    # Mock solicitations from get_records(type="solicitation")
    mock_sol = MagicMock()
    mock_sol.id = 101
    mock_sol.data = {"title": "Test Solicitation"}

    # Mock funds from get_records(type="fund")
    mock_fund = MagicMock()
    mock_fund.id = 201
    mock_fund.data = {"name": "Test Fund"}

    def _get_records_side_effect(**kwargs):
        if kwargs.get("type") == "solicitation":
            return [mock_sol]
        if kwargs.get("type") == "fund":
            return [mock_fund]
        return []

    mock_client.get_records.side_effect = _get_records_side_effect

    data = _call_tool(client, raw, "get_sample_ids", {})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]

    # Response shape must match the original: funds, solicitations, programs
    assert isinstance(content, dict)
    assert set(content.keys()) == {"funds", "solicitations", "programs"}

    assert content["programs"] == [{"id": 25, "name": "Test Program"}]
    assert content["solicitations"] == [{"id": 101, "name": "Test Solicitation"}]
    assert content["funds"] == [{"id": 201, "name": "Test Fund"}]

    # Verify solicitations were scoped by the first program_id
    mock_client.get_records.assert_any_call(type="solicitation", program_id=25)
    mock_client.get_records.assert_any_call(type="fund", program_id=25)


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.sample_ids.LabsRecordAPIClient")
def test_get_sample_ids_partial_failure_still_returns(mock_client_cls, client, auth_user):
    """If the org-data call fails, solicitations/funds are still attempted without a scope."""
    _, raw = auth_user

    mock_client = MagicMock()
    mock_client.base_url = "https://connect.example.com"
    mock_client_cls.return_value = mock_client

    # Simulate org-data call failure
    mock_client.http_client.get.side_effect = Exception("network error")

    # Funds succeed, solicitations empty
    mock_fund = MagicMock()
    mock_fund.id = 202
    mock_fund.data = {"name": "Fallback Fund"}

    def _get_records_side_effect(**kwargs):
        if kwargs.get("type") == "fund":
            return [mock_fund]
        return []

    mock_client.get_records.side_effect = _get_records_side_effect

    data = _call_tool(client, raw, "get_sample_ids", {})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["programs"] == []
    assert content["solicitations"] == []
    assert content["funds"] == [{"id": 202, "name": "Fallback Fund"}]

    # With no program found, scope should be None
    mock_client.get_records.assert_any_call(type="fund", program_id=None)


@pytest.mark.django_db
def test_get_sample_ids_requires_connect_token(client, db):
    """Tool must fail gracefully when the user has no Connect token."""
    user = User.objects.create(username="no-conn-sids")
    _, raw = MCPAccessToken.create_token(user, name="t")

    data = _call_tool(client, raw, "get_sample_ids", {})
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
