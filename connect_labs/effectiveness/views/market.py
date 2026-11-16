"""The Market size page and its API: the cost of reaching everyone in chosen places, and where it is worth it.

The choices are a ``Scenario`` (``effectiveness.scenario``) and the answer is ``effectiveness.sizing``; the page
and the workspace page draw the same payload.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import explore, sizing
from connect_labs.effectiveness.scenario import Scenario
from connect_labs.effectiveness.views.scenario_page import ScenarioPageGate, page_data, scenario_or_default
from connect_labs.labs.chrome import NOT_USED, page_chrome

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


@CONTEXT_NOT_USED
class MarketSizeView(ScenarioPageGate, View):
    """The page: choose places and models, change inputs, read the market. The numbers come from the API below."""

    def get(self, request):
        scenario, error = scenario_or_default(request)
        return render(request, "effectiveness/market.html", {"data": page_data(request, scenario, error)})


class MarketSizeDataView(ScenarioPageGate, View):
    """``?models=chc,kc&indicator=u5mr&threshold=80&iso=NGA,NER&chc_reach=50&i.chc.cost=2`` -> each model's market."""

    def get(self, request):
        try:
            return JsonResponse(sizing.market_of(Scenario.from_params(request.GET)))
        except explore.ExploreError as exc:
            return JsonResponse({"error": exc.message}, status=400)
