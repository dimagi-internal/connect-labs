"""The targeting surface: a map, a threshold, and a downloadable answer."""

from __future__ import annotations

import json
import logging

import markdown
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.indicators import availability
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators import export, interventions, measures, methods
from connect_labs.labs.indicators.africa import ISO_CODES, name_for
from connect_labs.labs.indicators.models import IndicatorValue, IngestRun, Source
from connect_labs.labs.indicators.resolve import BulkResolver, select_above

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 80.0
DEFAULT_INDICATOR = "u5mr"
DEFAULT_METHOD = methods.default_for(methods.Resolution.SUBNATIONAL).code

#: Degrees of simplification for map geometry. ADM1 polygons carry tens of
#: thousands of vertices; at continent zoom the difference is invisible and the
#: payload is an order of magnitude smaller.
MAP_SIMPLIFY = 0.02


class OpenLocallyMixin(LoginRequiredMixin):
    """Login-gated when deployed, open when running locally.

    This surface shows only public open data — WorldPop, DHS, UN IGME,
    geoBoundaries — and nothing specific to the signed-in user, so requiring the
    Connect OAuth round trip to look at it locally buys no protection and costs
    real friction: on a laptop with an expired CLI token the OAuth flow simply
    fails and the page is unreachable.

    Deployments keep the gate, matching every other labs page. If this should be
    public on labs too, drop the mixin rather than widening the exception.
    """

    def dispatch(self, request, *args, **kwargs):
        if settings.DEBUG:
            # Skip LoginRequiredMixin's check, keep the rest of the MRO.
            return super(LoginRequiredMixin, self).dispatch(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)


def source_name(code: str) -> str:
    """Human name for a source code.

    A rolled-up country row can carry several sources joined by "+", because its
    regions were not all measured the same way; say so rather than picking one.
    """
    if not code:
        return ""
    if "+" in code:
        parts = [source_name(c) for c in code.split("+")]
        return " + ".join(dict.fromkeys(p for p in parts if p))
    try:
        return Source(code).label
    except ValueError:
        return code


def _row_method_label(r, selected) -> str | None:
    """The method that actually produced THIS row, not the one that was asked for.

    A region with no value of its own inherits from an ancestor, and what it
    inherits is not necessarily one of the selected method's sources: most rows
    under "Survey as measured" for DR Congo are IGME's national figure applied
    downward, because the survey did not reach those provinces.

    Labelling every row with the selected method contradicted the ``logic``
    column beside it — "Survey as measured" against "IGME national model" — and
    hid the one thing a reader needs to weigh the row. Name what answered.
    """
    if r is None or selected is None:
        return selected.label if selected else None

    sources = r.source.split("+") if "+" in r.source else [r.source]
    if all(src in selected.source_order for src in sources):
        return selected.label

    labels = []
    for src in sources:
        answering = next((m for m in methods.METHODS.values() if m.source_order[:1] == (src,)), None)
        labels.append(answering.label if answering else source_name(src))
    return " + ".join(dict.fromkeys(labels))


def _row_logic(r, area) -> str:
    """A short account of how this row's value was arrived at."""
    if r is None:
        return ""
    steps: list[str] = []

    if r.source == "igme_subnational":
        steps.append(f"IGME small-area model, ADM{area.admin_level}")
    elif r.source == "igme":
        steps.append("IGME national model")
    elif r.source == "dhs_calibrated":
        f = r.extra.get("factor")
        steps.append(
            f"survey {r.measured_year}, re-levelled x{f:.2f}" if f else f"survey {r.measured_year}, re-levelled"
        )
        if r.extra.get("rake_factor"):
            steps.append(f"raked x{r.extra['rake_factor']:.2f} to the national figure")
    elif r.source == "dhs":
        steps.append(f"survey {r.measured_year}, as measured")
    elif "+" in (r.source or ""):
        steps.append("weighted mean across regions")
    else:
        steps.append(r.source or "")

    if r.inherited and r.measured_at is not None:
        steps.append(f"national figure applied from {r.measured_at.name}")
    if area.is_whole_country:
        steps.append(f"rolled up from {area.units_covered} regions")

    return "; ".join(x for x in steps if x)


def _plural(noun: str) -> str:
    """Enough English for the handful of nouns interventions actually use."""
    if noun.endswith("ild"):
        return noun + "ren"
    if noun.endswith(("s", "x", "ch", "sh")):
        return noun + "es"
    return noun + "s"


def _round_or_none(value):
    """Round for display, but keep "no estimate" distinct from zero."""
    return None if value is None else round(value)


