import json

import pytest
from django.urls import reverse

from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.fixture
def auth_header(db):
    user = User.objects.create(username="mcp-test-user")
    _, raw = MCPAccessToken.create_token(user, name="pytest")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def _rpc(client, method: str, params: dict | None = None, msg_id: int = 1, headers: dict | None = None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    extra = headers or {}
    response = client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(body),
        content_type="application/json",
        **extra,
    )
    return response, response.json()


@pytest.mark.django_db
def test_initialize_returns_server_info(client, auth_header):
    resp, data = _rpc(client, "initialize", {"protocolVersion": "2024-11-05"}, headers=auth_header)
    assert resp.status_code == 200
    assert data["result"]["serverInfo"]["name"] == "connect_labs"


@pytest.mark.django_db
def test_tools_list_returns_catalog(client, auth_header):
    resp, data = _rpc(client, "tools/list", headers=auth_header)
    assert resp.status_code == 200
    tool_names = [t["name"] for t in data["result"]["tools"]]
    assert "workflow_list" in tool_names


@pytest.mark.django_db
def test_tools_call_unknown_tool_returns_not_found(client, auth_header):
    resp, data = _rpc(client, "tools/call", {"name": "nonexistent", "arguments": {}}, headers=auth_header)
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_malformed_json_returns_parse_error(client, auth_header):
    resp = client.post(
        reverse("mcp:endpoint"),
        data="not json",
        content_type="application/json",
        **auth_header,
    )
    data = resp.json()
    assert data["error"]["code"] == -32700


@pytest.mark.django_db
def test_unknown_method_returns_method_not_found(client, auth_header):
    resp, data = _rpc(client, "does_not_exist", headers=auth_header)
    assert data["error"]["code"] == -32601
