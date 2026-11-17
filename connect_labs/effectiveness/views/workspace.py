"""The workspace page: one scenario, a map of its places, and the numbers beside it.

A new layout of the same scenario Market size reads (``scenario_page``), answered by the same API
(``cea:market_data``); this page adds the map, so it also serves the outlines (``shapes``). It starts at the territory
grain, where the best places to work show.
"""

from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import shapes
from connect_labs.effectiveness.views.scenario_page import (
    ScenarioPageGate,
    agent_panel,
    page_data,
    scenario_or_default,
)
from connect_labs.labs.chrome import NOT_USED, page_chrome

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


@CONTEXT_NOT_USED
class WorkspaceView(ScenarioPageGate, View):
    def get(self, request):
        scenario, loaded, error = scenario_or_default(request, default_grain="territory")
        data = {**page_data(request, scenario, loaded, error), "shapes_url": reverse("cea:workspace_shapes")}
        token = getattr(settings, "MAPBOX_TOKEN", "") or ""
        panel = agent_panel(
            request, "labs-cea://workspace", "cea_market_size", {"scenario": request.GET.get("scenario")}
        )
        return render(
            request, "effectiveness/workspace.html", {"data": data, "mapbox_token": token, "canopy_panel": panel}
        )


class WorkspaceShapesView(ScenarioPageGate, View):
    """Outlines of the countries or, with ``?grain=territory``, of their states and provinces: the same for every
    scenario."""

    def get(self, request):
        grain = "territory" if request.GET.get("grain") == "territory" else "country"
        return JsonResponse(shapes.outlines(grain), json_dumps_params={"separators": (",", ":")})
