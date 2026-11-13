"""The Market size page and its API: the cost of reaching everyone in chosen places, and where it is worth it.

The places come from the baseline's selection (``people``); the arithmetic is ``effectiveness.market``.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import engine, explore, groups, market, people, valuation
from connect_labs.labs.chrome import NOT_USED, page_chrome
from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.access import OpenLocallyMixin
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.query import float_param

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


def _models(request) -> list[str]:
    keys = [m for m in request.GET.get("models", "").split(",") if m in engine.MODELS]
    return keys or ["chc"]


def _overrides(request, model: str) -> dict:
    """``i.<model>.<key>=value`` query parameters: one model's input changes."""
    prefix = f"i.{model}."
    return explore.clean_overrides(
        model, {k[len(prefix) :]: v for k, v in request.GET.items() if k.startswith(prefix)}
    )


@CONTEXT_NOT_USED
class MarketSizeView(OpenLocallyMixin, View):
    """The page: choose places and models, change inputs, read the market. The numbers come from the API below."""

    def get(self, request):
        meta = explore.models()
        data = {
            "models": {
                k: {
                    **meta[k],
                    "key": k,
                    "noun": market.UNITS[k].noun,
                    "inputs": market.key_inputs(k, explore.inputs(k)),
                }
                for k in engine.MODELS
            },
            "indicators": [{"key": people.EVERYWHERE, "label": "Everywhere with data", "threshold": None}]
            + [
                {"key": k, "label": measures.get(k).label, "threshold": measures.get(k).threshold_default}
                for k in people.TARGET_INDICATORS
                if k in measures.MEASURES
            ],
            "blocks": [{"key": g, "title": groups.TITLES[g]} for g in groups.KEY_GROUPS],
            "bar": valuation.bar(),
            "api_url": request.path + "api/",
        }
        return render(request, "effectiveness/market.html", {"data": data})


class MarketSizeDataView(OpenLocallyMixin, View):
    """``?models=chc,kc&indicator=u5mr&threshold=80&iso=NGA,NER&bar=6&i.chc.cost=2`` -> each model's market."""

    def get(self, request):
        keys = _models(request)
        question, everywhere = people.places_question(request)
        bar = float_param(request, "bar", valuation.bar())
        try:
            overrides = {k: _overrides(request, k) for k in keys}
        except explore.ExploreError as exc:
            return JsonResponse({"error": exc.message}, status=400)
        found = people.people_by_country(question)
        out = []
        for k in keys:
            try:
                out.append(
                    {**market.size(k, people.for_model(found, k), overrides[k], bar), "overrides": overrides[k]}
                )
            except ZeroDivisionError:
                return JsonResponse({"error": f"Those inputs divide by zero in the {k} model."}, status=400)
        return JsonResponse(
            {
                "models": out,
                "bar": bar,
                "selection": {
                    "indicator": question.indicator,
                    "indicator_label": measures.get(question.indicator).label,
                    "threshold": question.threshold,
                    "method": found["method"],
                    "countries": len(found["countries"]),
                    "africa_total": len(ISO_CODES),
                    "everywhere": everywhere,
                    "regions": found["regions"],
                    "unsupported": found["unsupported"],
                },
            }
        )
