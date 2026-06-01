"""Auth behavior of the FastMCP MCP server, exercised through the in-process
bridge (commcare_connect.mcp.testing.call_tool), which resolves the raw PAT
through the real CommCarePATVerifier before running a tool.

The HTTP/transport-level 401 envelope is now produced by FastMCP itself; the
authentication DECISION (which token resolves to which user, and which are
rejected) is what these tests pin down. See test_server.py for the verifier's
return-value contract.
"""

import pytest

from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.mcp.testing import call_tool
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_missing_token_is_rejected():
    """No token -> PERMISSION_DENIED, tool never runs."""
    resp = call_tool(None, "list_templates", {})
    assert resp["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_invalid_token_is_rejected():
    resp = call_tool("garbage-token", "list_templates", {})
    assert resp["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_valid_token_runs_tool_as_user():
    user = User.objects.create(username="mcp-auth-test")
    _, raw = MCPAccessToken.create_token(user, name="t")
    # list_templates is read-only and needs no Connect token, so it runs clean
    # for any authenticated user.
    resp = call_tool(raw, "list_templates", {})
    assert resp["result"]["isError"] is False
    assert "templates" in resp["result"]["structuredContent"]


@pytest.mark.django_db
def test_revoked_token_is_rejected():
    user = User.objects.create(username="revoked-test")
    token, raw = MCPAccessToken.create_token(user, name="t")
    token.is_active = False
    token.save()
    resp = call_tool(raw, "list_templates", {})
    assert resp["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_valid_token_updates_last_used():
    from django.utils import timezone

    user = User.objects.create(username="last-used-test")
    token, raw = MCPAccessToken.create_token(user, name="t")
    assert token.last_used_at is None
    before = timezone.now()
    call_tool(raw, "list_templates", {})
    token.refresh_from_db()
    assert token.last_used_at is not None
    assert token.last_used_at >= before
