"""Market sizing: what a unit price buys over the places a threshold selects. Served at /labs/scenarios/."""

from __future__ import annotations

import hashlib

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.impact import engine, explore, valuation
from connect_labs.labs.chrome import NOT_USED, page_chrome
from connect_labs.labs.indicators import measures, response_cache
from connect_labs.labs.indicators.bases import UnitBasis, measure_for
from connect_labs.labs.indicators.resolve import select_above
from connect_labs.labs.indicators.response_cache import CachedJsonMixin
from connect_labs.labs.indicators.views import (
    DEFAULT_INDICATOR,
    OpenLocallyMixin,
    _admin_level,
    _float,
    _indicator,
    _iso_codes,
    _method,
    _plural,
    _rollup,
    _target_year,
)
from connect_labs.scenarios import interventions, market


class ScenarioView(OpenLocallyMixin, CachedJsonMixin, View):
    """What a unit price buys, over the places a threshold selects.

    The arithmetic is trivial once two things are fixed: a **unit cost** and a
    **unit of measure**. Which unit applies is a property of the intervention,
    not of the data — a bednet is priced per child, a water connection per
    household, a treatment per case — so the basis is chosen rather than
    inferred, and named interventions are presets for a basis and a price.
    """

    cache_name = "scenario"

    def get(self, request):
        slug = request.GET.get("intervention")
        basis_param = request.GET.get("basis")
        intervention = None

        if slug:
            try:
                intervention = interventions.get(slug)
            except KeyError:
                return JsonResponse({"error": f"unknown intervention {slug!r}"}, status=400)

        try:
            basis = (
                UnitBasis(basis_param) if basis_param else (intervention.basis if intervention else UnitBasis.PERSON)
            )
        except ValueError:
            return JsonResponse(
                {
                    "error": f"unknown basis {basis_param!r}",
                    "valid": [b.value for b in UnitBasis],
                },
                status=400,
            )

        indicator = _indicator(request, intervention.targets if intervention else DEFAULT_INDICATOR)
        if indicator not in measures.MEASURES:
            return JsonResponse({"error": f"unknown indicator {indicator!r}"}, status=400)

        threshold = _float(request, "threshold", measures.get(indicator).threshold_default)
        default_cost = intervention.unit_cost_usd if intervention else 1.0
        unit_cost = _float(request, "unit_cost", default_cost)

        cases_measure = measure_for(basis, indicator)
        if cases_measure is None:
            return JsonResponse(
                {
                    "error": (
                        f"a '{basis.value}' basis has no case count for {indicator!r} — "
                        "that indicator has no coverage figure to imply untreated cases"
                    )
                },
                status=400,
            )

        # Same scope, level and delivery year as the table above it. A cost
        # computed over the whole continent while the table shows one country
        # is not a caveat, it is a different answer in the same panel.
        selection = select_above(
            indicator=indicator,
            threshold=threshold,
            iso_codes=_iso_codes(request),
            method=_method(request, indicator),
            extra_counts=(cases_measure,),
            target_year=_target_year(request),
            rollup=_rollup(request),
            admin_level=_admin_level(request),
        )

        cases = selection.totals.get(cases_measure)
        got, total = selection.coverage.get(cases_measure, (0, 0))

        return JsonResponse(
            {
                "intervention": (
                    {
                        "slug": intervention.slug,
                        "label": intervention.label,
                        "description": intervention.description,
                        "caveat": intervention.caveat,
                        "default_unit_cost": intervention.unit_cost_usd,
                    }
                    if intervention
                    else None
                ),
                "basis": {
                    "code": basis.value,
                    "label": basis.label,
                    "noun": basis.noun,
                    "noun_plural": _plural(basis.noun),
                    "measure": cases_measure,
                    "measure_label": measures.get(cases_measure).label,
                },
                "indicator": indicator,
                "indicator_label": measures.get(indicator).label,
                "threshold": threshold,
                "unit_cost": unit_cost,
                "method": selection.method,
                "units": cases,
                "absorbable_usd": interventions.cost(cases, unit_cost) if cases else None,
                # Units are summed only where a value exists, so an incomplete
                # selection yields a floor — which is worth saying rather than
                # letting a confident total imply completeness.
                "unit_coverage": {"with_value": got, "of": total},
                "complete": bool(total and got == total),
                "counts": {
                    "regions": selection.unit_count,
                    "countries": selection.country_count,
                },
                "countries_unsupported": selection.countries_unsupported,
            }
        )


class InterventionsView(OpenLocallyMixin, View):
    """Unit bases and the intervention presets built on them."""

    def get(self, request):
        indicator = _indicator(request)
        return JsonResponse(
            {
                "bases": [
                    {
                        "code": b.value,
                        "label": b.label,
                        "noun": b.noun,
                        "measure": measure_for(b, indicator),
                        "available_for_indicator": measure_for(b, indicator) is not None,
                    }
                    for b in UnitBasis
                ],
                "interventions": [
                    {
                        "slug": i.slug,
                        "label": i.label,
                        "basis": i.basis.value,
                        "unit_cost_usd": i.unit_cost_usd,
                        "unit_noun": i.unit_noun,
                        "targets": i.targets,
                        "description": i.description,
                        "caveat": i.caveat,
                    }
                    for i in interventions.all_interventions()
                ],
            }
        )


#: The indicators the market-size page offers for choosing places: where need is highest, in the baseline's own terms.
TARGET_INDICATORS = (
    "u5mr",
    "nmr",
    "diarrhoea_prevalence",
    "ors_coverage",
    "vitamin_a_coverage",
    "measles_vaccination",
    "itn_use_children",
)
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
            "indicators": [
                {"key": k, "label": measures.get(k).label, "threshold": measures.get(k).threshold_default}
                for k in TARGET_INDICATORS
                if k in measures.MEASURES
            ],
            "blocks": market_blocks(),
            "bar": valuation.bar(),
            "api_url": request.path + "api/",
        }
        return render(request, "scenarios/market.html", {"data": data})


def market_blocks() -> list[dict]:
    from connect_labs.impact import groups

    return [{"key": g, "title": groups.TITLES[g]} for g in groups.KEY_GROUPS]


class MarketSizeDataView(OpenLocallyMixin, View):
    """``?models=chc,kc&indicator=u5mr&threshold=80&iso=NGA,NER&bar=6&i.chc.cost=2`` -> each model's market."""

    def get(self, request):
        keys = _models(request)
        indicator = _indicator(request)
        threshold = _float(request, "threshold", measures.get(indicator).threshold_default)
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
                    "regions": found["regions"],
                    "unsupported": found["unsupported"],
                },
            }
        )
