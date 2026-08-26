"""URLs for the targeting surface, mounted at /labs/targeting/."""

from django.urls import path

from connect_labs.labs.indicators import views

app_name = "indicators"

urlpatterns = [
    path("", views.TargetingView.as_view(), name="index"),
    path("api/map/", views.MapDataView.as_view(), name="map_data"),
    path("api/selection/", views.SelectionView.as_view(), name="selection"),
    path("api/coverage/", views.CoverageView.as_view(), name="coverage"),
    path("download/", views.SelectionDownloadView.as_view(), name="download"),
]
