from django.urls import path

from connect_labs.supply_chain import api_views, views
from connect_labs.supply_chain.procurement import views as procurement_views

app_name = "supply_chain"

urlpatterns = [
    # the domain shell — sub-components and their state
    path("", views.DomainHomeView.as_view(), name="home"),
    # the master item list is domain-level reference data, not procurement's:
    # tracking and distribution will both read it
    path("items/", views.ItemMasterView.as_view(), name="items"),
    # shared API: one endpoint for the whole domain, because the registry is shared
    path("api/operations/", api_views.OperationListView.as_view(), name="api_operations"),
    path("api/<str:name>/", api_views.OperationDispatchView.as_view(), name="api_operation"),
    # sub-component one: procurement
    path(
        "procurement/",
        procurement_views.RoundBoardView.as_view(),
        name="procurement_round_board",
    ),
    path(
        "procurement/rounds/<int:round_id>/",
        procurement_views.RoundDetailView.as_view(),
        name="procurement_round_detail",
    ),
    path(
        "procurement/rounds/<int:round_id>/compare/",
        procurement_views.ComparisonView.as_view(),
        name="procurement_comparison",
    ),
    path(
        "procurement/quotes/new/",
        procurement_views.QuoteEntryView.as_view(),
        name="procurement_quote_entry",
    ),
    path(
        "procurement/registries/",
        procurement_views.RegistriesView.as_view(),
        name="procurement_registries",
    ),
    path(
        "procurement/quotes/followup/",
        procurement_views.FollowupDraftView.as_view(),
        name="procurement_followup_draft",
    ),
]
