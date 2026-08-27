"""The targeting surface: a map, a threshold, and a downloadable answer."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import availability, export, measures, methods
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


def _round_or_none(value):
    """Round for display, but keep "no estimate" distinct from zero."""
    return None if value is None else round(value)


def _method(request) -> str:
    """The requested method, or the default for its resolution."""
    code = request.GET.get("method")
    if code and code in methods.METHODS:
        return code
    resolution = request.GET.get("resolution")
    if resolution in (r.value for r in methods.Resolution):
        return methods.default_for(methods.Resolution(resolution)).code
    return DEFAULT_METHOD


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
            AdminBoundary.objects.filter(iso_code__in=ISO_CODES, admin_level=1).values("iso_code").distinct().count()
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

        method = methods.get(_method(request))
        supported = set(availability.countries_supporting(method, indicator))

        # A national method paints one shape per country. Painting a national
        # figure onto regions would look like subnational detail that does not
        # exist. Countries the method cannot answer for are simply absent.
        level = 0 if method.is_national else 1
        units = list(AdminBoundary.objects.filter(iso_code__in=supported, admin_level=level))

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
            method=_method(request),
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
                "threshold_pct": threshold / 10.0,
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
            method=_method(request),
        )
        stem = export.filename_stem(selection)

        if fmt == "csv":
            resp = HttpResponse(export.to_csv(selection), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="{stem}.csv"'
            return resp

        resp = HttpResponse(export.to_zip(selection), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
        return resp


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


class CoverageView(OpenLocallyMixin, View):
    """What data we actually hold — the honest backdrop to any headline number."""

    def get(self, request):
        by_indicator = list(
            IndicatorValue.objects.values("indicator", "source")
            .annotate(rows=Count("id"), countries=Count("iso_code", distinct=True))
            .order_by("indicator", "source")
        )
        boundaries = list(
            AdminBoundary.objects.filter(iso_code__in=ISO_CODES, admin_level__in=(0, 1))
            .values("admin_level")
            .annotate(n=Count("id"), countries=Count("iso_code", distinct=True))
            .order_by("admin_level")
        )
        missing = sorted(
            set(ISO_CODES)
            - set(
                AdminBoundary.objects.filter(iso_code__in=ISO_CODES, admin_level=1)
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
