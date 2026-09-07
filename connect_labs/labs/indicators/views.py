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

#: How many countries may be drawn at district level at once. The limit is the
#: map's, not the analysis's: the table and the download will happily rank every
#: district in a dozen countries, but drawing 47,000 polygons is not a map.
MAX_COUNTRIES_AT_DISTRICT = 3


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
    # No resolution asked for still means "the subnational default", but which
    # method that is depends on the indicator. The flat constant is right for
    # the mortality measures this began as and answers no country at all for,
    # say, improved drinking water — so a link without ?method= landed on a
    # method with nothing behind it.
    return availability.default_method_for(indicator, res or methods.Resolution.SUBNATIONAL).code


def _float(request, key, default):
    try:
        return float(request.GET.get(key, default))
    except (TypeError, ValueError):
        return default


def _indicator(request, default: str = DEFAULT_INDICATOR) -> str:
    """The requested indicator, or the default if it names nothing.

    Every other query parameter already degrades: a misspelled ISO, an
    impossible admin level, a year of 1066 all fall back and the page still
    answers. The indicator did not — `?indicator=NOT_A_REAL_INDICATOR` reached
    `measures.get()` and raised, so one endpoint returned 500 while the others
    returned 200 for the same request. Falling back keeps the surface usable
    and consistent with everything beside it.
    """
    code = request.GET.get("indicator")
    if code and code in measures.MEASURES:
        return code
    return default


def _iso_codes(request) -> list[str]:
    """The countries in scope, defaulting to the whole continent.

    Scoping is what turns this from a continental scan into a country
    proposal. Unknown codes are dropped rather than passed through: a
    misspelled ISO would otherwise silently return an empty selection, which
    reads as "nowhere qualifies" rather than "that is not a country".
    """
    raw = request.GET.get("iso", "")
    wanted = [c.strip().upper() for c in raw.replace(" ", ",").split(",") if c.strip()]
    kept = [c for c in wanted if c in ISO_CODES]
    return kept or list(ISO_CODES)


def _admin_level(request) -> int | None:
    """The level to evaluate at, or None to take the deepest carrying values.

    Deepest is not always most informative. Liberia measures ORS coverage for
    its fifteen counties and no district, so all 136 districts inherit one of
    fifteen numbers -- a finer grid over the same information. Pinning the
    level is how a reader asks for the units that were actually measured.
    """
    v = request.GET.get("admin_level")
    if v and v.isdigit() and int(v) in boundary_set.LEVELS:
        return int(v)
    return None


def _rollup(request) -> bool:
    """Whether a wholly-selected country collapses into one row.

    On, a country every one of whose regions clears the threshold is stated as
    the country -- truer and shorter. Off, the regions are listed, which is
    what a ranking question wants: you cannot rank fifteen counties against
    each other if they have been added together first.
    """
    return request.GET.get("rollup", "1").lower() not in ("0", "false", "no", "off")


def _target_year(request) -> int | None:
    """The year the programme runs, if one was named.

    Counts are carried to it at each country's own growth rate; rates are left
    alone, because nothing here models how coverage or mortality moves and a
    projected rate would be invention.
    """
    v = request.GET.get("target_year")
    if v and v.isdigit() and 2000 <= int(v) <= 2050:
        return int(v)
    return None


def _selection_for(request):
    """The one selection every view on this page answers from.

    Built here rather than in each view because the map, the table, the
    download and the methodology have to be asking the same question -- four
    hand-assembled parameter lists is how they come to disagree, and how
    ``target_year`` came to reach the download but not the workings.
    """
    indicator = _indicator(request)
    measure = measures.get(indicator)
    return select_above(
        indicator=indicator,
        threshold=_float(request, "threshold", measure.threshold_default),
        year=int(y) if (y := request.GET.get("year")) and y.isdigit() else None,
        iso_codes=_iso_codes(request),
        method=_method(request, indicator),
        target_year=_target_year(request),
        rollup=_rollup(request),
        admin_level=_admin_level(request),
    )


