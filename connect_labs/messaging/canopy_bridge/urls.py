"""The workers surface's routes, mounted at /messaging/canopy/ (config/urls.py). Labs' site keys and
site.json are its host site's own (``/labs/canopy/``), which declares this surface."""

from django.urls import path

from connect_labs.messaging.canopy_bridge import mcp, views

app_name = "canopy_bridge"

urlpatterns = [
    # The worker tools (S4), the workers surface's MCP resource: canopy's gateway in subject mode,
    # Authorization: CanopySubject + DPoP only.
    path("mcp/", mcp.endpoint, name="mcp"),
    # Staff: "what the agent saw", one Worker Brief by its hash (the Connect Worker page links here).
    path("brief/<str:brief_hash>/", views.BriefView.as_view(), name="brief"),
]
