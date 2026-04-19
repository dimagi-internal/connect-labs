import json

import pytest
from django.urls import reverse


def _rpc(client, method: str, params: dict | None = None, msg_id: int = 1):
    """Helper to send a JSON-RPC request and return the parsed response body."""
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post(reverse("mcp:endpoint"), data=json.dumps(body), content_type="application/json")
    return response, response.json()


@pytest.mark.django_db
def test_initialize_returns_server_info(client):
    resp, data = _rpc(client, "initialize", {"protocolVersion": "2024-11-05"})
    assert resp.status_code == 200
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"]["serverInfo"]["name"] == "connect_labs"
    assert "capabilities" in data["result"]


@pytest.mark.django_db
def test_tools_list_returns_empty_catalog(client):
    resp, data = _rpc(client, "tools/list")
    assert resp.status_code == 200
    assert data["result"] == {"tools": []}


@pytest.mark.django_db
def test_tools_call_unknown_tool_returns_not_found(client):
    resp, data = _rpc(client, "tools/call", {"name": "nonexistent", "arguments": {}})
    assert resp.status_code == 200
    # Error is inside the result object per MCP spec, not at top level
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_malformed_json_returns_parse_error(client):
    resp = client.post(reverse("mcp:endpoint"), data="not json", content_type="application/json")
    data = resp.json()
    assert data["error"]["code"] == -32700


@pytest.mark.django_db
def test_unknown_method_returns_method_not_found(client):
    resp, data = _rpc(client, "does_not_exist")
    assert data["error"]["code"] == -32601
