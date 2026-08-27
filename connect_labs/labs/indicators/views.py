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
from connect_labs.labs.indicators import export, measures
from connect_labs.labs.indicators.africa import ISO_CODES, name_for
from connect_labs.labs.indicators.models import IndicatorValue, IngestRun
from connect_labs.labs.indicators.resolve import BulkResolver, select_above

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 80.0
DEFAULT_INDICATOR = "u5mr"

#: Degrees of simplification for map geometry. ADM1 polygons carry tens of
#: thousands of vertices; at continent zoom the difference is invisible and the
#: payload is an order of magnitude smaller.
MAP_SIMPLIFY = 0.02


def _round_or_none(value):
    """Round for display, but keep "no estimate" distinct from zero."""
    return None if value is None else round(value)


def _float(request, key, default):
    try:
        return float(request.GET.get(key, default))
    except (TypeError, ValueError):
        return default


class TargetingView(LoginRequiredMixin, TemplateView):
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
            }
        )
        return ctx


class MapDataView(LoginRequiredMixin, View):
    """GeoJSON for the choropleth: one feature per ADM1 unit, carrying its numbers.

    Countries with no ADM1 boundaries fall back to their ADM0 outline so they
    appear on the map as a single unit rather than as a hole.
    """

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None
        simplify = _float(request, "simplify", MAP_SIMPLIFY)

        boundaries = list(AdminBoundary.objects.filter(iso_code__in=ISO_CODES, admin_level__in=(0, 1)))
        # Prefer ADM1; keep ADM0 only for countries that have no regions loaded.
        have_adm1 = {b.iso_code for b in boundaries if b.admin_level == 1}
        units = [b for b in boundaries if b.admin_level == 1 or b.iso_code not in have_adm1]

        bulk = BulkResolver(units, year=year)
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


class SelectionView(LoginRequiredMixin, View):
    """Apply a threshold and return the headline numbers plus the table."""

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        threshold = _float(request, "threshold", DEFAULT_THRESHOLD)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None

        # Scoped to Africa exactly as the map is: a table listing places the
        # map cannot show would be a quiet contradiction.
        selection = select_above(indicator=indicator, threshold=threshold, year=year, iso_codes=ISO_CODES)
        measure = measures.get(indicator)

        return JsonResponse(
            {
                "indicator": indicator,
                "indicator_label": measure.label,
                "indicator_unit": measure.unit,
                "threshold": threshold,
                "threshold_pct": threshold / 10.0,
                "totals": {
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
                        "source": (r.source_ref or r.source) if r else None,
                        "year": r.year if r else None,
                        "inherited": bool(r and r.inherited),
                        "measured_at": (
                            f"{r.measured_at.name} (ADM{r.measured_at.admin_level})" if r and r.inherited else None
                        ),
                        "births": _round_or_none(a.counts.get("births")),
                        "pop_u5": _round_or_none(a.counts.get("pop_u5")),
                        "pop_total": _round_or_none(a.counts.get("pop_total")),
                        "births_partial": not a.is_complete("births"),
                    }
                    for a in selection.areas
                ],
            }
        )


class SelectionDownloadView(LoginRequiredMixin, View):
    """The table and its methodology, zipped together."""

    def get(self, request):
        indicator = request.GET.get("indicator", DEFAULT_INDICATOR)
        threshold = _float(request, "threshold", DEFAULT_THRESHOLD)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None
        fmt = request.GET.get("format", "zip")

        selection = select_above(indicator=indicator, threshold=threshold, year=year, iso_codes=ISO_CODES)
        stem = export.filename_stem(selection)

        if fmt == "csv":
            resp = HttpResponse(export.to_csv(selection), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="{stem}.csv"'
            return resp

        resp = HttpResponse(export.to_zip(selection), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
        return resp


class CoverageView(LoginRequiredMixin, View):
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
