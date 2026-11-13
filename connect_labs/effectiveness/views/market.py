"""The Market size page and its API: the cost of reaching everyone in chosen places, and where it is worth it.

The places come from the baseline's selection (``labs.indicators``); the arithmetic is ``effectiveness.market``.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.effectiveness import engine, explore, groups, market, valuation
from connect_labs.labs.chrome import NOT_USED, page_chrome
from connect_labs.labs.indicators import measures, response_cache
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.resolve import select_above
from connect_labs.labs.indicators.views import (
    OpenLocallyMixin,
    _admin_level,
    _float,
    _indicator,
    _iso_codes,
    _method,
    _rollup,
    _target_year,
)

#: The burden indicators the market-size page offers for choosing places (higher means more need).
TARGET_INDICATORS = ("u5mr", "nmr", "diarrhoea_prevalence")
#: "Everywhere with data": the indicator that has values in the most countries, with no threshold, so the market is
#: every place the baseline can count people in rather than only the highest-burden ones.
EVERYWHERE = "all"
EVERYWHERE_INDICATOR = "diarrhoea_prevalence"
CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


def _where(request) -> tuple[str, float]:
    """The indicator and threshold that choose the places; ``indicator=all`` means everywhere with data."""
    if request.GET.get("indicator") == EVERYWHERE:
        return EVERYWHERE_INDICATOR, 0.0
    indicator = _indicator(request)
    return indicator, _float(request, "threshold", measures.get(indicator).threshold_default)


def _models(request) -> list[str]:
    keys = [m for m in request.GET.get("models", "").split(",") if m in engine.MODELS]
    return keys or ["chc"]


def _overrides(request, model: str) -> dict:
    """``i.<model>.<key>=value`` query parameters: one model's input changes."""
    prefix = f"i.{model}."
    return explore.clean_overrides(
        model, {k[len(prefix) :]: v for k, v in request.GET.items() if k.startswith(prefix)}
    )


def _people(request, indicator: str, threshold: float) -> dict:
    """People per country in the places the baseline selects, for every model's count; cached with the data."""
    params = [
        indicator,
        threshold,
        sorted(_iso_codes(request)),
        _method(request, indicator),
        _target_year(request),
        _rollup(request),
        _admin_level(request),
    ]
    key = "market:people:" + hashlib.sha1(f"{response_cache.data_version()}|{params}".encode()).hexdigest()
    got = cache.get(key)
    if got is not None:
        return got
    selection = select_above(
        indicator=indicator,
        threshold=threshold,
        iso_codes=_iso_codes(request),
        method=_method(request, indicator),
        extra_counts=market.count_measures(),
        target_year=_target_year(request),
        rollup=_rollup(request),
        admin_level=_admin_level(request),
    )
    by_country: dict[str, dict] = {}
    for area in selection.areas:
        c = by_country.setdefault(area.iso_code, {"name": area.country_name, "counts": {}, "partial": set()})
        for m in market.count_measures():
            v = area.counts.get(m)
            if v is None:
                c["partial"].add(m)
            else:
                c["counts"][m] = c["counts"].get(m, 0) + v
    out = {
        "countries": {
            iso: {"name": c["name"], "counts": c["counts"], "partial": sorted(c["partial"])}
            for iso, c in by_country.items()
        },
        "regions": selection.unit_count,
        "unsupported": selection.countries_unsupported,
        "method": selection.method,
    }
    cache.set(key, out, response_cache.TTL_SECONDS)
    return out


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
            "indicators": [{"key": EVERYWHERE, "label": "Everywhere with data", "threshold": None}]
            + [
                {"key": k, "label": measures.get(k).label, "threshold": measures.get(k).threshold_default}
                for k in TARGET_INDICATORS
                if k in measures.MEASURES
            ],
            "blocks": market_blocks(),
            "bar": valuation.bar(),
            "api_url": request.path + "api/",
        }
        return render(request, "effectiveness/market.html", {"data": data})


def market_blocks() -> list[dict]:
    return [{"key": g, "title": groups.TITLES[g]} for g in groups.KEY_GROUPS]


class MarketSizeDataView(OpenLocallyMixin, View):
    """``?models=chc,kc&indicator=u5mr&threshold=80&iso=NGA,NER&bar=6&i.chc.cost=2`` -> each model's market."""

    def get(self, request):
        keys = _models(request)
        indicator, threshold = _where(request)
        bar = _float(request, "bar", valuation.bar())
        try:
            overrides = {k: _overrides(request, k) for k in keys}
        except explore.ExploreError as exc:
            return JsonResponse({"error": exc.message}, status=400)
        found = _people(request, indicator, threshold)
        out = []
        for k in keys:
            measure = market.UNITS[k].measure
            people = {
                iso: {"name": c["name"], "units": c["counts"].get(measure), "partial": measure in c["partial"]}
                for iso, c in found["countries"].items()
            }
            try:
                out.append({**market.size(k, people, overrides[k], bar), "overrides": overrides[k]})
            except ZeroDivisionError:
                return JsonResponse({"error": f"Those inputs divide by zero in the {k} model."}, status=400)
        return JsonResponse(
            {
                "models": out,
                "bar": bar,
                "selection": {
                    "indicator": indicator,
                    "indicator_label": measures.get(indicator).label,
                    "threshold": threshold,
                    "method": found["method"],
                    "countries": len(found["countries"]),
                    "africa_total": len(ISO_CODES),
                    "everywhere": request.GET.get("indicator") == EVERYWHERE,
                    "regions": found["regions"],
                    "unsupported": found["unsupported"],
                },
            }
        )
