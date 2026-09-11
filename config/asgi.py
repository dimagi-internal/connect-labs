"""
ASGI config for Connect.

Exposes the ASGI callable as a module-level variable named ``application``.

The connect-labs MCP server (``connect_labs.mcp``) is a FastMCP 3.x
Streamable-HTTP app served at ``/mcp/`` — the same public URL the old
hand-rolled JSON-RPC view used (see ``connect_labs.mcp.snippets``). Auth
is enforced INSIDE the MCP app by ``CommCarePATVerifier``, which accepts a
per-user Personal Access Token or an access token from the standard MCP
sign-in (``connect_labs.mcp.oauth``); there is no hand-rolled gate here. This
module serves that sign-in's discovery documents at the host root and shapes
the 401 so a client knows where to sign in.

Streamable-HTTP requires the MCP app's lifespan to run for session
management, but Django's bare ASGI app has no lifespan. So we build a
combined app: a Starlette router that mounts the MCP app under ``/mcp`` and
the Django ASGI app at ``/`` (catch-all), with the MCP app's lifespan wired
into the Starlette app. Starlette owns the ASGI ``lifespan`` events; the
Django sub-mount only ever sees ``http``/``websocket`` scopes.

The MCP token-management browser routes (``/mcp/admin/create-token/``) stay
on Django; they're mounted ahead of the MCP app so the protocol endpoint at
``/mcp/`` and the admin route don't collide.

The deploy entrypoint (``docker/start``) runs this module under gunicorn's
``UvicornWorker`` so the same process model serves WSGI-style concurrency on
an ASGI app.
"""

import json
import os
import sys
from pathlib import Path

