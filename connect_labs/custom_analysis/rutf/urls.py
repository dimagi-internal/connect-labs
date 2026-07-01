"""URL configuration for RUTF timeline views."""

from django.urls import path

from connect_labs.custom_analysis.rutf import views

app_name = "rutf"

urlpatterns = [
    path("children/", views.RUTFTimelineListView.as_view(), name="child_list"),
    path("children/stream/", views.RUTFChildListStreamView.as_view(), name="child_list_stream"),
    path("children/<str:child_id>/", views.RUTFTimelineDetailView.as_view(), name="child_timeline"),
    path("api/child/<str:child_id>/stream/", views.RUTFTimelineDataStreamView.as_view(), name="api_child_data_stream"),
    path("image/<str:blob_id>/", views.RUTFImageProxyView.as_view(), name="image_proxy"),
]