def _method(request, indicator: str = "u5mr") -> str:
    """The requested method, or a default that can actually answer this indicator.

    An explicit choice is honoured even when it has no data — the surface then
    says so, which is the point. Only the default adapts.
    """
    code = request.GET.get("method")
    if code and code in methods.METHODS:
        return code
    resolution = request.GET.get("resolution")
    res = methods.Resolution(resolution) if resolution in (r.value for r in methods.Resolution) else None
    if res is None:
        return DEFAULT_METHOD
    return availability.default_method_for(indicator, res).code


def _float(request, key, default):
    try:
        return float(request.GET.get(key, default))
    except (TypeError, ValueError):
        return default


class TargetingView(OpenLocallyMixin, TemplateView):
    """The map page."""

    template_name = "indicators/targeting.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        measure = measures.get(DEFAULT_INDICATOR)

        loaded = (
            boundary_set.owned().filter(iso_code__in=ISO_CODES, admin_level=1).values("iso_code").distinct().count()
        )
        with_u5mr = IndicatorValue.objects.filter(indicator="u5mr").values("iso_code").distinct().count()

        ctx.update(
            {
                "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or "",
                "default_threshold": DEFAULT_THRESHOLD,
                "indicator": DEFAULT_INDICATOR,
                "indicator_label": measure.label,
                "indicator_unit": measure.unit,
                "countries_with_boundaries": loaded,
                "countries_with_u5mr": with_u5mr,
                "africa_total": len(ISO_CODES),
                "last_runs": list(IngestRun.objects.filter(ok=True)[:5]),
                "default_method": DEFAULT_METHOD,
            }
        )
        return ctx


class MapDataView(OpenLocallyMixin, View):
    """GeoJSON for the choropleth: one feature per ADM1 unit, carrying its numbers.

    Countries with no ADM1 boundaries fall back to their ADM0 outline so they
    appear on the map as a single unit rather than as a hole.
    """

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None
        simplify = _float(request, "simplify", MAP_SIMPLIFY)

        method = methods.get(_method(request, indicator))
        supported = set(availability.countries_supporting(method, indicator))

        # A national method paints one shape per country. Painting a national
        # figure onto regions would look like subnational detail that does not
        # exist. Countries the method cannot answer for are simply absent.
        level = 0 if method.is_national else 1
        units = list(boundary_set.owned().filter(iso_code__in=supported, admin_level=level))

        bulk = BulkResolver(units, year=year, source_order=method.source_order)
        features = []

        for b in units:
            rate = bulk.get(indicator, b)
            geom = b.geometry.simplify(simplify, preserve_topology=True) if simplify else b.geometry
            if geom.empty or geom.num_coords == 0:
                geom = b.geometry
            features.append(
                {
                    "type": "Feature",
                    "id": b.pk,
                    "geometry": json.loads(geom.geojson),
                    "properties": {
                        "pk": b.pk,
                        "name": b.name,
                        "iso": b.iso_code,
                        "country": name_for(b.iso_code),
                        "level": b.admin_level,
                        indicator: round(rate.value, 1) if rate else None,
                        "inherited": bool(rate and rate.inherited),
                        "source": (rate.source_ref or rate.source) if rate else None,
                        "source_url": (rate.source_url or "") if rate else None,
                        "year": rate.year if rate else None,
                        "births": _round_or_none(r2.value if (r2 := bulk.get("births", b)) else None),
                        "pop_u5": _round_or_none(r3.value if (r3 := bulk.get("pop_u5", b)) else None),
                        "pop_total": _round_or_none(r4.value if (r4 := bulk.get("pop_total", b)) else None),
                    },
                }
            )

        return JsonResponse(
            {"type": "FeatureCollection", "features": features},
            json_dumps_params={"separators": (",", ":")},
        )


