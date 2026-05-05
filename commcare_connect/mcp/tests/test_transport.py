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


def test_internal_error_envelope_includes_labs_api_detail():
    """Unhandled LabsAPIError must surface upstream HTTP status + body in the envelope.

    Before this fix the envelope was a bare `{"error": {"code": -32603, "message": "Internal
    error"}}` — every upstream failure looked identical and was un-debuggable from outside the
    labs server. The fix populates JSON-RPC's optional `error.data` with exception type,
    request_id, and (for LabsAPIError) upstream status + body.
    """
    import json as _json

    from commcare_connect.labs.integrations.connect.api_client import LabsAPIError
    from commcare_connect.mcp.transport import _internal_error_response

    exc = LabsAPIError("upstream boom", status_code=403, body="forbidden body")
    response = _internal_error_response(msg_id=42, exc=exc, request_id="abc12345")

    payload = _json.loads(response.content)
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 42
    assert payload["error"]["code"] == -32603
    assert "LabsAPIError" in payload["error"]["message"]
    assert "upstream boom" in payload["error"]["message"]

    detail = payload["error"]["data"]
    assert detail["exception_type"] == "LabsAPIError"
    assert detail["upstream_status"] == 403
    assert detail["upstream_body"] == "forbidden body"
    assert detail["request_id"] == "abc12345"


def test_internal_error_envelope_for_non_labs_exception():
    """Non-LabsAPIError exceptions still surface type + request_id, just no upstream fields."""
    import json as _json

    from commcare_connect.mcp.transport import _internal_error_response

    response = _internal_error_response(msg_id=1, exc=ValueError("unexpected"), request_id="xyz")
    payload = _json.loads(response.content)

    assert payload["error"]["code"] == -32603
    assert "ValueError" in payload["error"]["message"]
    assert "unexpected" in payload["error"]["message"]

    detail = payload["error"]["data"]
    assert detail["exception_type"] == "ValueError"
    assert detail["request_id"] == "xyz"
    assert "upstream_status" not in detail
    assert "upstream_body" not in detail
