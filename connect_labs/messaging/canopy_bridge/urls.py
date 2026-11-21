"""The ``connect-messaging`` site's routes, mounted at /messaging/canopy/ (config/urls.py)."""

from django.urls import path

from connect_labs.messaging.canopy_bridge import site_views

app_name = "canopy_bridge"

urlpatterns = [
    # Public, like any host's keys: what canopy verifies this site's signatures with.
    path("jwks.json", site_views.jwks, name="jwks"),
    # Public: the site's signed facts (origins, keys, MCP resource, person_scheme), beside the JWKS.
    path("site.json", site_views.site_document, name="site"),
]
