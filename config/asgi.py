"""
ASGI config for CommCare Connect.

Exposes the ASGI callable as a module-level variable named ``application``.

The connect-labs MCP server (``commcare_connect.mcp``) is a FastMCP 3.x
Streamable-HTTP app served at ``/mcp/`` — the same public URL the old
hand-rolled JSON-RPC view used (see ``commcare_connect.mcp.snippets``). Auth
is enforced INSIDE the MCP app via the per-user PAT verifier
(``CommCarePATVerifier``); there is no hand-rolled gate here.

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

import os
import sys
from pathlib import Path

# Mirror config/wsgi.py: make the interior commcare_connect dir importable.
BASE_DIR = Path(__file__).resolve(strict=True).parent.parent
sys.path.append(str(BASE_DIR / "commcare_connect"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from django.core.asgi import get_asgi_application  # noqa: E402

# Initialize Django (populates the app registry) before importing any module
# that touches ORM models — commcare_connect.mcp.server imports the tools
# package, which imports services/models.
_django_asgi_app = get_asgi_application()

from asgiref.sync import sync_to_async  # noqa: E402
from django.db import connections  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from commcare_connect.mcp.server import build_http_app  # noqa: E402


class _ClosingConnectionsApp:
    """Close this request's Django DB connections at the MCP request boundary.

    The FastMCP app is mounted here as a Starlette sub-app, OUTSIDE Django's
    ``ASGIHandler``. Django recycles DB connections via ``close_old_connections``,
    wired to the ``request_started`` / ``request_finished`` signals — signals
    that ONLY Django's own request handling emits. An MCP request never reaches
    ``ASGIHandler``, so those signals never fire and nothing recycles the
    connections it opened (PAT auth ``users_user`` lookups, every tool handler's
    ORM work, the per-call ``MCPAuditLog`` COMMIT). Under ``CONN_MAX_AGE > 0``
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

    Modeled on the ``_PlainBearerChallenge`` / ``_ReprefixApp`` wrappers below
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


class _PlainBearerChallenge:
    """Rewrite FastMCP's OAuth-style 401 challenge to a plain realm challenge.

    FastMCP 3.x's ``TokenVerifier`` answers a missing/invalid token with
    ``WWW-Authenticate: Bearer error="invalid_token", error_description=...``.
    The RFC 6750 ``error="invalid_token"`` parameter marks the endpoint as an
    OAuth-protected resource, so a spec-compliant MCP client (e.g. Claude Code)
    responds to the 401 by initiating OAuth discovery — fetching
    ``/.well-known/oauth-protected-resource`` — instead of simply re-sending the
    Personal Access Token it already holds. That discovery probe falls through
    to Django's HTML 404 (see the ``_oauth_metadata_absent`` routes below) and
    the client crashes parsing HTML as JSON ("Unrecognized token '<'"), which
    surfaces as a "Failed to reconnect" error.

    connect-labs is a PAT-only resource server (no OAuth authorization server),
    exactly as the pre-FastMCP hand-rolled transport was — it returned a bare
    ``Bearer realm="labs-mcp"`` challenge, which clients satisfy by re-sending
    their bearer. This wrapper restores that behaviour: on a 401 it strips the
    OAuth-style ``WWW-Authenticate`` and replaces it with the plain realm form.
    It is otherwise fully transparent (only ``http`` 401 response headers are
    touched; streaming/SSE 200 responses pass through untouched).
    """

    _PLAIN_CHALLENGE = b'Bearer realm="labs-mcp"'

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start" and message.get("status") == 401:
                headers = [(k, v) for (k, v) in message.get("headers", []) if k.lower() != b"www-authenticate"]
                headers.append((b"www-authenticate", self._PLAIN_CHALLENGE))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)


async def _oauth_metadata_absent(request):
    """Clean JSON 404 for OAuth discovery probes.

    Served for the root ``/.well-known/oauth-*`` paths an MCP client probes
    after a 401. Without these routes the requests fall through to Django's
    catch-all and return a styled HTML 404 that the client cannot parse as the
    expected JSON metadata. A JSON 404 lets discovery fail gracefully; the
    client then falls back to its configured PAT. connect-labs intentionally
    serves no OAuth metadata — authentication is a Personal Access Token sent as
    ``Authorization: Bearer <token>`` (mint at /labs/mcp/tokens/).
    """
    return JSONResponse(
        {
            "error": "not_found",
            "error_description": (
                "connect-labs MCP authenticates with a Personal Access Token "
                "(Authorization: Bearer <token>); it does not serve OAuth metadata."
            ),
        },
        status_code=404,
    )


# RFC 9728 / RFC 8414 discovery paths clients probe at the host root (both the
# bare form and the resource-path-suffixed form for the /mcp/ resource).
_OAUTH_DISCOVERY_PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/openid-configuration",
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
    return Starlette(
        routes=[
            # OAuth discovery probes answered with clean JSON (not Django's HTML
            # 404) so a client that does RFC 9728 discovery after a 401 fails
            # gracefully instead of crashing on an unparseable body. Mounted
            # ahead of the Django catch-all. connect-labs serves no OAuth
            # metadata — it is a PAT-only resource server.
            *[Route(path, _oauth_metadata_absent, methods=["GET", "POST"]) for path in _OAUTH_DISCOVERY_PATHS],
            # Keep the Django token-management browser routes on Django. The
            # _ReprefixApp wrapper re-adds /mcp/admin so Django's URL router sees
            # the full path and can match mcp/admin/create-token/.
            Mount("/mcp/admin", app=_ReprefixApp("/mcp/admin", _django_asgi_app)),
            # FastMCP Streamable-HTTP protocol endpoint at /mcp/. Wrapped so the
            # auth 401 carries a plain `Bearer realm` challenge (not FastMCP's
            # OAuth-style `error="invalid_token"`), keeping PAT clients off the
            # OAuth-discovery path that breaks reconnect. The outer
            # _ClosingConnectionsApp closes this request's DB connections at the
            # mount boundary (MCP bypasses Django's request_finished signal), the
            # primary, comprehensive fix for the connection leak (#667 / #669).
            Mount("/mcp", app=_ClosingConnectionsApp(_PlainBearerChallenge(mcp_app))),
            # Django handles everything else (catch-all, mounted last).
            Mount("/", app=_django_asgi_app),
        ],
        # Run the MCP session-manager lifespan for the whole process.
        lifespan=mcp_app.lifespan,
    )


application = build_application()
