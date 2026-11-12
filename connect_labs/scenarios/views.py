"""Market sizing: what a unit price buys over the places a threshold selects. Served at /labs/scenarios/."""

from __future__ import annotations

from django.http import JsonResponse
from django.views import View

from connect_labs.labs.indicators import measures
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
from connect_labs.scenarios import interventions


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
