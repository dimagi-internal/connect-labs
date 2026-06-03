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


@pytest.fixture
def asgi_app():
    """A fresh combined ASGI app per test.

    ``config.asgi.application`` is a process-wide singleton whose FastMCP
    ``StreamableHTTPSessionManager`` can only run its lifespan once. Tests that
    enter the lifespan in-process must each get their own instance, or the
    second one crashes with "session manager .run() can only be called once".
    The factory hands out an independent app (and session manager) per test.
    """
    from config.asgi import build_application

    return build_application()


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
def test_streamable_http_list_and_call_end_to_end(asgi_app):
    import anyio
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    application = asgi_app

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
def test_streamable_http_rejects_missing_token(asgi_app):
    import anyio
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    application = asgi_app

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


@pytest.mark.django_db(transaction=True)
def test_unauthenticated_challenge_is_plain_bearer_not_oauth(asgi_app):
    """A bearer-less request must get a plain ``Bearer realm`` challenge.

    FastMCP 3.x's TokenVerifier emits ``WWW-Authenticate: Bearer
    error="invalid_token", ...`` on 401. The RFC 6750 ``error="invalid_token"``
    marks the endpoint as an OAuth-protected resource, so a spec-compliant MCP
    client (Claude Code) responds by probing ``/.well-known/oauth-protected-
    resource`` — which Django answers with an HTML 404, crashing the client's
    JSON parse on reconnect. We rewrite the challenge back to the pre-FastMCP
    ``Bearer realm="labs-mcp"`` form, which clients satisfy by re-sending their
    PAT. Regression guard for the connect-labs reconnect break.
    """
    import anyio

    application = asgi_app

    async def _run():
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://testserver"
            ) as c:
                return await c.post(
                    "/mcp/",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "regression-probe", "version": "0"},
                        },
                    },
                )

    resp = anyio.run(_run)

    assert resp.status_code == 401
    challenge = resp.headers.get("www-authenticate", "")
    # The OAuth-discovery trigger must be gone...
    assert "error=" not in challenge, f"OAuth-style challenge leaked: {challenge!r}"
    assert "invalid_token" not in challenge, f"OAuth-style challenge leaked: {challenge!r}"
    # ...replaced by the plain realm challenge the old transport served.
    assert 'realm="labs-mcp"' in challenge, f"expected plain Bearer realm, got: {challenge!r}"


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
    ],
)
def test_oauth_discovery_paths_return_json_not_html(asgi_app, path):
    """OAuth discovery probes must return parseable JSON, never Django's HTML.

    The combined ASGI app mounts the MCP app under ``/mcp`` and Django as the
    root catch-all. A client doing RFC 9728 discovery probes these root paths;
    without an explicit route they fall through to Django's styled HTML 404,
    which the client cannot parse as the expected JSON metadata (``Unrecognized
    token '<'``). We serve a clean JSON 404 so discovery fails gracefully and
    the client falls back to its configured PAT.
    """
    import anyio

    application = asgi_app

    async def _run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.get(path)

    resp = anyio.run(_run)

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json"), resp.headers.get("content-type")
    # Must parse as JSON (would raise on Django's HTML 404 body).
    body = resp.json()
    assert body["error"] == "not_found"
