from django.conf import settings
from django.urls import path

from connect_labs.supply_chain import api_views, network_views, reference_views, views
from connect_labs.supply_chain.procurement import views as procurement_views

app_name = "supply_chain"

urlpatterns = [
    # the domain shell — sub-components and their state
    path("", views.DomainHomeView.as_view(), name="home"),
    # the master item list is domain-level reference data, not procurement's:
    # tracking and distribution will both read it
    path("catalogue/", views.CatalogueView.as_view(), name="catalogue"),
    # Write screens for the catalogue. Both literals sit above the slug route
    # for the reason the comment below gives: "new" and "items" are valid
    # slugs and would otherwise be looked up as products.
    path("catalogue/new/", reference_views.ProductCreateView.as_view(), name="product_create"),
    path("catalogue/items/new/", reference_views.ItemCreateView.as_view(), name="item_create"),
    path("catalogue/items/<int:item_id>/edit/", reference_views.ItemUpdateView.as_view(), name="item_edit"),
    # Before the slug route: "items" is itself a valid slug, so the literal
    # would otherwise lose to the converter and every trade item would 404
    # looking for a product called "items".
    path("catalogue/items/<int:item_id>/", views.ItemDetailView.as_view(), name="item_detail"),
    path("catalogue/<slug:slug>/", views.ProductDetailView.as_view(), name="product_detail"),
    path("catalogue/<slug:slug>/edit/", reference_views.ProductUpdateView.as_view(), name="product_edit"),
    # Suppliers are reference data reused across rounds, so they sit at the
    # domain level rather than under procurement -- orders and receipts name
    # them too.
    path("suppliers/", views.SupplierDirectoryView.as_view(), name="suppliers"),
    path("suppliers/new/", reference_views.SupplierCreateView.as_view(), name="supplier_create"),
    path("suppliers/<int:supplier_id>/", views.SupplierDetailView.as_view(), name="supplier_detail"),
    path("suppliers/<int:supplier_id>/edit/", reference_views.SupplierUpdateView.as_view(), name="supplier_edit"),
    # Organisations are labs-wide rather than programme-scoped, and they sit
    # under Suppliers in the nav because you visit them to bind a partner or
    # fold a duplicate away, not daily. "merge" and "new" precede the int
    # route so neither can be read as an id.
    path("organisations/", network_views.OrganisationDirectoryView.as_view(), name="organisations"),
    path("organisations/new/", network_views.OrgCreateView.as_view(), name="org_create"),
    path("organisations/merge/", network_views.OrgMergeView.as_view(), name="org_merge"),
    path("organisations/<int:org_id>/edit/", network_views.OrgUpdateView.as_view(), name="org_edit"),
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
    # write screens: the sourcing lifecycle a person could not previously
    # complete in a browser — create a round, open it, invite, record a reply,
    # correct a mistake.
    path(
        "procurement/rounds/new/",
        procurement_views.RoundCreateView.as_view(),
        name="procurement_round_create",
    ),
    path(
        "procurement/rounds/<int:round_id>/edit/",
        procurement_views.RoundUpdateView.as_view(),
        name="procurement_round_edit",
    ),
    path(
        "procurement/rounds/<int:round_id>/open/",
        procurement_views.RoundOpenView.as_view(),
        name="procurement_round_open",
    ),
    path(
        "procurement/rounds/<int:round_id>/close/",
        procurement_views.RoundCloseView.as_view(),
        name="procurement_round_close",
    ),
    path(
        "procurement/rounds/<int:round_id>/invite/",
        procurement_views.OutreachLogView.as_view(),
        name="procurement_outreach_log",
    ),
    path(
        "procurement/outreach/<int:outreach_id>/reply/",
        procurement_views.OutreachReplyView.as_view(),
        name="procurement_outreach_reply",
    ),
    path(
        "procurement/outreach/<int:outreach_id>/delete/",
        procurement_views.OutreachDeleteView.as_view(),
        name="procurement_outreach_delete",
    ),
    path(
        "procurement/quotes/<int:quote_id>/void/",
        procurement_views.QuoteVoidView.as_view(),
        name="procurement_quote_void",
    ),
    path(
        "procurement/quotes/new/",
        procurement_views.QuoteEntryView.as_view(),
        name="procurement_quote_entry",
    ),
    # After "quotes/new/" so the literal never loses to the int converter.
    path(
        "procurement/quotes/<int:quote_id>/",
        procurement_views.QuoteDetailView.as_view(),
        name="procurement_quote_detail",
    ),
    path("orders/", views.OrdersView.as_view(), name="orders"),
    path("orders/<int:contract_id>/", views.OrderDetailView.as_view(), name="order_detail"),
    # Before Stock, the way the work runs: stock has to have somewhere to rest
    # before there is any to look at.
    path("network/", network_views.NetworkView.as_view(), name="network"),
    path("network/new/", network_views.SupplyPointCreateView.as_view(), name="supply_point_create"),
    path(
        "network/<int:supply_point_id>/edit/",
        network_views.SupplyPointUpdateView.as_view(),
        name="supply_point_edit",
    ),
    path("stock/", views.StockView.as_view(), name="stock"),
    path("distribution/", views.DistributionView.as_view(), name="distribution"),
]

if settings.DEBUG:
    # Local iteration only: the labs OAuth middleware cannot be satisfied on a
    # laptop. Raises Http404 on its own if DEBUG is ever off. See dev_views.
    from connect_labs.supply_chain import dev_views

    urlpatterns += [path("dev-login/", dev_views.dev_login, name="dev_login")]
