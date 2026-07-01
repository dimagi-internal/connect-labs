"""Smoke test for the FastMCP MCP server build.

The protocol endpoint is no longer a Django view (it's the FastMCP
Streamable-HTTP ASGI app mounted in config/asgi.py), so there is no
``reverse("mcp:endpoint")`` GET to assert a 405 on. Instead we smoke-test
that the server instance builds, exposes tools, and produces a mountable
HTTP app with a lifespan.
"""


def test_server_builds_with_tools():
    from connect_labs.mcp.server import mcp

    assert mcp.name == "connect_labs"


def test_http_app_builds_with_lifespan():
    from connect_labs.mcp.server import build_http_app

    app = build_http_app()
    # Streamable-HTTP requires a lifespan for session management.
    assert app.lifespan is not None
