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

from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from commcare_connect.mcp.server import build_http_app  # noqa: E402

# Streamable-HTTP ASGI app. path="/" -> the MCP endpoint is the mount root,
# i.e. /mcp/ (the preserved public URL).
_mcp_app = build_http_app()


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


application = Starlette(
    routes=[
        # Keep the Django token-management browser routes on Django. The
        # _ReprefixApp wrapper re-adds /mcp/admin so Django's URL router sees
        # the full path and can match mcp/admin/create-token/.
        Mount("/mcp/admin", app=_ReprefixApp("/mcp/admin", _django_asgi_app)),
        # FastMCP Streamable-HTTP protocol endpoint at /mcp/.
        Mount("/mcp", app=_mcp_app),
        # Django handles everything else (catch-all, mounted last).
        Mount("/", app=_django_asgi_app),
    ],
    # Run the MCP session-manager lifespan for the whole process.
    lifespan=_mcp_app.lifespan,
)
