"""URL routes for the synthetic-opportunity export API (mounted at /api/export/)."""
from django.urls import path

from . import views

app_name = "export_api"

urlpatterns = [
    path("opportunities/", views.OpportunityListView.as_view(), name="opportunities"),
    path(
        "opportunity/<int:opportunity_id>/",
        views.OpportunityDetailView.as_view(),
        name="detail",
    ),
    path(
        "opportunity/<int:opportunity_id>/user_visits/",
        views.OpportunityDataView.as_view(endpoint="user_visits"),
        name="user_visits",
    ),
    path(
        "opportunity/<int:opportunity_id>/user_data/",
        views.OpportunityDataView.as_view(endpoint="user_data"),
        name="user_data",
    ),
    path(
        "opportunity/<int:opportunity_id>/completed_works/",
        views.OpportunityDataView.as_view(endpoint="completed_works"),
        name="completed_works",
    ),
    path(
        "opportunity/<int:opportunity_id>/completed_module/",
        views.OpportunityDataView.as_view(endpoint="completed_module"),
        name="completed_module",
    ),
    path(
        "opportunity/<int:opportunity_id>/app_structure/",
        views.AppStructureView.as_view(),
        name="app_structure",
    ),
]