# Mirror config/wsgi.py: make the interior connect_labs dir importable.
BASE_DIR = Path(__file__).resolve(strict=True).parent.parent
sys.path.append(str(BASE_DIR / "connect_labs"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from django.core.asgi import get_asgi_application  # noqa: E402

# Initialize Django (populates the app registry) before importing any module
# that touches ORM models — connect_labs.mcp.server imports the tools
# package, which imports services/models.
_django_asgi_app = get_asgi_application()

from asgiref.sync import sync_to_async  # noqa: E402
from django.db import connections  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from connect_labs.mcp import oauth  # noqa: E402
from connect_labs.mcp.server import build_http_app  # noqa: E402


class _ClosingConnectionsApp:
    """Close this request's Django DB connections at the MCP request boundary.

    The FastMCP app is mounted here as a Starlette sub-app, OUTSIDE Django's
    ``ASGIHandler``. Django recycles DB connections via ``close_old_connections``,
    wired to the ``request_started`` / ``request_finished`` signals — signals
    that ONLY Django's own request handling emits. An MCP request never reaches
    ``ASGIHandler``, so those signals never fire and nothing recycles the
    connections it opened (token lookups in auth, every tool handler's ORM
    work, the per-call ``MCPAuditLog`` COMMIT). Under ``CONN_MAX_AGE > 0``
    those connections are kept open for reuse but, absent the request-finished
    signal, never closed — they sit ``idle`` on RDS and accumulate until the
    instance exhausts its connection slots (issues #667 / #669).

    This wraps the ENTIRE ``/mcp`` mount, so it is a single, comprehensive close
    point covering auth + tools + audit-log + ANY future MCP DB entrypoint,
    rather than the per-function whack-a-mole of ``server._closing_connections``
    (which only wraps two named callables and is now defense-in-depth). The
    close runs in the SAME ``thread_sensitive`` asgiref executor the MCP handlers
    ran in, so it targets exactly the thread-local connections those handlers
    opened. ``close_all()`` (not ``close_old_connections()``) is deliberate: the
    asgiref thread pool churns, so unconditionally closing guarantees nothing is
    left open on a thread that never serves another MCP call.

    Modeled on the ``_BearerChallenge`` / ``_ReprefixApp`` wrappers below
    (same scope/receive/send shape). Only ``http`` scopes get a boundary close;
    ``websocket`` / ``lifespan`` scopes pass through untouched (the lifespan in
    particular must not have its connections yanked).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            # Close in the thread-sensitive executor the MCP handlers used, so
            # we close THEIR thread-local connections (not the event loop's).
            await sync_to_async(connections.close_all, thread_sensitive=True)()


class _BearerChallenge:
    """Answer an unauthenticated MCP request with the MCP spec's 401.

    The MCP authorization spec has a client that gets a 401 read
    ``resource_metadata`` from ``WWW-Authenticate``, fetch that document, and
    sign the user in through the authorization server it names. So the
    challenge names it, and a request that carried no token gets no error code
    at all (RFC 6750 section 3.1) — only one that carried a rejected token is
    told ``error="invalid_token"``.

    History, because the next reader will find #431: FastMCP's default
    challenge (``error="invalid_token"`` and no ``resource_metadata``) sent
    clients to OAuth discovery paths that Django answered with an HTML 404, and
    reconnect broke on the unparseable body. With no OAuth server behind the
    MCP endpoint then, the fix was a plain ``Bearer realm`` challenge that kept
    clients off discovery. Labs now serves real discovery documents (below), so
    discovery is the path that works, and this wrapper points clients at it.

    The body is rewritten too, for the reason #431 found: FastMCP's prose
    cannot tell a request with NO Authorization header from one with a rejected
    token, and a client whose PAT header helper fails silently sends none. The
    body says which of the two this was and names both ways in. Only ``http``
    401 responses are touched; streaming/SSE 200 responses pass through.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _had_token(scope) -> bool:
        return any(key.lower() == b"authorization" and value.strip() for key, value in scope.get("headers", []))

    def _challenge(self, had_token: bool) -> bytes:
        parts = ['Bearer realm="labs-mcp"']
        if had_token:
            parts.append('error="invalid_token"')
        # Only point at metadata this instance actually serves: an instance with
        # no public origin offers no sign-in, and naming another one's would send
        # the user somewhere that cannot issue a token for this server.
        if oauth.sign_in_configured():
            parts.append(f'resource_metadata="{oauth.protected_resource_metadata_url()}"')
        return ", ".join(parts).encode()

    def _body(self, had_token: bool) -> bytes:
        tokens_url = (
            f"{oauth.public_base_url()}/labs/mcp/tokens/" if oauth.sign_in_configured() else "/labs/mcp/tokens/"
        )
        if oauth.sign_in_configured():
            ways_in = (
                "Sign in through your MCP client (it reads resource_metadata from the WWW-Authenticate header "
                "and opens a browser), or send a Personal Access Token as 'Authorization: Bearer <token>' "
                f"-- mint or rotate one at {tokens_url}."
            )
        else:
            ways_in = (
                "This instance authenticates with a Personal Access Token sent as "
                f"'Authorization: Bearer <token>' -- mint or rotate one at {tokens_url}."
            )
        if had_token:
            body = {
                "error": "invalid_token",
                "error_description": (
                    "The bearer token on this request is not one this server accepts: it is unknown, "
                    "expired or revoked. " + ways_in
                ),
            }
        else:
            body = {
                "error": "unauthorized",
                "error_description": (
                    "This request carried no Authorization header. "
                    + ways_in
                    + " A client set up with a Personal Access Token whose header helper fails silently "
                    "sends no header at all, which looks exactly like this."
                ),
            }
        return json.dumps(body).encode()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        had_token = self._had_token(scope)
        challenge = self._challenge(had_token)
        error_body = self._body(had_token)

        # Rewriting the body means the upstream Content-Length is wrong, so the
        # start message is held until the body is in hand.
        state = {"is_401": False, "start": None}

        async def _send(message):
            if message["type"] == "http.response.start":
                if message.get("status") == 401:
                    headers = [
                        (k, v)
                        for (k, v) in message.get("headers", [])
                        if k.lower() not in (b"www-authenticate", b"content-length", b"content-type")
                    ]
                    headers.append((b"www-authenticate", challenge))
                    headers.append((b"content-type", b"application/json"))
                    headers.append((b"content-length", str(len(error_body)).encode()))
                    state["is_401"] = True
                    state["start"] = {**message, "headers": headers}
                    return
                await send(message)
                return

            if message["type"] == "http.response.body" and state["is_401"]:
                # Swallow the upstream body entirely; emit ours once, at the end.
                if message.get("more_body"):
                    return
                await send(state["start"])
                await send({"type": "http.response.body", "body": error_body, "more_body": False})
                state["is_401"] = False
                return

            await send(message)

        await self.app(scope, receive, _send)


# ---------------------------------------------------------------------------
# The MCP sign-in's discovery documents, at the host root where clients look.
# Served here rather than by Django because they sit outside every Django
# prefix and must never fall through to Django's HTML 404 (#431).
#
# The CORS headers make the documents readable from a browser-based client, but
# they are not sufficient for one: /mcp/ itself, /o/register/ and /o/token/ send
# no CORS headers (CORS_URLS_REGEX covers /api/ only), so a flow driven from a
# web page still cannot complete. Native and server-side clients are unaffected.
# ---------------------------------------------------------------------------

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


def _metadata_endpoint(build_body, status_code: int = 200, needs_sign_in: bool = True):
    async def endpoint(request):
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_CORS_HEADERS)
        if needs_sign_in and not oauth.sign_in_configured():
            return JSONResponse(
                {
                    "error": "not_found",
                    "error_description": (
                        "This Connect Labs instance does not offer the MCP sign-in (no public origin is "
                        "configured). Authenticate with a Personal Access Token instead."
                    ),
                },
                status_code=404,
                headers=_CORS_HEADERS,
            )
        return JSONResponse(build_body(), status_code=status_code, headers=_CORS_HEADERS)

    return endpoint


def _openid_configuration_absent() -> dict:
    return {
        "error": "not_found",
        "error_description": (
            "Connect Labs is an OAuth 2 authorization server, not an OpenID provider: "
            "read /.well-known/oauth-authorization-server."
        ),
    }


# RFC 9728: the bare form and the form suffixed with the resource path /mcp/.
_PROTECTED_RESOURCE_METADATA_PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-protected-resource/mcp/",
]
# RFC 8414 for the issuer (the host root). The /mcp-suffixed forms are for older
# clients that derive the authorization server from the MCP URL instead of
# reading the protected-resource metadata; answering them costs nothing.
_AUTHORIZATION_SERVER_METADATA_PATHS = [
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/oauth-authorization-server/mcp/",
]
_OPENID_CONFIGURATION_PATHS = [
    "/.well-known/openid-configuration",
    "/.well-known/openid-configuration/mcp",
]


