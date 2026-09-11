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

from unittest import mock

import httpx
import pytest

from connect_labs.mcp.models import MCPAccessToken, MCPAuditLog
from connect_labs.users.models import User


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


def _post_without_valid_auth(application, method="initialize", authorization=None):
    """POST one MCP request that the verifier will reject; return the response."""
    import anyio

    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if method == "initialize":
        payload["params"] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "regression-probe", "version": "0"},
        }

    async def _run():
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://testserver"
            ) as c:
                return await c.post("/mcp/", headers=headers, json=payload)

    return anyio.run(_run)


@pytest.mark.django_db(transaction=True)
def test_unauthenticated_challenge_points_clients_at_the_sign_in(asgi_app):
    """A bearer-less request gets the MCP spec's 401: where to sign in, and no error code.

    A spec-compliant MCP client reads ``resource_metadata`` from this challenge
    and starts the sign-in from it, so it must name the protected-resource
    metadata this server actually serves. A request that carried no token gets
    no ``error=`` at all (RFC 6750 section 3.1).

    History: FastMCP's default challenge -- ``error="invalid_token"`` and no
    ``resource_metadata`` -- sent clients to discovery paths Django answered with
    an HTML 404, which broke reconnect (#431). Those paths now serve real JSON
    metadata (see the discovery tests below), so pointing clients at them is
    the fix rather than the fault.
    """
    from connect_labs.mcp import oauth

    resp = _post_without_valid_auth(asgi_app)

    assert resp.status_code == 401
    challenge = resp.headers.get("www-authenticate", "")
    assert 'realm="labs-mcp"' in challenge, challenge
    assert f'resource_metadata="{oauth.protected_resource_metadata_url()}"' in challenge, challenge
    assert "error=" not in challenge, f"a request with no token must carry no error code: {challenge!r}"


@pytest.mark.django_db(transaction=True)
def test_a_rejected_token_is_told_invalid_token_and_where_to_sign_in(asgi_app):
    from connect_labs.mcp import oauth

    resp = _post_without_valid_auth(asgi_app, authorization="Bearer not-a-real-token")

    assert resp.status_code == 401
    challenge = resp.headers.get("www-authenticate", "")
    assert 'error="invalid_token"' in challenge, challenge
    assert f'resource_metadata="{oauth.protected_resource_metadata_url()}"' in challenge, challenge
    assert resp.json()["error"] == "invalid_token"


@pytest.mark.django_db(transaction=True)
def test_the_401_body_says_which_failure_this_was_and_names_both_ways_in(asgi_app):
    """FastMCP's 401 prose cannot tell a missing header from a rejected token.

    A client whose PAT header helper fails silently sends NO header, and with
    FastMCP's body that is indistinguishable from a revoked token -- a live
    debugging session lost time to exactly that (#431). The body now says which
    it was, and names both ways in: the sign-in, and a Personal Access Token.
    """
    from config.asgi import build_application

    missing = _post_without_valid_auth(asgi_app, method="tools/list")
    # A second request needs a second app: the session manager's lifespan runs once per instance.
    rejected = _post_without_valid_auth(
        build_application(), method="tools/list", authorization="Bearer revoked-or-typoed"
    )

    for resp in (missing, rejected):
        assert resp.status_code == 401
        described = resp.json()["error_description"]
        # FastMCP's exact words, not a paraphrase, so the assertion cannot drift.
        assert "clear authentication tokens" not in described
        assert "Sign in through your MCP client" in described
        assert "Personal Access Token" in described
        assert "/labs/mcp/tokens/" in described
        # The body must stay parseable and correctly framed after the rewrite.
        assert resp.headers["content-type"].startswith("application/json")
        assert int(resp.headers["content-length"]) == len(resp.content)

    assert "no Authorization header" in missing.json()["error_description"]
    assert "unknown, expired or revoked" in rejected.json()["error_description"]


def test_mcp_mount_wrapped_with_closing_connections_middleware(asgi_app):
    """The /mcp mount must be wrapped by the boundary-close ASGI middleware.

    This is the single comprehensive close point for the connection leak
    (#667/#669): it covers PAT auth, every tool handler, the audit write, and
    any future MCP DB entrypoint — not just the two callables
    ``server._closing_connections`` happens to wrap. Structural guard so the
    wrapper can't be silently dropped from config.asgi.
    """
    from config import asgi

    mcp_mount = next(r for r in asgi_app.routes if getattr(r, "path", None) == "/mcp")
    assert isinstance(mcp_mount.app, asgi._ClosingConnectionsApp), (
        "the /mcp mount is no longer wrapped by _ClosingConnectionsApp — " "MCP requests would leak DB connections"
    )


