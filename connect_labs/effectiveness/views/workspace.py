"""The workspace page: one scenario, a map of its places, and the numbers beside it.

A new layout of the same scenario Market size reads (``scenario_page``), answered by the same API
(``cea:market_data``); this page adds the map, so it also serves the country outlines (``shapes``).
"""

from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import shapes
from connect_labs.effectiveness.views.scenario_page import ScenarioPageGate, page_data, scenario_or_default
from connect_labs.labs.chrome import NOT_USED, page_chrome

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


@CONTEXT_NOT_USED
class WorkspaceView(ScenarioPageGate, View):
    def get(self, request):
        scenario, error = scenario_or_default(request)
        data = {**page_data(request, scenario, error), "shapes_url": reverse("cea:workspace_shapes")}
        token = getattr(settings, "MAPBOX_TOKEN", "") or ""
        return render(request, "effectiveness/workspace.html", {"data": data, "mapbox_token": token})


class WorkspaceShapesView(ScenarioPageGate, View):
    """Country outlines, the same for every scenario."""

    def get(self, request):
        return JsonResponse(shapes.country_outlines(), json_dumps_params={"separators": (",", ":")})
