"""Tests for the FastMCP 3.x server (replaces the hand-rolled transport).

Covers:
  * CommCarePATVerifier — valid / invalid / revoked / expired token resolution
    (the per-user auth contract; tools run AS the resolved user).
  * tool-name parity — the FastMCP surface advertises the SAME tool set the
    legacy registry did.
  * protocol — initialize + tools/list work via the FastMCP in-memory client
    (FastMCP owns the JSON-RPC envelope now).
  * _run_registry_tool surfaces LabsAPIError upstream detail inline (the
    behavior the old transport._internal_error_response provided).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.server import CommCarePATVerifier, mcp
from connect_labs.mcp.tool_registry import list_tools as registry_list_tools
from connect_labs.users.models import User

# ---------------------------------------------------------------------------
# Custom-verifier tests (valid/invalid/revoked/expired -> user/None)
# ---------------------------------------------------------------------------


def _verify(raw):
    return asyncio.run(CommCarePATVerifier().verify_token(raw))


# transaction=True: verify_token resolves the user via sync_to_async, which runs
# the ORM on a threadpool thread with its OWN DB connection. Under the default
# (transactional) django_db fixture the test's seeded rows are invisible to that
# connection, so the lookup misses. transaction=True commits the rows so the
# threadpool connection can see them. (This is the exact isolation pitfall called
# out in the task brief — and it keeps these rows from leaking by using a truncating
# teardown rather than a never-committed transaction.)
@pytest.mark.django_db(transaction=True)
def test_verifier_valid_token_resolves_user():
    user = User.objects.create(username="verify-ok")
    _, raw = MCPAccessToken.create_token(user, name="t")
    access = _verify(raw)
    assert access is not None
    assert access.claims["user_id"] == user.pk
    assert access.claims["sub"] == str(user.pk)
    assert access.claims["auth_method"] == "pat"


@pytest.mark.django_db(transaction=True)  # see note above: async verify_token uses a threadpool connection
def test_verifier_valid_token_touches_last_used():
    user = User.objects.create(username="verify-touch")
    token, raw = MCPAccessToken.create_token(user, name="t")
    assert token.last_used_at is None
    before = timezone.now()
    assert _verify(raw) is not None
    token.refresh_from_db()
    assert token.last_used_at is not None
    assert token.last_used_at >= before


@pytest.mark.django_db
def test_verifier_invalid_token_returns_none():
    assert _verify("garbage-token") is None


def test_verifier_empty_token_returns_none():
    assert _verify("") is None
    assert _verify(None) is None


@pytest.mark.django_db(transaction=True)  # async verify_token threadpool connection must see the seeded row
def test_verifier_revoked_token_returns_none():
    user = User.objects.create(username="verify-revoked")
    token, raw = MCPAccessToken.create_token(user, name="t")
    token.is_active = False
    token.save()
    assert _verify(raw) is None


@pytest.mark.django_db(transaction=True)  # async verify_token threadpool connection must see the seeded row
def test_verifier_expired_token_returns_none():
    user = User.objects.create(username="verify-expired")
    token, raw = MCPAccessToken.create_token(user, name="t")
    token.expires_at = timezone.now() - timedelta(days=1)
    token.save()
    assert _verify(raw) is None


# ---------------------------------------------------------------------------
# Tool-name parity + protocol
# ---------------------------------------------------------------------------


def test_tool_names_match_legacy_registry():
    """The FastMCP surface must advertise EXACTLY the legacy registry's tools."""
    legacy = {t["name"] for t in registry_list_tools()}
    fastmcp_tools = asyncio.run(mcp.list_tools())
    surfaced = {t.name for t in fastmcp_tools}
    assert surfaced == legacy


def test_tool_schemas_match_legacy_registry():
    """input_schema is preserved byte-for-byte as the advertised parameters."""
    legacy = {t["name"]: t["inputSchema"] for t in registry_list_tools()}
    fastmcp_tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name, schema in legacy.items():
        assert fastmcp_tools[name].parameters == schema, name


def test_initialize_and_list_via_inmemory_client():
    """FastMCP owns initialize/tools/list now; an in-memory client exercises it."""
    from fastmcp import Client

    async def _run():
        async with Client(mcp) as client:
            await client.ping()
            tools = await client.list_tools()
            return [t.name for t in tools]

    names = asyncio.run(_run())
    assert "workflow_list" in names
    assert "list_funds" in names


# ---------------------------------------------------------------------------
# Upstream-error detail (ported from the old _internal_error_response tests)
# ---------------------------------------------------------------------------


def test_run_registry_tool_surfaces_labs_api_detail(monkeypatch):
    """An unhandled LabsAPIError must surface upstream status + body in the
    ToolError message — the diagnostic the old transport put in error.data."""
    from fastmcp.exceptions import ToolError

    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.mcp import server
    from connect_labs.mcp.tool_registry import Tool as RegistryToolSpec

    def _boom(user):
        raise LabsAPIError("upstream boom", status_code=403, body="forbidden body")

    spec = RegistryToolSpec(
        name="explode", description="d", input_schema={"type": "object"}, handler=_boom, is_write=False
    )
    # No access token in context -> user is None; _write_audit is best-effort
    # and swallows the absent DB row, so the error path still runs.
    monkeypatch.setattr(server, "_write_audit", lambda *a, **k: None)
    monkeypatch.setattr(server, "current_user", lambda: None)

    with pytest.raises(ToolError) as excinfo:
        server._run_registry_tool(spec, {})
    msg = str(excinfo.value)
    assert "LabsAPIError" in msg
    assert "upstream boom" in msg
    assert "upstream_status=403" in msg
    assert "forbidden body" in msg


def test_run_registry_tool_maps_mcptoolerror_code(monkeypatch):
    """An MCPToolError raised by a handler becomes a ToolError carrying its
    message; the original code is recoverable via the cause chain."""
    from fastmcp.exceptions import ToolError

    from connect_labs.mcp import server
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tool_registry import Tool as RegistryToolSpec

    def _denied(user):
        raise MCPToolError("PERMISSION_DENIED", "nope")

    spec = RegistryToolSpec(
        name="denytool", description="d", input_schema={"type": "object"}, handler=_denied, is_write=False
    )
    monkeypatch.setattr(server, "_write_audit", lambda *a, **k: None)
    monkeypatch.setattr(server, "current_user", lambda: None)

    with pytest.raises(ToolError) as excinfo:
        server._run_registry_tool(spec, {})
    assert "nope" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, MCPToolError)
    assert excinfo.value.__cause__.code == "PERMISSION_DENIED"