@pytest.mark.django_db(transaction=True)
def test_real_mcp_request_closes_connections_at_boundary(asgi_app, monkeypatch):
    """Drive a real MCP HTTP request and prove the boundary close runs.

    To isolate the middleware as the close point, we NEUTRALIZE the per-function
    ``server._closing_connections`` wrapper (turn it into a passthrough). The
    middleware is then the ONLY thing that can close the request's connections.
    Without the middleware this assertion is red (nothing closes); with it the
    boundary close fires. ``connections.close_all`` is spied with ``wraps`` so it
    still really closes (keeping the DB consistent) while recording the call.
    """
    import anyio
    from django.db import connections as dj_connections
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from connect_labs.mcp import server

    # Make the per-function wrapper a no-op so the middleware is the sole closer.
    monkeypatch.setattr(server, "_closing_connections", lambda fn: fn)

    application = asgi_app

    user = User.objects.create(username="boundary-close")
    _, raw = MCPAccessToken.create_token(user, name="boundary")

    close_spy = mock.Mock(wraps=dj_connections.close_all)
    monkeypatch.setattr(dj_connections, "close_all", close_spy)

    async def _run():
        transport = StreamableHttpTransport(
            url="http://testserver/mcp/",
            headers={"Authorization": f"Bearer {raw}"},
            httpx_client_factory=_client_factory_to_asgi(application),
        )
        async with application.router.lifespan_context(application):
            async with Client(transport) as client:
                await client.call_tool("list_templates", {})

    anyio.run(_run)

    # The middleware closed this request's connections at the boundary — even
    # with the per-function wrapper neutralized.
    assert close_spy.called, "boundary close never ran — MCP request leaked its DB connection"


def _get(application, path):
    import anyio

    async def _run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.get(path)

    return anyio.run(_run)


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource/mcp/",
    ],
)
def test_protected_resource_metadata_is_served_as_json(asgi_app, path):
    """RFC 9728 metadata: the document the 401 challenge points clients at.

    These root paths sit outside every Django prefix; without an explicit route
    they fall through to Django's styled HTML 404, which a client cannot parse
    (``Unrecognized token '<'``, #431).
    """
    from connect_labs.mcp import oauth

    resp = _get(asgi_app, path)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json"), resp.headers.get("content-type")
    assert resp.headers["access-control-allow-origin"] == "*"
    body = resp.json()
    assert body["resource"] == oauth.resource_url()
    assert body["authorization_servers"] == [oauth.public_base_url()]


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
    ],
)
def test_authorization_server_metadata_is_served_as_json(asgi_app, path):
    from connect_labs.mcp import oauth

    resp = _get(asgi_app, path)

    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == oauth.public_base_url()
    assert body["registration_endpoint"].endswith(oauth.REGISTRATION_PATH)
    assert body["code_challenge_methods_supported"] == ["S256"]


@pytest.mark.parametrize("path", ["/.well-known/openid-configuration", "/.well-known/openid-configuration/mcp"])
def test_openid_configuration_is_a_json_404_not_django_html(asgi_app, path):
    resp = _get(asgi_app, path)

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json"), resp.headers.get("content-type")
    assert resp.json()["error"] == "not_found"


def test_metadata_answers_a_cors_preflight(asgi_app):
    """A browser-based client reads the metadata cross-origin before anything else."""
    import anyio

    async def _run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://testserver") as c:
            return await c.options("/.well-known/oauth-protected-resource/mcp")

    resp = anyio.run(_run)

    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "*"


@pytest.mark.django_db(transaction=True)
def test_an_instance_with_no_public_origin_offers_no_sign_in(asgi_app, settings):
    """A dev or staging instance must not advertise production's sign-in.

    Every endpoint in the discovery documents is absolute, so an instance that
    does not know its own origin cannot describe itself — it stays PAT-only,
    which is what labs was before the sign-in existed.
    """
    settings.LABS_PUBLIC_URL = ""

    metadata = _get(asgi_app, "/.well-known/oauth-protected-resource/mcp")
    challenged = _post_without_valid_auth(asgi_app)

    assert metadata.status_code == 404
    assert metadata.json()["error"] == "not_found"
    assert "resource_metadata" not in challenged.headers.get("www-authenticate", "")
    assert "Personal Access Token" in challenged.json()["error_description"]
