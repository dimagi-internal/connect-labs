"""URLs for the targeting surface, mounted at /labs/targeting/."""

from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.views.generic import RedirectView

from connect_labs.labs.indicators import views

app_name = "indicators"


def _moved_api(name):
    """A view that sends an old API address to its new one with the method and query kept."""

    def view(request, **kwargs):
        target = reverse(name, kwargs=kwargs)
        if request.META.get("QUERY_STRING"):
            target += "?" + request.META["QUERY_STRING"]
        response = HttpResponseRedirect(target)
        response.status_code = 307
        return response

    return view


urlpatterns = [
    path("", views.TargetingView.as_view(), name="index"),
    path("api/map/", views.MapDataView.as_view(), name="map_data"),
    path("api/selection/", views.SelectionView.as_view(), name="selection"),
    path("api/coverage/", views.CoverageView.as_view(), name="coverage"),
    path("api/methods/", views.MethodsView.as_view(), name="methods"),
    path("api/scope/", views.ScopeView.as_view(), name="scope"),
    path("api/methodology/", views.MethodologyView.as_view(), name="methodology"),
    # Market sizing moved to the scenarios component (/labs/scenarios/); these keep the old addresses working.
    path("api/interventions/", _moved_api("scenarios:interventions")),
    path("api/scenario/", _moved_api("scenarios:scenario")),
    path("download/", views.SelectionDownloadView.as_view(), name="download"),
    # Malaria PMC was retired on 13 Nov 2026: a saved link lands on Market size.
    path("pmc/", RedirectView.as_view(pattern_name="cea:market", permanent=False)),
]
