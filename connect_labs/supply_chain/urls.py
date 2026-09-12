from django.conf import settings
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
    path("orders/", views.OrdersView.as_view(), name="orders"),
    path("orders/<int:contract_id>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("stock/", views.StockView.as_view(), name="stock"),
    path("distribution/", views.DistributionView.as_view(), name="distribution"),
]

if settings.DEBUG:
    # Local iteration only: the labs OAuth middleware cannot be satisfied on a
    # laptop. Raises Http404 on its own if DEBUG is ever off. See dev_views.
    from connect_labs.supply_chain import dev_views

    urlpatterns += [path("dev-login/", dev_views.dev_login, name="dev_login")]
