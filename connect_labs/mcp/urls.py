from django.urls import path

from .admin_views import create_token_browser
from .agent_request_views import agent_request_claim, agent_request_create

app_name = "mcp"

# NOTE: the MCP protocol endpoint (formerly views.mcp_endpoint at "") is no
# longer a Django view. It is the FastMCP 3.x Streamable-HTTP ASGI app mounted
# at /mcp/ in config/asgi.py — same public URL, async transport. Only the
# token-creation browser route remains on Django (mounted under /mcp/admin/ in
# config/asgi.py so it doesn't collide with the FastMCP mount).
urlpatterns = [
    path("admin/create-token/", create_token_browser, name="admin_create_token"),
    # An agent asking a person for a temporary no-user-visit token, signed with the agent's own
    # token (mcp/agent_requests.py). The person's page is labs:mcp_agent_request.
    path("admin/agent-requests/", agent_request_create, name="agent_request_create"),
    path("admin/agent-requests/<str:ref>/claim/", agent_request_claim, name="agent_request_claim"),
]
