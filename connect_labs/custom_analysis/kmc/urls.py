"""URL configuration for KMC timeline views."""

from django.urls import path

from connect_labs.custom_analysis.kmc import views

app_name = "kmc"

urlpatterns = [
    path("children/", views.KMCTimelineListView.as_view(), name="child_list"),
    path("children/stream/", views.KMCChildListStreamView.as_view(), name="child_list_stream"),
    path("children/<str:child_id>/", views.KMCTimelineDetailView.as_view(), name="child_timeline"),
    path("api/child/<str:child_id>/stream/", views.KMCTimelineDataStreamView.as_view(), name="api_child_data_stream"),
    path("image/<str:blob_id>/", views.KMCImageProxyView.as_view(), name="image_proxy"),
]
