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
    path("api/interventions/", views.InterventionsView.as_view(), name="interventions"),
    path("api/scenario/", views.ScenarioView.as_view(), name="scenario"),
    path("download/", views.SelectionDownloadView.as_view(), name="download"),
    # Malaria PMC is a view of the cost-effectiveness explorer and moved to /labs/cea/targeting/pmc/
    # (connect_labs/cea_urls.py). These keep the old addresses working: the page for a person's saved
    # link, the API for a page that was open across the deploy (307, so a POST stays a POST).
    path("pmc/", RedirectView.as_view(pattern_name="cea:pmc", query_string=True, permanent=False)),
    path("api/pmc/", _moved_api("cea:pmc_data")),
    path("api/pmc/rank/", _moved_api("cea:pmc_rank")),
    path("api/pmc/run/", _moved_api("cea:pmc_run")),
    path("api/pmc/run/<int:pk>/", _moved_api("cea:pmc_run_status")),
]
