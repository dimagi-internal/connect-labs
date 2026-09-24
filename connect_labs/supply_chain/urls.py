from django.conf import settings
from django.urls import path

from connect_labs.supply_chain import (
    api_views,
    distribution_views,
    fulfilment_views,
    network_views,
    reference_views,
    stock_views,
    views,
)
from connect_labs.supply_chain.alerts import views as alert_views
from connect_labs.supply_chain.procurement import views as procurement_views
from connect_labs.supply_chain.update_links import views as update_link_views

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
        "procurement/quotes/<int:quote_id>/correct/",
        distribution_views.QuoteCorrectView.as_view(),
        name="procurement_quote_correct",
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
    # Every check with its facts. The overview only counts them.
    path("checks/", views.ChecksView.as_view(), name="checks"),
    path(
        "procurement/awards/<int:award_id>/",
        procurement_views.AwardDetailView.as_view(),
        name="award_detail",
    ),
    path(
        "procurement/awards/<int:award_id>/approvals/new/",
        procurement_views.ApprovalRequestView.as_view(),
        name="approval_request",
    ),
    path(
        "procurement/approvals/<int:approval_id>/documents/new/",
        procurement_views.ApprovalDocumentAttachView.as_view(),
        name="approval_document_attach",
    ),
    path(
        "procurement/quotes/<int:quote_id>/documents/new/",
        procurement_views.QuoteDocumentAttachView.as_view(),
        name="quote_document_attach",
    ),
    path(
        "procurement/approvals/<int:approval_id>/decide/",
        procurement_views.ApprovalDecideView.as_view(),
        name="approval_decide",
    ),
    path("orders/", views.OrdersView.as_view(), name="orders"),
    # "new" before the int route, so the literal cannot be read as an id.
    path("orders/new/", fulfilment_views.ContractCreateView.as_view(), name="contract_create"),
    path("orders/<int:contract_id>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("orders/<int:contract_id>/edit/", fulfilment_views.ContractUpdateView.as_view(), name="contract_edit"),
    # Everything below hangs off the order it belongs to: an invoice with no
    # contract is an invoice against nothing, and the URL is where that is said.
    path(
        "orders/<int:contract_id>/invoices/new/",
        fulfilment_views.InvoiceRecordView.as_view(),
        name="invoice_record",
    ),
    path(
        "orders/<int:contract_id>/documents/new/",
        fulfilment_views.DocumentAttachView.as_view(),
        name="document_attach",
    ),
    path(
        "orders/<int:contract_id>/shipments/new/",
        stock_views.ShipmentRecordView.as_view(),
        name="shipment_record",
    ),
    path(
        "orders/<int:contract_id>/receipts/new/",
        stock_views.ReceiptRecordView.as_view(),
        name="receipt_record",
    ),
    path("documents/<int:document_id>/", views.document_open, name="document_open"),
    path("shipments/<int:shipment_id>/", views.ShipmentDetailView.as_view(), name="shipment_detail"),
    path("shipments/<int:shipment_id>/status/", stock_views.ShipmentStatusView.as_view(), name="shipment_status"),
    path(
        "shipments/<int:shipment_id>/documents/require/",
        stock_views.ShipmentRequireDocumentView.as_view(),
        name="shipment_require_document",
    ),
    path(
        "shipments/<int:shipment_id>/documents/unrequire/",
        stock_views.ShipmentRequirementRemoveView.as_view(),
        name="shipment_unrequire_document",
    ),
    path(
        "shipments/<int:shipment_id>/documents/new/",
        stock_views.ShipmentDocumentAttachView.as_view(),
        name="shipment_document_attach",
    ),
    path("shipments/<int:shipment_id>/charges/new/", stock_views.ChargeRecordView.as_view(), name="charge_record"),
    path("invoices/<int:invoice_id>/edit/", fulfilment_views.InvoiceUpdateView.as_view(), name="invoice_edit"),
    path(
        "invoices/<int:invoice_id>/payments/new/",
        fulfilment_views.PaymentRecordView.as_view(),
        name="payment_record",
    ),
    path(
        "payments/<int:payment_id>/confirm/",
        fulfilment_views.PaymentConfirmView.as_view(),
        name="payment_confirm",
    ),
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
    # No edit screen for a movement, deliberately: the ledger is append-only,
    # and a correction is another movement naming its cause.
    path("stock/movements/", views.MovementsView.as_view(), name="movements"),
    path("stock/movements/new/", stock_views.MovementRecordView.as_view(), name="movement_record"),
    path("stock/counts/new/", stock_views.StockCountRecordView.as_view(), name="stock_count_record"),
    path("distribution/", views.DistributionView.as_view(), name="distribution"),
    path("distribution/new/", distribution_views.DistributionRecordView.as_view(), name="distribution_record"),
    # Alerts: who is told about what, and the log of what they were told.
    path("alerts/", alert_views.AlertListView.as_view(), name="alerts"),
    path("alerts/new/", alert_views.AlertCreateView.as_view(), name="alert_create"),
    path("alerts/check-now/", alert_views.AlertCheckNowView.as_view(), name="alert_check_now"),
    path("alerts/<int:subscription_id>/edit/", alert_views.AlertUpdateView.as_view(), name="alert_edit"),
    path("alerts/<int:subscription_id>/delete/", alert_views.AlertDeleteView.as_view(), name="alert_delete"),
    # Supplier update links: the programme's screens for issuing them...
    path("links/", update_link_views.UpdateLinkListView.as_view(), name="update_links"),
    path("links/new/", update_link_views.UpdateLinkIssueView.as_view(), name="update_link_issue"),
    path(
        "links/<int:link_id>/revoke/",
        update_link_views.UpdateLinkRevokeView.as_view(),
        name="update_link_revoke",
    ),
    # ...and the page behind one. Unauthenticated and token-gated: the only
    # route in the domain without login_required, and skip-listed in
    # labs/oauth_session.py for the same reason Pulse's public displays are.
    path("u/<str:token>/", update_link_views.UpdateLinkPublicView.as_view(), name="update_link_public"),
]

if settings.DEBUG:
    # Local iteration only: the labs OAuth middleware cannot be satisfied on a
    # laptop. Raises Http404 on its own if DEBUG is ever off. See dev_views.
    from connect_labs.supply_chain import dev_views

    urlpatterns += [path("dev-login/", dev_views.dev_login, name="dev_login")]
