"""A tiny urlconf for the route-metric tests: one nested include with an id, so the
test sees the same joined ``ResolverMatch.route`` a real labs URL produces."""

from django.http import HttpResponse, HttpResponseNotFound
from django.urls import include, path


def _ok(request, **kwargs):
    return HttpResponse("ok")


def _boom(request):
    return HttpResponse("nope", status=503)


def not_found(request, exception):
    # The real 404 template needs the full labs urlconf and auth; this test does not.
    return HttpResponseNotFound("missing")


handler404 = "connect_labs.utils.tests.route_metric_urls.not_found"

workflow_patterns = [
    path("<int:workflow_id>/", _ok, name="detail"),
    path("<int:workflow_id>/runs/<slug:run_slug>/", _ok, name="run"),
]

urlpatterns = [
    path("labs/workflow/", include(workflow_patterns)),
    path("unavailable/", _boom),
    path("", _ok),
]