class _ReprefixApp:
    """Re-prepend the stripped Starlette Mount prefix before forwarding to app.

    Starlette's Mount strips its prefix from scope["path"] before calling the
    child app, so Mount("/mcp/admin", django) gives Django path "/create-token/"
    instead of "/mcp/admin/create-token/".  This wrapper re-adds the prefix so
    Django's URL router sees the full path and can match mcp/admin/create-token/.
    """

    def __init__(self, prefix: str, app):
        self.prefix = prefix
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope = dict(scope)
            scope["path"] = self.prefix + scope["path"]
        await self.app(scope, receive, send)


def build_application() -> Starlette:
    """Assemble the combined MCP + Django ASGI app.

    A factory, not a bare module-level expression, because the FastMCP
    Streamable-HTTP app owns a ``StreamableHTTPSessionManager`` whose ``.run()``
    lifespan can only be entered ONCE per instance. Production enters it exactly
    once (process startup), so the module-level ``application`` below is correct.
    But tests that drive the app in-process need to enter the lifespan per test;
    sharing one instance makes the second test crash with "session manager
    .run() can only be called once". The factory hands each caller a fresh app
    (fresh session manager), so tests stay isolated while production is
    unchanged. See ``test_asgi_integration.py``.
    """
    # Streamable-HTTP ASGI app. path="/" -> the MCP endpoint is the mount root,
    # i.e. /mcp/ (the preserved public URL).
    mcp_app = build_http_app()
    methods = ["GET", "OPTIONS"]
    return Starlette(
        routes=[
            # The MCP sign-in's discovery documents (see connect_labs.mcp.oauth),
            # mounted ahead of the Django catch-all.
            *[
                Route(path, _metadata_endpoint(oauth.protected_resource_metadata), methods=methods)
                for path in _PROTECTED_RESOURCE_METADATA_PATHS
            ],
            *[
                Route(path, _metadata_endpoint(oauth.authorization_server_metadata), methods=methods)
                for path in _AUTHORIZATION_SERVER_METADATA_PATHS
            ],
            *[
                Route(
                    path,
                    _metadata_endpoint(_openid_configuration_absent, status_code=404, needs_sign_in=False),
                    methods=methods,
                )
                for path in _OPENID_CONFIGURATION_PATHS
            ],
            # Keep the Django token-management browser routes on Django. The
            # _ReprefixApp wrapper re-adds /mcp/admin so Django's URL router sees
            # the full path and can match mcp/admin/create-token/.
            Mount("/mcp/admin", app=_ReprefixApp("/mcp/admin", _django_asgi_app)),
            # FastMCP Streamable-HTTP protocol endpoint at /mcp/. Wrapped so the
            # auth 401 is the MCP spec's challenge, naming where to sign in. The
            # outer _ClosingConnectionsApp closes this request's DB connections at
            # the mount boundary (MCP bypasses Django's request_finished signal),
            # the primary, comprehensive fix for the connection leak (#667 / #669).
            Mount("/mcp", app=_ClosingConnectionsApp(_BearerChallenge(mcp_app))),
            # Django handles everything else (catch-all, mounted last).
            Mount("/", app=_django_asgi_app),
        ],
        # Run the MCP session-manager lifespan for the whole process.
        lifespan=mcp_app.lifespan,
    )


application = build_application()