class SelectionView(OpenLocallyMixin, View):
    """Apply a threshold and return the headline numbers plus the table."""

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        threshold = _float(request, "threshold", DEFAULT_THRESHOLD)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None

        # Scoped to Africa exactly as the map is: a table listing places the
        # map cannot show would be a quiet contradiction.
        # Scoped to Africa exactly as the map is, and produced with the
        # requested method so the table and the map always agree.
        selection = select_above(
            indicator=indicator,
            threshold=threshold,
            year=year,
            iso_codes=ISO_CODES,
            method=_method(request, indicator),
        )
        measure = measures.get(indicator)

        return JsonResponse(
            {
                "indicator": indicator,
                "indicator_label": measure.label,
                "indicator_unit": measure.unit,
                "lower_is_worse": indicator in measures.LOWER_IS_WORSE,
                "gap_label": (
                    measures.get(f"{indicator}_gap").label if f"{indicator}_gap" in measures.MEASURES else None
                ),
                "threshold": threshold,
                "threshold_pct": measures.percent_equivalent(indicator, threshold),
                "totals": {
                    "expected_deaths": selection.totals.get("expected_deaths"),
                    "ors_gap_children": selection.totals.get("ors_gap_children"),
                    "gap": selection.totals.get(f"{indicator}_gap"),
                    "births": selection.totals.get("births"),
                    "pop_u5": selection.totals.get("pop_u5"),
                    "pop_total": selection.totals.get("pop_total"),
                    indicator: selection.totals.get(indicator),
                },
                "counts": {
                    "rows": selection.area_count,
                    "units": selection.unit_count,
                    "countries": selection.country_count,
                    # Units answered by a source this method does not declare —
                    # a region with no value of its own inheriting from one that
                    # has. Reported so a reader can ask how much of the
                    # selection is really this method's own measurement.
                    "off_method_units": selection.off_method_units,
                },
                # How much of the selection actually carries each count. Where
                # these fall short the total is a floor, and the UI says so
                # rather than presenting an undercount as a measurement.
                "coverage": {c: {"with_value": got, "of": total} for c, (got, total) in selection.coverage.items()},
                "method": selection.method,
                "resolution": selection.resolution,
                "countries_unsupported": selection.countries_unsupported,
                "countries_fully_above": selection.countries_fully_above,
                "countries_partly_above": selection.countries_partly_above,
                "skipped_no_data": selection.skipped_no_data,
                "selected_pks": [a.boundary.pk for a in selection.areas],
                "rows": [
                    {
                        "country": a.country_name,
                        "iso": a.iso_code,
                        "name": a.name,
                        "level": a.admin_level,
                        "whole_country": a.is_whole_country,
                        "units_covered": a.units_covered,
                        "value": round(r.value, 1) if (r := a.values.get(indicator)) else None,
                        "ci_low": round(r.ci_low, 1) if r and r.ci_low is not None else None,
                        "ci_high": round(r.ci_high, 1) if r and r.ci_high is not None else None,
                        # A published interval that spans the cut point means
                        # this row's membership is not distinguishable from
                        # chance at this threshold.
                        "straddles_threshold": bool(
                            r and r.ci_low is not None and r.ci_high is not None and r.ci_low <= threshold <= r.ci_high
                        ),
                        # The logic behind this particular row, not just the
                        # dataset it came from: which method answered, at what
                        # level, and what was done to the value on the way.
                        "method_label": _row_method_label(
                            r, methods.get(selection.method) if selection.method else None
                        ),
                        "logic": _row_logic(r, a),
                        "source_name": source_name(r.source) if r else None,
                        "source_detail": (r.source_ref or "") if r else "",
                        "source_url": (r.source_url or "") if r else "",
                        "year": r.measured_year if r else None,
                        "adjusted": bool(r and r.adjusted),
                        "adjusted_note": (
                            f"survey value {r.extra['raw_value']:.0f} in {r.extra['raw_year']}, "
                            f"re-levelled x{r.extra['factor']:.2f} to {r.year}"
                            if r and r.adjusted
                            else ""
                        ),
                        "inherited": bool(r and r.inherited),
                        "measured_at": (
                            f"{r.measured_at.name} (ADM{r.measured_at.admin_level})" if r and r.inherited else None
                        ),
                        "expected_deaths": _round_or_none(a.counts.get("expected_deaths")),
                        "ors_gap_children": _round_or_none(a.counts.get("ors_gap_children")),
                        "gap": _round_or_none(a.counts.get(f"{indicator}_gap")),
                        "births": _round_or_none(a.counts.get("births")),
                        "pop_u5": _round_or_none(a.counts.get("pop_u5")),
                        "pop_total": _round_or_none(a.counts.get("pop_total")),
                        "births_partial": not a.is_complete("births"),
                    }
                    for a in selection.areas
                ],
            }
        )


