"""Tests for the get_opportunity_apps MCP tool."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.mcp.testing import call_tool
from commcare_connect.mcp.tool_registry import get_tool
from commcare_connect.users.models import User


@pytest.fixture
def auth_user(db):
    user = User.objects.create(username="apps-test")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    # client is unused: the MCP protocol endpoint is now a FastMCP ASGI app,
    # not a Django view. call_tool drives the same auth/handler/audit/rate-limit
    # path in-process and returns the same JSON-RPC-shaped envelope.
    return call_tool(raw_pat, tool_name, arguments)


def _mock_client():
    mock_client = MagicMock()
    mock_client.base_url = "https://connect.example.com"
    return mock_client


def test_registered_as_read_only():
    tool = get_tool("get_opportunity_apps")
    assert tool is not None
    assert tool.is_write is False


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.apps.LabsRecordAPIClient")
def test_happy_path_default_both(mock_client_cls, client, auth_user):
    _, raw = auth_user
    mock_client = _mock_client()
    mock_client_cls.return_value = mock_client

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "learn_app": {"name": "Learn", "modules": []},
        "deliver_app": {"name": "Deliver", "modules": []},
    }
    mock_client.http_client.get.return_value = fake_resp

    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["learn_app"]["name"] == "Learn"
    assert content["deliver_app"]["name"] == "Deliver"

    mock_client.http_client.get.assert_called_once()
    call = mock_client.http_client.get.call_args
    assert call.args[0] == "https://connect.example.com/export/opportunity/42/app_structure/"
    assert call.kwargs["params"] == {"app_type": "both"}
    mock_client.close.assert_called_once()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.apps.LabsRecordAPIClient")
def test_app_type_learn_only(mock_client_cls, client, auth_user):
    _, raw = auth_user
    mock_client = _mock_client()
    mock_client_cls.return_value = mock_client

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"learn_app": {"name": "Learn"}, "deliver_app": None}
    mock_client.http_client.get.return_value = fake_resp

    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 42, "app_type": "learn"})

    assert data["result"]["isError"] is False
    assert data["result"]["structuredContent"]["deliver_app"] is None
    assert mock_client.http_client.get.call_args.kwargs["params"] == {"app_type": "learn"}


@pytest.mark.django_db
def test_invalid_app_type_rejected(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 42, "app_type": "bogus"})
    # Schema validation in transport rejects before reaching the handler.
    assert data["result"]["isError"] is True


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.apps.LabsRecordAPIClient")
def test_404_maps_to_not_found(mock_client_cls, client, auth_user):
    _, raw = auth_user
    mock_client = _mock_client()
    mock_client_cls.return_value = mock_client

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 404
    mock_client.http_client.get.return_value = fake_resp

    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 9999})
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.apps.LabsRecordAPIClient")
def test_502_maps_to_upstream_error(mock_client_cls, client, auth_user):
    _, raw = auth_user
    mock_client = _mock_client()
    mock_client_cls.return_value = mock_client

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 502
    mock_client.http_client.get.return_value = fake_resp

    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 42})
    assert data["result"]["structuredContent"]["error"]["code"] == "UPSTREAM_ERROR"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.apps.LabsRecordAPIClient")
def test_request_error_maps_to_upstream_error(mock_client_cls, client, auth_user):
    _, raw = auth_user
    mock_client = _mock_client()
    mock_client_cls.return_value = mock_client
    mock_client.http_client.get.side_effect = httpx.ConnectError("dns fail")

    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 42})
    assert data["result"]["structuredContent"]["error"]["code"] == "UPSTREAM_ERROR"


@pytest.mark.django_db
def test_requires_connect_token(client, db):
    user = User.objects.create(username="no-conn-apps")
    _, raw = MCPAccessToken.create_token(user, name="t")

    data = _call_tool(client, raw, "get_opportunity_apps", {"opportunity_id": 42})
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
