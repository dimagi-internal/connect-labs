from django.urls import path

from connect_labs.pulse import api, network_api, views

app_name = "pulse"

urlpatterns = [
    # Read API — the only surface cards talk to.
    path("api/summary/", api.SummaryView.as_view(), name="api_summary"),
    path("api/events/", api.EventsView.as_view(), name="api_events"),
    path("api/replay/", api.ReplayView.as_view(), name="api_replay"),
    path("api/grid/", api.GridView.as_view(), name="api_grid"),
    path("api/network/", network_api.NetworkView.as_view(), name="api_network"),
    # The header widget any labs page can opt into. Aggregates only, no names.
    path("api/widget/", api.WidgetView.as_view(), name="api_widget"),
    # Drill-down, fetched on click rather than polled with the summary.
    path("api/partner/", api.PartnerView.as_view(), name="api_partner"),
    path("api/worker/", api.WorkerView.as_view(), name="api_worker"),
    path("api/opp/", api.OpportunityView.as_view(), name="api_opp"),
    # Authenticated views.
    path("", views.PulseIndexView.as_view(), name="index"),
    path("network/", views.PulseNetworkView.as_view(), name="network"),
    path("costs/", views.PulseCostsView.as_view(), name="costs"),
    path("v/<slug:layout>/", views.PulseDisplayView.as_view(), name="display"),
    path("opp/<int:opp_id>/", views.PulseOppView.as_view(), name="opp"),
    # An engagement several Connect opportunities belong to, addressed by slug.
    # After the int route, so an opportunity id can never be read as a slug.
    path("opp/<slug:slug>/", views.PulseOppGroupView.as_view(), name="opp_group"),
    # Donor reports: authenticated authoring, public rendering.
    path("reports/", views.PulseReportListView.as_view(), name="report_list"),
    path("reports/<str:slug>/edit/", views.PulseReportEditView.as_view(), name="report_edit"),
    path("r/<str:slug>/", views.PulseReportView.as_view(), name="report"),
    # Public, unauthenticated, token-scoped. Revocable per link.
    path("p/<str:token>/", views.PulsePublicView.as_view(), name="public"),
]
