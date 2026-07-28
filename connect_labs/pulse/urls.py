from django.urls import path

from connect_labs.pulse import api, views

app_name = "pulse"

urlpatterns = [
    # Read API — the only surface cards talk to.
    path("api/summary/", api.SummaryView.as_view(), name="api_summary"),
    path("api/events/", api.EventsView.as_view(), name="api_events"),
    path("api/replay/", api.ReplayView.as_view(), name="api_replay"),
    # Authenticated views.
    path("", views.PulseIndexView.as_view(), name="index"),
    path("v/<slug:layout>/", views.PulseDisplayView.as_view(), name="display"),
    # Public, unauthenticated, token-scoped. Revocable per link.
    path("p/<str:token>/", views.PulsePublicView.as_view(), name="public"),
]
