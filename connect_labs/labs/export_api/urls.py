"""URL routes for the synthetic-opportunity export API (mounted at /api/export/)."""
from django.urls import path

from . import views

app_name = "export_api"

urlpatterns = [
    path("opp_org_program_list/", views.OppOrgProgramListView.as_view(), name="opp_org_program_list"),
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
        "opportunity/<int:opportunity_id>/payment/",
        views.OpportunityDataView.as_view(endpoint="payment"),
        name="payment",
    ),
    path(
        "opportunity/<int:opportunity_id>/invoice/",
        views.OpportunityDataView.as_view(endpoint="invoice"),
        name="invoice",
    ),
    path(
        "opportunity/<int:opportunity_id>/assessment/",
        views.OpportunityDataView.as_view(endpoint="assessment"),
        name="assessment",
    ),
    path(
        "opportunity/<int:opportunity_id>/app_structure/",
        views.AppStructureView.as_view(),
        name="app_structure",
    ),
]
