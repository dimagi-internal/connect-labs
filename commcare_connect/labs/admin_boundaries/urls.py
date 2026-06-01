"""
URL routing for Admin Boundaries
"""

from django.urls import path

from . import views

app_name = "admin_boundaries"

urlpatterns = [
    # Admin Boundaries Manager
    path("", views.AdminBoundariesView.as_view(), name="index"),
    # Bulk name resolution — used by microplans' paste-the-ward-list bulk-create
    # form to render a confirmable resolution preview before plan creation.
    path("resolve_many/", views.ResolveManyByNameAPIView.as_view(), name="resolve_many"),
    path("load/", views.LoadBoundariesView.as_view(), name="load"),
    path("load/stream/", views.LoadBoundariesStreamView.as_view(), name="load_stream"),
    path("delete/", views.DeleteBoundariesView.as_view(), name="delete"),
    path("stats/", views.BoundaryStatsAPIView.as_view(), name="stats"),
    # GeoPoDe file upload
    path("upload/geopode/", views.UploadGeoPoDEView.as_view(), name="upload_geopode"),
    # GeoJSON API
    path("geojson/<str:iso_code>/ADM<int:admin_level>/", views.BoundaryGeoJSONView.as_view(), name="geojson"),
    # Opportunity boundary coverage API
    path("api/coverage/", views.BoundaryCoverageAPIView.as_view(), name="coverage_api"),
    path("api/countries/", views.AvailableCountriesAPIView.as_view(), name="countries_api"),
    # Boundary map visualization
    path("map/", views.BoundaryMapView.as_view(), name="map"),
    path("api/map/", views.BoundaryMapAPIView.as_view(), name="map_api"),
]
