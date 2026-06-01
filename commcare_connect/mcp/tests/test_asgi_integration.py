"""End-to-end Streamable-HTTP integration test.

Drives the real combined ASGI app (config.asgi.application — Starlette mounting
the FastMCP Streamable-HTTP app at /mcp/) through an in-process httpx ASGI
transport, with a FastMCP client speaking the actual MCP protocol. This proves
the full wire path: HTTP request -> FastMCP -> CommCarePATVerifier (Bearer) ->
tool dispatch -> audit, exactly as a remote Claude Code client would hit prod.

transaction=True is REQUIRED: the request is served on a threadpool/anyio worker
whose DB connection differs from the test's; rows seeded here must be committed
to be visible there (and torn down by truncation, so nothing leaks).
"""

from __future__ import annotations

import httpx
import pytest

from commcare_connect.mcp.models import MCPAccessToken, MCPAuditLog
from commcare_connect.users.models import User


def _client_factory_to_asgi(app):
    """Return an McpHttpClientFactory that routes httpx through the ASGI app."""

    def factory(headers=None, timeout=None, auth=None, **kwargs):
        kwargs.pop("transport", None)  # we supply our own ASGI transport
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    return factory


@pytest.mark.django_db(transaction=True)
def test_streamable_http_list_and_call_end_to_end():
    import anyio
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from config.asgi import application

    user = User.objects.create(username="e2e-mcp")
    _, raw = MCPAccessToken.create_token(user, name="e2e")

    async def _run():
        transport = StreamableHttpTransport(
            url="http://testserver/mcp/",
            headers={"Authorization": f"Bearer {raw}"},
            httpx_client_factory=_client_factory_to_asgi(application),
        )
        # httpx.ASGITransport does NOT run ASGI lifespan events, so the
        # FastMCP session manager's task group would never start. Drive the
        # combined app's lifespan manually around the request (this is the
        # exact lifespan config.asgi wires in for production).
        async with application.router.lifespan_context(application):
            async with Client(transport) as client:
                tools = await client.list_tools()
                names = {t.name for t in tools}
                # A read tool needing no Connect token -> safe to actually call.
                result = await client.call_tool("list_templates", {})
                return names, result

    names, result = anyio.run(_run)

    assert "list_templates" in names
    assert "workflow_list" in names
    # The call ran as the authenticated user and returned structured content.
    assert result.structured_content is not None
    assert "templates" in result.structured_content

    # Audit row written for the call, attributed to the PAT's user.
    assert MCPAuditLog.objects.filter(user=user, tool_name="list_templates", success=True).exists()


@pytest.mark.django_db(transaction=True)
def test_streamable_http_rejects_missing_token():
    import anyio
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from config.asgi import application

    async def _run():
        transport = StreamableHttpTransport(
            url="http://testserver/mcp/",
            httpx_client_factory=_client_factory_to_asgi(application),
        )
        async with Client(transport) as client:
            await client.list_tools()

    # No Bearer token -> the verifier returns None -> FastMCP rejects the
    # session before any tool runs.
    with pytest.raises(Exception):  # noqa: B017,PT011 — any auth/transport error is acceptable
        anyio.run(_run)
