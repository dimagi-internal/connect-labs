"""
URL configuration for coverage app.
"""

from django.urls import path

from . import views

app_name = "coverage"

urlpatterns = [
    path("", views.CoverageMapView.as_view(), name="map"),
    path("api/map-stream/", views.CoverageMapStreamView.as_view(), name="map_stream"),
    path("debug/", views.CoverageDebugView.as_view(), name="debug"),
    path("token-status/", views.CoverageTokenStatusView.as_view(), name="token_status"),
]
