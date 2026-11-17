"""The Market size page and its API: the cost of reaching everyone in chosen places, and where it is worth it.

The choices are a ``Scenario`` (``effectiveness.scenario``) and the answer is ``effectiveness.sizing``; the page
and the workspace page draw the same payload.
"""

from __future__ import annotations

import csv

from django.http import HttpResponse, JsonResponse
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
        scenario, loaded, error = scenario_or_default(request)
        return render(request, "effectiveness/market.html", {"data": page_data(request, scenario, loaded, error)})


class MarketSizeDataView(ScenarioPageGate, View):
    """``?models=chc,kc&indicator=u5mr&threshold=80&iso=NGA,NER&chc_reach=50&i.chc.cost=2`` -> each model's market."""

    def get(self, request):
        try:
            return JsonResponse(sizing.market_of(Scenario.from_params(request.GET)))
        except explore.ExploreError as exc:
            return JsonResponse({"error": exc.message}, status=400)


class MarketPlanCsvView(ScenarioPageGate, View):
    """The plan as a spreadsheet: ``?models=kc&target=1000000&grain=territory...`` -> one row per place, then the
    totals."""

    def get(self, request):
        try:
            scenario = Scenario.from_params(request.GET)
            payload = sizing.market_of(scenario)
        except explore.ExploreError as exc:
            return HttpResponse(exc.message, status=400, content_type="text/plain")
        planned = [m for m in payload["models"] if m.get("plan")]
        if not planned:
            return HttpResponse(
                "Give a target (target=<people>) to get a plan.", status=400, content_type="text/plain"
            )
        m, plan = planned[0], planned[0]["plan"]
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="plan-{m["model"]}-{int(plan["target"])}.csv"'
        out = csv.writer(response)
        out.writerow(
            [
                "rank",
                "place",
                "country",
                f"{m['noun']} reached",
                "share of its cases",
                "cost a year (USD)",
                "deaths averted a year",
                "multiple of benchmark",
                "baseline risk",
            ]
        )
        for i, p in enumerate(plan["places"], 1):
            out.writerow(
                [
                    i,
                    p["name"],
                    p["country"],
                    round(p["reached"]),
                    round(p["share_of_cases"] or 0, 3),
                    round(p["cost"]),
                    round(p["deaths_averted"], 1),
                    round(p["multiple"], 2),
                    round(p["risk"], 4) if p["risk"] is not None else "",
                ]
            )
        t = plan["totals"]
        out.writerow([])
        out.writerow(
            [
                "total",
                f"{t['places']} places",
                f"{t['countries']} countries",
                round(t["people"]),
                "",
                round(t["cost"]),
                round(t["deaths_averted"], 1),
                round(t["multiple"], 2) if t["multiple"] else "",
                "",
            ]
        )
        out.writerow(["cost per death averted (USD)", round(t["cost_per_death"]) if t["cost_per_death"] else ""])
        out.writerow(["target", round(plan["target"]), "shortfall", round(plan["shortfall"])])
        out.writerow([])
        out.writerow(["scenario link", "&".join(f"{k}={v}" for k, v in payload["link"].items())])
        out.writerow(
            [
                "note",
                "Modelled, not measured. Kangaroo Care's baseline risk is scaled to local newborn mortality by Labs "
                "when place_risk is on.",
            ]
        )
        return response
