"""The ``connect-messaging`` site's routes, mounted at /messaging/canopy/ (config/urls.py)."""

from django.urls import path

from connect_labs.messaging.canopy_bridge import mcp, site_views, views

app_name = "canopy_bridge"

urlpatterns = [
    # Public, like any host's keys: what canopy verifies this site's signatures with.
    path("jwks.json", site_views.jwks, name="jwks"),
    # Public: the site's signed facts (origins, keys, MCP resource, person_scheme), beside the JWKS.
    path("site.json", site_views.site_document, name="site"),
    # The worker tools (S4): canopy's gateway in subject mode, Authorization: CanopySubject + DPoP only.
    path("mcp/", mcp.endpoint, name="mcp"),
    # Staff: "what the agent saw", one Worker Brief by its hash (the Connect Worker page links here).
    path("brief/<str:brief_hash>/", views.BriefView.as_view(), name="brief"),
]