class TargetingView(OpenLocallyMixin, TemplateView):
    """The map page."""

    template_name = "indicators/targeting.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        measure = measures.get(DEFAULT_INDICATOR)

        loaded = (
            boundary_set.owned().filter(iso_code__in=ISO_CODES, admin_level=1).values("iso_code").distinct().count()
        )

        ctx.update(
            {
                "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or "",
                "default_threshold": DEFAULT_THRESHOLD,
                "indicator": DEFAULT_INDICATOR,
                "indicator_label": measure.label,
                "indicator_unit": measure.unit,
                "countries_with_boundaries": loaded,
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
        indicator = _indicator(request)
        year = request.GET.get("year")
        year = int(year) if year and year.isdigit() else None
        simplify = _float(request, "simplify", MAP_SIMPLIFY)

        method = methods.get(_method(request, indicator))
        scope = _iso_codes(request)
        supported = set(availability.countries_supporting(method, indicator)) & set(scope)

        # A national method paints one shape per country. Painting a national
        # figure onto regions would look like subnational detail that does not
        # exist. Countries the method cannot answer for are simply absent.
        #
        # Below that, the map follows the level the table is pinned to, so the
        # shapes on screen are the rows in the table. Districts are only drawn
        # for a scoped selection: ADM2 across Africa is some 47,000 polygons,
        # which is not a map, it is a download.
        # `or 1` here would swallow a deliberate ADM0 pin, zero being falsy.
        pinned = _admin_level(request)
        level = 0 if method.is_national else (1 if pinned is None else pinned)
        if level == 2 and len(scope) > MAX_COUNTRIES_AT_DISTRICT:
            level = 1
        units = list(boundary_set.owned().filter(iso_code__in=supported, admin_level=level))

        bulk = BulkResolver(units, year=year, source_order=method.source_order, lens_on=indicator)
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
        indicator = _indicator(request)
        measure = measures.get(indicator)
        threshold = _float(request, "threshold", measure.threshold_default)
        scope = _iso_codes(request)
        # Resolved through the intervention layer rather than by name, so the
        # aliasing that makes an ORS question answerable from either the
        # prevalence or the coverage measure is stated once.
        annual_gap = interventions.measure_for(interventions.UnitBasis.CASE_YEAR, indicator)

        # Scoped, levelled and projected exactly as the map is, and produced
        # with the requested method, so the table and the map always agree.
        selection = _selection_for(request)

        return JsonResponse(
            {
                "indicator": indicator,
                "indicator_label": measure.label,
                "indicator_unit": measure.unit,
                "lower_is_worse": indicator in measures.LOWER_IS_WORSE,
                # The population the measure is about, named. For a coverage
                # measure this is its denominator; for a burden it is what the
                # rate is weighted by. Either way it is the count a reader
                # needs and the fixed under-5 tiles could not give them.
                "denominator": measure.weight_by,
                "denominator_label": (measures.get(measure.weight_by).label if measure.weight_by else None),
                "gap_label": (
                    measures.get(f"{indicator}_gap").label if f"{indicator}_gap" in measures.MEASURES else None
                ),
                # A survey measures a fortnight. The gap it implies is therefore
                # a fortnight's worth of cases, and quoting it as a year's is a
                # twentyfold error -- one this page made in print. Where the
                # annual sibling exists it is offered alongside, labelled, so
                # the reader picks a basis rather than assuming one.
                "gap_annual_label": (measures.get(annual_gap).label if annual_gap else None),
                "threshold": threshold,
                "threshold_pct": measures.percent_equivalent(indicator, threshold),
                "totals": {
                    "expected_deaths": selection.totals.get("expected_deaths"),
                    "ors_gap_children": selection.totals.get("ors_gap_children"),
                    "gap": selection.totals.get(f"{indicator}_gap"),
                    "gap_annual": selection.totals.get(annual_gap) if annual_gap else None,
                    "denominator": (selection.totals.get(measure.weight_by) if measure.weight_by else None),
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
                    "inherited_units": selection.inherited_units,
                    "small_sample_units": selection.small_sample_units,
                },
                # How much of the selection actually carries each count. Where
                # these fall short the total is a floor, and the UI says so
                # rather than presenting an undercount as a measurement.
                "coverage": {
                    c: {
                        "with_value": got,
                        "of": total,
                        # Named, so a shortfall can be reported as "unreached
                        # households" rather than as a measure code — or, as it
                        # was, not reported at all unless it happened to be
                        # births.
                        "label": measures.get(c).label if c in measures.MEASURES else c,
                    }
                    for c, (got, total) in selection.coverage.items()
                },
                "method": selection.method,
                "resolution": selection.resolution,
                # The shape the question was asked in, echoed back. A reader
                # who cannot see that a total is a 2027 projection over
                # fifteen unrolled counties cannot check it.
                "scope": {
                    "iso_codes": scope,
                    "countries": [name_for(c) for c in scope],
                    "whole_continent": len(scope) == len(ISO_CODES),
                },
                "projected_to": selection.projected_to,
                # Named, not coded. Every sibling list here is country names,
                # and "No growth series for LBR" reads like a system fault
                # where "for Liberia" reads like the fact it is.
                "projected_without_rate": [name_for(c) for c in selection.projected_without_rate],
                "rolled_up": selection.rolled_up,
                "pinned_level": selection.pinned_level,
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
                        "gap_annual": _round_or_none(a.counts.get(annual_gap)) if annual_gap else None,
                        "small_sample": bool(r and r.small_sample),
                        "sample": (r.extra.get("sample_unweighted") if r else None),
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
        fmt = request.GET.get("format", "zip")
        selection = _selection_for(request)
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
        selection = _selection_for(request)
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
        indicator = _indicator(request)
        return JsonResponse(
            {
                "resolutions": availability.resolutions(),
                # Indicator-aware: this field is what a client picks when the
                # reader has not chosen, so it has to be a method that can
                # answer the question being asked.
                "default": availability.default_method_for(indicator, methods.Resolution.SUBNATIONAL).code,
                # Order matters and belongs to the registry: the menu reads
                # groups in this order rather than sorting them alphabetically,
                # because "Child survival" leads for a reason.
                "groups": list(measures.GROUPS),
                "indicators": [
                    {
                        "code": m.code,
                        "label": m.label,
                        "unit": m.unit,
                        "description": m.description,
                        # The menu group, from the registry rather than the
                        # template — so the grouping is one fact with one home
                        # and the MCP tools can report it too.
                        "group": measures.group_of(m.code),
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


class ScopeView(OpenLocallyMixin, View):
    """Where you can ask the question, and at what level it is worth asking.

    Two things a reader cannot see from the map and has to be told:

    *Which countries there are.* Obvious, but the surface had no country
    control at all, so every question was continental whether or not that was
    the question.

    *Which levels are actually measured.* Boundary depth and measurement depth
    are different facts and the difference is the whole trap. Liberia has 136
    geoBoundaries districts and ORS coverage for none of them -- all 136 would
    inherit one of fifteen county figures and rank in fifteen flat ties, a
    finer grid over the same information presented as more detail. So each
    level reports how many of its units carry a reading **of their own**, and
    the control can say "15 measured" against "0 measured, 136 inherited"
    rather than leaving a reader to discover it in the caveat line.
    """

    def get(self, request):
        indicator = _indicator(request)
        method = methods.get(_method(request, indicator))
        supported = set(availability.countries_supporting(method, indicator))

        counts = (
            boundary_set.owned()
            .filter(iso_code__in=ISO_CODES)
            .values("iso_code", "admin_level")
            .annotate(n=Count("id"))
        )
        by_iso: dict[str, dict[int, int]] = {}
        for row in counts:
            by_iso.setdefault(row["iso_code"], {})[row["admin_level"]] = row["n"]

        payload = {
            "countries": [
                {
                    "iso": iso,
                    "name": name_for(iso),
                    "levels": {str(k): v for k, v in sorted(by_iso.get(iso, {}).items())},
                    "supported": iso in supported,
                }
                for iso in ISO_CODES
                if by_iso.get(iso)
            ],
            "max_countries_at_district": MAX_COUNTRIES_AT_DISTRICT,
        }

        # Measurement depth is per country and per indicator, so it is only
        # computed when a country was named -- resolving every district in
        # Africa to answer a control's label would be a minute of work for a
        # tooltip.
        scope = _iso_codes(request)
        if len(scope) <= MAX_COUNTRIES_AT_DISTRICT:
            units = list(boundary_set.owned().filter(iso_code__in=scope))
            bulk = BulkResolver(units, source_order=method.source_order, lens_on=indicator)
            depth: dict[int, dict[str, int]] = {}
            for b in units:
                d = depth.setdefault(b.admin_level, {"units": 0, "measured": 0, "inherited": 0})
                d["units"] += 1
                r = bulk.get(indicator, b)
                if r is None:
                    continue
                d["inherited" if r.inherited else "measured"] += 1
            payload["depth"] = {str(k): v for k, v in sorted(depth.items())}
            payload["depth_indicator"] = indicator
            payload["depth_scope"] = scope

        return JsonResponse(payload)


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

        indicator = _indicator(request, intervention.targets if intervention else DEFAULT_INDICATOR)
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