class SelectionDownloadView(OpenLocallyMixin, View):
    """The table and its methodology, zipped together."""

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        threshold = _float(request, "threshold", DEFAULT_THRESHOLD)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None
        fmt = request.GET.get("format", "zip")

        selection = select_above(
            indicator=indicator,
            threshold=threshold,
            year=year,
            iso_codes=ISO_CODES,
            method=_method(request, indicator),
        )
        stem = export.filename_stem(selection)

        if fmt == "csv":
            resp = HttpResponse(export.to_csv(selection), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="{stem}.csv"'
            return resp

        resp = HttpResponse(export.to_zip(selection), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
        return resp


class MethodologyView(OpenLocallyMixin, View):
    """The workings behind the current selection, rendered for the page.

    This is the same text the download ships as ``METHODOLOGY.md`` — same
    function, not a second copy — because a methodology the page paraphrases is
    one that can quietly stop matching the file a funder was sent. Putting it on
    the page is the point: the arithmetic should be readable before anyone
    unzips anything, and reproducible from what is on screen.
    """

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        selection = select_above(
            indicator=indicator,
            threshold=_float(request, "threshold", measures.get(indicator).threshold_default),
            year=int(y) if (y := request.GET.get("year")) and y.isdigit() else None,
            iso_codes=ISO_CODES,
            method=_method(request, indicator),
        )
        source = export.to_methodology(selection)
        return JsonResponse(
            {
                "markdown": source,
                "html": markdown.markdown(source, extensions=["tables", "fenced_code"]),
            }
        )


class MethodsView(OpenLocallyMixin, View):
    """What methods exist, and how much of the continent each can answer for."""

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        return JsonResponse(
            {
                "resolutions": availability.resolutions(),
                "default": DEFAULT_METHOD,
                "indicators": [
                    {
                        "code": m.code,
                        "label": m.label,
                        "unit": m.unit,
                        "description": m.description,
                        "lower_is_worse": m.code in measures.LOWER_IS_WORSE,
                        "per_1000": "1,000" in m.unit,
                        "threshold_min": m.threshold_min,
                        "threshold_max": m.threshold_max,
                        "threshold_default": m.threshold_default,
                    }
                    for m in measures.targetable()
                ],
                **availability.matrix(indicator),
            }
        )


class ScenarioView(OpenLocallyMixin, View):
    """What a unit price buys, over the places a threshold selects.

    The arithmetic is trivial once two things are fixed: a **unit cost** and a
    **unit of measure**. Which unit applies is a property of the intervention,
    not of the data — a bednet is priced per child, a water connection per
    household, a treatment per case — so the basis is chosen rather than
    inferred, and named interventions are presets for a basis and a price.
    """

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
                interventions.UnitBasis(basis_param)
                if basis_param
                else (intervention.basis if intervention else interventions.UnitBasis.PERSON)
            )
        except ValueError:
            return JsonResponse(
                {
                    "error": f"unknown basis {basis_param!r}",
                    "valid": [b.value for b in interventions.UnitBasis],
                },
                status=400,
            )

        indicator = request.GET.get("indicator") or (intervention.targets if intervention else DEFAULT_INDICATOR)
        if indicator not in measures.MEASURES:
            return JsonResponse({"error": f"unknown indicator {indicator!r}"}, status=400)

        threshold = _float(request, "threshold", measures.get(indicator).threshold_default)
        default_cost = intervention.unit_cost_usd if intervention else 1.0
        unit_cost = _float(request, "unit_cost", default_cost)

        cases_measure = interventions.measure_for(basis, indicator)
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

        selection = select_above(
            indicator=indicator,
            threshold=threshold,
            iso_codes=ISO_CODES,
            method=_method(request, indicator),
            extra_counts=(cases_measure,),
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
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        return JsonResponse(
            {
                "bases": [
                    {
                        "code": b.value,
                        "label": b.label,
                        "noun": b.noun,
                        "measure": interventions.measure_for(b, indicator),
                        "available_for_indicator": interventions.measure_for(b, indicator) is not None,
                    }
                    for b in interventions.UnitBasis
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


class CoverageView(OpenLocallyMixin, View):
    """What data we actually hold — the honest backdrop to any headline number."""

    def get(self, request):
        by_indicator = list(
            IndicatorValue.objects.values("indicator", "source")
            .annotate(rows=Count("id"), countries=Count("iso_code", distinct=True))
            .order_by("indicator", "source")
        )
        boundaries = list(
            boundary_set.owned()
            .filter(iso_code__in=ISO_CODES, admin_level__in=(0, 1))
            .values("admin_level")
            .annotate(n=Count("id"), countries=Count("iso_code", distinct=True))
            .order_by("admin_level")
        )
        missing = sorted(
            set(ISO_CODES)
            - set(
                boundary_set.owned()
                .filter(iso_code__in=ISO_CODES, admin_level=1)
                .values_list("iso_code", flat=True)
                .distinct()
            )
        )
        return JsonResponse(
            {
                "indicators": by_indicator,
                "boundaries": boundaries,
                "countries_missing_adm1": [{"iso": c, "name": name_for(c)} for c in missing],
                "runs": [
                    {
                        "source": r.source,
                        "indicator": r.indicator,
                        "rows": r.rows_written,
                        "countries": r.countries,
                        "ok": r.ok,
                        "at": r.started_at.isoformat(),
                    }
                    for r in IngestRun.objects.all()[:20]
                ],
            }
        )
