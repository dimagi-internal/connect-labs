from django.urls import path

from .admin_views import create_token_browser

app_name = "mcp"

# NOTE: the MCP protocol endpoint (formerly views.mcp_endpoint at "") is no
# longer a Django view. It is the FastMCP 3.x Streamable-HTTP ASGI app mounted
# at /mcp/ in config/asgi.py — same public URL, async transport. Only the
# token-creation browser route remains on Django (mounted under /mcp/admin/ in
# config/asgi.py so it doesn't collide with the FastMCP mount).
urlpatterns = [
    path("admin/create-token/", create_token_browser, name="admin_create_token"),
]
