"""Targeting tools — the intervention-targeting surface, for a chat session.

These mirror ``/labs/targeting/`` rather than reimplementing it: every number
comes from ``resolve.select_above`` and every explanation from
``export.to_methodology``, the same functions the page and the download use. A
second implementation would be free to drift from the one a funder was sent,
which is the failure this whole app is built to avoid.

The investigation these support is the one the page supports:

    which indicators can I target on, and what kind of question is each?
      -> where is it worse (or coverage lower) than some threshold?
        -> who lives there, and how many of them are we actually sure about?
          -> what would it cost to reach them?
            -> show me the workings so I can put it in a proposal.

Three things are deliberately returned even though they make the answers look
worse, because a model summarising these numbers cannot see the caveats a human
reads off the page:

  * ``coverage`` — how many selected units actually carry each count. Where it
    falls short the total is a floor, not a measurement.
  * ``inherited_units`` — units carrying a figure measured somewhere coarser,
    usually their country. A rate inherits downward legitimately, but a total
    built mostly that way is a national figure repeated, not a subnational one.
  * ``countries_unsupported`` — countries the method cannot answer at all,
    listed rather than silently dropped.
  * ``regional_proxy_units`` — per count, units whose figure rests on a
    regional aggregate rather than a national one (low-birthweight births in a
    country with no national LBW estimate). Part of the total, disclosed.
  * ``births_implausible_units`` — units whose births cannot belong to the
    under-fives beside them. Non-zero means the births total, and everything
    derived from it, should not be quoted until those units are explained.
  * ``countries_supported`` + ``empty_because_unanswerable`` — whether an empty
    answer is a finding. Zero rows because nowhere crossed the threshold and
    zero rows because nothing in scope had data are opposite conclusions, and
    a summariser cannot tell them apart from the totals alone.
"""

from __future__ import annotations

import logging

from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

#: Rows returned by default. The full table is what the download is for; a chat
#: needs enough to reason over and rank, not 300 rows of JSON.
DEFAULT_ROW_LIMIT = 25
MAX_ROW_LIMIT = 200


def _imports():
    """Import inside the handler so tool registration never drags in PostGIS."""
    from connect_labs.labs.indicators import availability, export, interventions, measures, methods
    from connect_labs.labs.indicators.africa import ISO_CODES
    from connect_labs.labs.indicators.resolve import select_above

    return availability, export, interventions, measures, methods, ISO_CODES, select_above


#: Mirrors ResearchNote.summary's max_length. A note's summary is the line a
#: reader sees before deciding whether to open it, so it is deliberately short.
_SUMMARY_MAX = 300


def _family(measures_mod, code: str) -> str:
    """Burden or coverage — the thing that decides which way the threshold reads."""
    return "coverage" if code in measures_mod.LOWER_IS_WORSE else "burden"


def _resolve_method(availability_mod, methods_mod, indicator: str, resolution: str, method: str | None) -> str:
    """Honour an explicit method; otherwise pick one that can answer this indicator.

    The registry default is per *resolution* and was chosen without knowing the
    indicator, so it picks IGME — which publishes mortality only. Asking for
    sanitation that way selects a method with data for 0 of 55 countries and
    returns an empty answer with nothing to explain it.
    """
    if method:
        if method not in methods_mod.METHODS:
            raise MCPToolError("BAD_REQUEST", f"Unknown method {method!r}. Call targeting_indicators to list them.")
        return method
    res = methods_mod.Resolution(resolution)
    return availability_mod.default_method_for(indicator, res).code


def _selection(
    indicator,
    threshold,
    resolution,
    method,
    iso_codes,
    extra_counts=(),
    target_year=None,
    rollup=True,
    admin_level=None,
):
    availability_mod, _, _, measures_mod, methods_mod, ISO_CODES, select_above = _imports()
    if indicator not in measures_mod.MEASURES:
        raise MCPToolError("BAD_REQUEST", f"Unknown indicator {indicator!r}. Call targeting_indicators to list them.")
    measure = measures_mod.get(indicator)
    if threshold is None:
        threshold = measure.threshold_default
    chosen = _resolve_method(availability_mod, methods_mod, indicator, resolution, method)
    selection = select_above(
        indicator=indicator,
        threshold=float(threshold),
        iso_codes=[c.upper() for c in iso_codes] if iso_codes else list(ISO_CODES),
        method=chosen,
        extra_counts=tuple(extra_counts),
        target_year=target_year,
        rollup=rollup,
        admin_level=admin_level,
    )
    return selection, measure, chosen


@register(
    name="targeting_indicators",
    description=(
        "List the indicators you can target on, and — crucially — what KIND of question "
        "each one is. A 'burden' indicator (under-5 mortality, stunting, malaria "
        "prevalence) is worse when HIGH, so a threshold selects places ABOVE it. A "
        "'coverage' indicator (sanitation, ORS, immunisation) is worse when LOW, so the "
        "threshold selects places BELOW it and the quantity worth funding is the "
        "unreached count, not the coverage rate. Also returns each indicator's own unit "
        "(per 1,000 vs percent — they are not interchangeable), its sensible threshold "
        "range, and which methods can actually answer it — IGME publishes mortality only, "
        "so it can answer almost none of them and the default method is chosen per "
        "indicator rather than fixed. Start here."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "Optional: detail for one indicator instead of the whole list.",
            }
        },
        "additionalProperties": False,
    },
)
def targeting_indicators(user, *, indicator=None):
    availability_mod, _, _, measures_mod, methods_mod, _, _ = _imports()

    codes = [indicator] if indicator else sorted(measures_mod.TARGETABLE)
    if indicator and indicator not in measures_mod.MEASURES:
        raise MCPToolError("BAD_REQUEST", f"Unknown indicator {indicator!r}")

    out = []
    for code in codes:
        m = measures_mod.get(code)
        can_answer = []
        for method in methods_mod.METHODS.values():
            n = sum(1 for r in availability_mod.for_method(method, code) if r.available)
            if n:
                can_answer.append({"method": method.code, "countries": n, "resolution": method.resolution.value})
        out.append(
            {
                "indicator": code,
                "label": m.label,
                "unit": m.unit,
                "family": _family(measures_mod, code),
                "selects": "below the threshold" if code in measures_mod.LOWER_IS_WORSE else "above the threshold",
                "threshold_min": m.threshold_min,
                "threshold_max": m.threshold_max,
                "threshold_default": m.threshold_default,
                # A percent threshold is already a percent. Only a per-1,000 rate has
                # a second reading, and assuming otherwise is how 50% rendered as 5.0%.
                "percent_equivalent_of_default": measures_mod.percent_equivalent(code, m.threshold_default),
                "methods_that_can_answer": sorted(can_answer, key=lambda d: -d["countries"]),
            }
        )
    return {"indicators": out, "count": len(out)}


@register(
    name="targeting_select",
    description=(
        "The core query: where does an indicator cross a threshold, and who lives there? "
        "Returns totals (population, under-5s, annual births, expected deaths, and the "
        "unreached count for a coverage indicator), how many areas/units/countries, and "
        "the top rows with each row's own source, year and method. "
        "Read the honesty fields before quoting any total: 'coverage' says how many "
        "selected units actually carry each count (a shortfall means the total is a "
        "FLOOR, not a measurement); 'inherited_units' says how many carry a figure "
        "measured somewhere coarser -- usually their country -- rather than in their "
        "own right, so a selection that is mostly inherited is a national figure "
        "repeated across regions; 'small_sample_units' says how many rest on a "
        "survey estimate the source itself flags as too thin to rely on (DHS "
        "suppresses below 25 unweighted cases and brackets below 50); and "
        "'countries_unsupported' lists countries the method cannot answer at all; "
        "'regional_proxy_units' says, per count, how many units rest on a REGIONAL "
        "aggregate rather than a national figure (e.g. births_lbw in Nigeria and "
        "Ethiopia, which publish no national low-birthweight estimate) -- quote such a "
        "total as including regional estimates; 'births_implausible_units' counts units "
        "whose births/under-5 ratio is outside 0.12-0.32 -- non-zero means do not quote "
        "the births total; and "
        "'empty_because_unanswerable' is true when a zero-row answer means the question "
        "could not be asked anywhere in scope rather than that nowhere crossed the "
        "threshold — do not report that case as a finding. Newborn indicators (u5mr, nmr, "
        "the maternal & newborn group) also carry births_lbw, the low-birthweight births "
        "a KMC-type programme is sized on."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {"type": "string", "description": "e.g. u5mr, improved_sanitation, stunting"},
            "threshold": {"type": "number", "description": "In the indicator's own unit. Defaults to its default."},
            "resolution": {"type": "string", "enum": ["national", "subnational"], "default": "subnational"},
            "method": {"type": "string", "description": "Optional. Omit to get one that can answer this indicator."},
            "iso_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional ISO-3 filter, e.g. ['NGA','ETH']. Default: all of Africa.",
            },
            "limit": {"type": "integer", "description": f"Rows to return (default {DEFAULT_ROW_LIMIT})."},
            # These three were implemented and callable in Python but absent from the
            # schema, and `additionalProperties: False` made them unreachable over MCP.
            # `target_year` was the loudest: targeting_scenario and
            # targeting_compare_criteria both describe theirs as "See targeting_select",
            # pointing at a parameter targeting_select did not offer.
            "target_year": {
                "type": "integer",
                "description": (
                    "Carry counts to a delivery year before selecting. Omit to use the " "source years as published."
                ),
            },
            "admin_level": {
                "type": "integer",
                "description": (
                    "Pin the level selected on: 1 for regions, 2 for districts. Omit to let "
                    "each row sit at the coarsest unit that is honestly describable. Targeting "
                    "selects on geoBoundaries, and not every country has an ADM2 there — one "
                    "that does not is DROPPED from a level-2 answer entirely (subnational spans "
                    "levels 1-2 with no national fallback). Read 'countries_missing_level' "
                    "before concluding anything from a small level-2 result: Nigeria and Kenya "
                    "land there, and targeting_admin_levels will still show them an ADM2 under "
                    "a different source."
                ),
            },
            "rollup": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Collapse a country whose every region clears the threshold into one "
                    "'whole country' row. Set false to always get individual regions."
                ),
            },
        },
        "required": ["indicator"],
        "additionalProperties": False,
    },
)
def targeting_select(
    user,
    *,
    indicator,
    threshold=None,
    resolution="subnational",
    method=None,
    iso_codes=None,
    limit=None,
    target_year=None,
    rollup=True,
    admin_level=None,
):
    _, _, _, measures_mod, _, _, _ = _imports()
    selection, measure, chosen = _selection(
        indicator,
        threshold,
        resolution,
        method,
        iso_codes,
        target_year=target_year,
        rollup=rollup,
        admin_level=admin_level,
    )
    limit = max(1, min(int(limit or DEFAULT_ROW_LIMIT), MAX_ROW_LIMIT))

    rows = []
    for area in selection.areas[:limit]:
        resolved = area.values.get(indicator)
        rows.append(
            {
                "area": area.name,
                "country": area.country_name,
                "level": f"ADM{area.admin_level}",
                "whole_country": area.is_whole_country,
                "units_covered": area.units_covered,
                "value": round(resolved.value, 1) if resolved else None,
                "source": resolved.source if resolved else None,
                "year": resolved.measured_year if resolved else None,
                "inherited": bool(resolved and resolved.inherited),
                # Per row as well as in the total: a reader scanning a table
                # needs to see which line is thin, not just how many are.
                "small_sample": bool(resolved and resolved.small_sample),
                "sample_unweighted": resolved.sample_unweighted if resolved else None,
                # Where the rate was measured, when not here -- a region, or a
                # regional aggregate for a country with no estimate of its own.
                "measured_at": resolved.measured_at_label if resolved and resolved.inherited else None,
                # Counts on this row that rest on a regional aggregate.
                "regional_proxy_counts": sorted(c for c, n in area.regional_proxy_units.items() if n),
                "births_implausible_units": area.births_implausible_units,
                **{k: (round(v) if v is not None else None) for k, v in area.counts.items()},
            }
        )

    return {
        "indicator": indicator,
        "label": measure.label,
        "unit": measure.unit,
        "family": _family(measures_mod, indicator),
        "threshold": selection.threshold,
        "percent_equivalent": measures_mod.percent_equivalent(indicator, selection.threshold),
        "method": chosen,
        "resolution": selection.resolution,
        "totals": {k: (round(v) if v is not None else None) for k, v in selection.totals.items()},
        "counts": {
            "areas": selection.area_count,
            "units": selection.unit_count,
            "countries": selection.country_count,
        },
        "coverage": {k: {"with_value": got, "of": total} for k, (got, total) in selection.coverage.items()},
        "inherited_units": selection.inherited_units,
        "small_sample_units": selection.small_sample_units,
        "regional_proxy_units": selection.regional_proxy_units,
        "births_implausible_units": selection.births_implausible_units,
        "projected_to": selection.projected_to,
        "projected_without_rate": selection.projected_without_rate,
        "countries_fully_above": selection.countries_fully_above,
        "countries_partly_above": selection.countries_partly_above,
        "countries_unsupported": selection.countries_unsupported,
        "countries_supported": selection.countries_supported,
        # Answerable countries dropped only because they have no boundary at the
        # pinned admin_level. Not "unsupported" (the method can answer them) and
        # not "no data" (there were no units to evaluate), so without this they
        # vanished from the answer leaving no trace anywhere.
        "countries_missing_level": selection.countries_missing_level,
        # Zero rows means one of two opposite things and the caller cannot tell
        # them apart from the numbers: nowhere met the threshold (a finding), or
        # nothing in scope could be asked (not a finding). A model summarising
        # "0 areas above 50% improved water in Liberia" will report it as good
        # news unless something says otherwise. This says otherwise.
        "empty_because_unanswerable": bool(not selection.area_count and not selection.countries_supported),
        "rows": rows,
        "rows_returned": len(rows),
        "rows_total": selection.area_count,
    }


@register(
    name="targeting_methodology",
    description=(
        "The workings behind a selection, as markdown: what the table is, how rows are "
        "rolled up, why rates are never summed, every source with its year and licence, "
        "the formula behind each derived column, and the caveats that apply. "
        "This is the same text the page shows and the download ships as METHODOLOGY.md "
        "— produced by the same function, so it cannot drift from the file someone was "
        "sent. Fetch it before putting any of these numbers in a proposal, and quote it "
        "rather than paraphrasing the arithmetic."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {"type": "string"},
            "threshold": {"type": "number"},
            "resolution": {"type": "string", "enum": ["national", "subnational"], "default": "subnational"},
            "method": {"type": "string"},
            "iso_codes": {"type": "array", "items": {"type": "string"}},
            "rollup": {
                "type": "boolean",
                "description": (
                    "Default true. Set false so the cross-checks run over the individual units — a "
                    "rolled-up country is one row, and a median computed across one area is not a check."
                ),
            },
            "admin_level": {"type": "integer", "description": "Pin the level: 1 regions, 2 districts."},
            "target_year": {"type": "integer", "description": "Carry counts to this year first."},
        },
        "required": ["indicator"],
        "additionalProperties": False,
    },
)
def targeting_methodology(
    user,
    *,
    indicator,
    threshold=None,
    resolution="subnational",
    method=None,
    iso_codes=None,
    rollup=True,
    admin_level=None,
    target_year=None,
):
    _, export_mod, _, _, _, _, _ = _imports()
    # The same controls the selection takes, because the cross-checks are
    # computed over the selection's rows. A rolled-up country is one row, and
    # "the two derivations differ by a median 39% across 1 areas" is not a
    # check — it is a coin toss reported as a finding.
    selection, _, chosen = _selection(
        indicator,
        threshold,
        resolution,
        method,
        iso_codes,
        rollup=rollup,
        admin_level=admin_level,
        target_year=target_year,
    )
    return {
        "indicator": indicator,
        "threshold": selection.threshold,
        "method": chosen,
        "markdown": export_mod.to_methodology(selection),
    }


@register(
    name="targeting_scenario",
    description=(
        "Cost a selection. Two things must be fixed and neither can be inferred from the "
        "data: what one unit costs, and what a unit IS — a birth, a child under 5, a "
        "person, a household, or a case of disease. Which applies is a property of the "
        "programme (KMC is priced per LOW-BIRTHWEIGHT newborn -- 'lbw_birth' -- a bednet per "
        "child, a water connection per household), so it is chosen, not guessed. Returns "
        "the absorbable spend, the unit count behind it, and any caveat -- including how "
        "many units rest on a regional rather than national rate ('regional_proxy_units'). "
        "Where an indicator implies no case count the 'case' basis is declined rather "
        "than approximated."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {"type": "string"},
            "threshold": {"type": "number"},
            "basis": {
                "type": "string",
                # 'case_year' was implemented and reachable in Python but missing from
                # this enum, so no MCP caller could select it. That is not a cosmetic
                # gap: 'case' is a survey recall window (a fortnight) and 'case_year' is
                # a year of the same episodes, and for ORS across Africa they are
                # 10.8M against 215.1M. Costing a year of supply on the fortnight figure
                # under-prices it roughly twentyfold, and the enum offered only the
                # fortnight.
                "enum": ["birth", "lbw_birth", "under_5", "person", "household", "case", "case_year"],
                "description": (
                    "What one unit of cost buys. 'lbw_birth' counts births under 2,500 g "
                    "(births x the UNICEF-WHO national low-birthweight rate) -- the KMC "
                    "denominator, roughly a seventh of 'birth'; it includes small-for-"
                    "gestational-age term babies and most preterm babies but not preterm "
                    "babies of 2,500 g or more. 'case' counts a survey recall window "
                    "(a fortnight); 'case_year' counts a year of the same episodes -- "
                    "pick deliberately, they differ by more than an order of magnitude."
                ),
            },
            "unit_cost": {"type": "number", "description": "USD per unit."},
            "resolution": {"type": "string", "enum": ["national", "subnational"], "default": "subnational"},
            "method": {"type": "string"},
            "iso_codes": {"type": "array", "items": {"type": "string"}},
            "reach": {
                "type": "number",
                "description": (
                    "Share of the units present that a campaign actually reaches, 0-1. Default 1.0, "
                    "which prices a programme nobody has run: independent post-round surveys of "
                    "door-to-door campaigns measure 70-92%. The response returns both 'units_present' "
                    "and the reached 'units' the cost is built on, so the assumption stays visible."
                ),
            },
            "target_year": {
                "type": "integer",
                "description": "Carry counts to the delivery year before costing. See targeting_select.",
            },
        },
        "required": ["indicator", "basis", "unit_cost"],
        "additionalProperties": False,
    },
)
def targeting_scenario(
    user,
    *,
    indicator,
    basis,
    unit_cost,
    threshold=None,
    resolution="subnational",
    method=None,
    iso_codes=None,
    reach=1.0,
    target_year=None,
):
    _, _, interventions_mod, _, _, _, _ = _imports()
    legal = [b.value for b in interventions_mod.UnitBasis]
    # The count fields every other targeting tool speaks -- rows and totals carry
    # 'pop_u5'/'births'/'pop_total', and targeting_compare_criteria's `count` takes
    # them verbatim -- while a basis is named for the unit rather than the column.
    # Reusing the field you just read off a selection is the obvious move and it used
    # to fail. Derived from measure_for() so it cannot drift from the real mapping.
    aliases = {}
    for b in interventions_mod.UnitBasis:
        if b in (interventions_mod.UnitBasis.DISEASE_CASE, interventions_mod.UnitBasis.CASE_YEAR):
            continue  # these resolve against the indicator, so they have no fixed column
        column = interventions_mod.measure_for(b)
        if column:
            aliases[column] = b.value

    requested = basis
    if basis in aliases:
        basis = aliases[basis]
    try:
        unit_basis = interventions_mod.UnitBasis(basis)
    except ValueError:
        raise MCPToolError(
            "BAD_REQUEST",
            f"Unknown basis {requested!r}. Choose one of: {', '.join(legal)}. "
            f"(Count-field names are accepted too: {', '.join(sorted(aliases))}.)",
        ) from None

    cases_measure = interventions_mod.measure_for(unit_basis, indicator)
    if cases_measure is None:
        alternatives = [b for b in legal if b not in ("case", "case_year")]
        raise MCPToolError(
            "BAD_REQUEST",
            f"A {basis!r} basis has no count for {indicator!r} — that indicator implies no "
            "case count, so pricing per case would be an approximation dressed as a figure. "
            f"Choose one of: {', '.join(alternatives)}.",
        )

    if not 0 < reach <= 1:
        raise MCPToolError("BAD_REQUEST", f"reach must be between 0 and 1, got {reach!r}")

    selection, measure, chosen = _selection(
        indicator,
        threshold,
        resolution,
        method,
        iso_codes,
        extra_counts=(cases_measure,),
        target_year=target_year,
    )
    present = selection.totals.get(cases_measure)
    # Present and reached are different numbers, and a programme is costed on
    # the second. Door-to-door campaigns measure 70-92% in post-round surveys;
    # costing at 100% prices a programme nobody has run.
    units = None if present is None else present * reach
    got, of = selection.coverage.get(cases_measure, (0, 0))
    proxied = selection.regional_proxy_units.get(cases_measure, 0)
    caveats = []
    if of and got < of:
        caveats.append(
            f"{of - got} of {of} selected units carry no {cases_measure} figure and contribute "
            "nothing, so this is a floor rather than a total."
        )
    if proxied:
        caveats.append(
            f"{proxied} of {of} selected units' {cases_measure} rest on a REGIONAL aggregate rate: "
            "their country publishes no national estimate, so its UN subregion's figure is applied. "
            "Included in the total, not a national measurement."
        )
    if selection.births_implausible_units:
        caveats.append(
            f"{selection.births_implausible_units} selected units carry a births figure outside the "
            "plausible range for their under-five population; any births-based count here is suspect "
            "until they are explained."
        )
    return {
        "indicator": indicator,
        "threshold": selection.threshold,
        "method": chosen,
        "basis": basis,
        "counts_measure": cases_measure,
        "units_present": round(present) if present is not None else None,
        "reach": reach,
        "projected_to": selection.projected_to,
        "units": round(units) if units is not None else None,
        "unit_cost": unit_cost,
        "absorbable_usd": round(units * unit_cost) if units is not None else None,
        "coverage": {"units_with_a_figure": got, "of_units_selected": of},
        "is_floor": bool(of and got < of),
        "regional_proxy_units": proxied,
        "births_implausible_units": selection.births_implausible_units,
        "caveat": " ".join(caveats) or None,
    }


@register(
    name="targeting_admin_levels",
    description=(
        "How deep the open boundary data goes for a country — which is what decides "
        "whether 'how many villages' is answerable at all. Most African countries stop at "
        "district or ward level; only a handful reach the village (Rwanda has 14,815 "
        "umudugudu at ADM5, Madagascar 17,465 fokontany at ADM4, Burundi 2,615 collines "
        "at ADM3). Reports what is loaded here and, optionally, what geoBoundaries "
        "publishes. Where no village layer exists, a village COUNT cannot be produced "
        "honestly from boundaries alone."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "iso_codes": {"type": "array", "items": {"type": "string"}, "description": "ISO-3 codes."},
        },
        "required": ["iso_codes"],
        "additionalProperties": False,
    },
)
def targeting_admin_levels(user, *, iso_codes):
    """Levels loaded per source, and which of them targeting can actually use."""
    from collections import defaultdict

    from connect_labs.labs.admin_boundaries.models import AdminBoundary
    from connect_labs.labs.indicators.boundaries import SOURCE as TARGETING_BOUNDARY_SOURCE

    wanted = [c.upper() for c in iso_codes]
    loaded: dict[str, dict] = defaultdict(dict)
    rows = (
        AdminBoundary.objects.filter(iso_code__in=wanted).values_list("iso_code", "source", "admin_level").order_by()
    )
    counts: dict[tuple, int] = defaultdict(int)
    for iso, source, level in rows:
        counts[(iso, source, level)] += 1
    for (iso, source, level), n in counts.items():
        loaded[iso].setdefault(source, {})[f"ADM{level}"] = n

    # `loaded` reports every source, and only geoBoundaries is selectable. That
    # distinction lived in the note's last sentence, and a reader who saw
    # "NGA geopode ADM2: 774" reasonably concluded districts were available for
    # Nigeria. They are not: pinning admin_level=2 drops Nigeria from the answer
    # entirely. So say it per country, in the payload, rather than asking the
    # reader to cross-reference a caveat against a dict.
    selectable = {}
    for iso in wanted:
        gb = (loaded.get(iso) or {}).get(TARGETING_BOUNDARY_SOURCE) or {}
        levels = sorted(int(k[3:]) for k in gb)
        elsewhere = sorted(
            {
                int(lvl[3:])
                for src, by_level in (loaded.get(iso) or {}).items()
                if src != TARGETING_BOUNDARY_SOURCE
                for lvl in by_level
            }
            - set(levels)
        )
        selectable[iso] = {"levels": levels, "levels_only_in_other_sources": elsewhere}

    return {
        "loaded": {iso: loaded.get(iso, {}) for iso in wanted},
        # The answer to "can I ask for districts here?" -- read this, not `loaded`.
        "selectable_by_targeting": selectable,
        "note": (
            "AdminBoundary is shared across labs apps and holds several sources — they are "
            "alternative tessellations of the same land, not a hierarchy. Never mix two "
            "sources inside one count or it double-counts. Targeting selects on "
            f"{TARGETING_BOUNDARY_SOURCE} ONLY, so read 'selectable_by_targeting' to decide "
            "what admin_level to ask for: a level listed under "
            "'levels_only_in_other_sources' exists in the database but targeting cannot "
            "select on it, and pinning it DROPS that country from the answer rather than "
            "falling back to a coarser row (it comes back in 'countries_missing_level')."
        ),
    }


@register(
    name="targeting_research",
    description=(
        "Read what has already been worked out about an indicator — which sources can "
        "answer it, which were rejected and why, what the traps are — so an investigation "
        "does not start from nothing. "
        "Every note is REVALIDATED as you read it: each of its claims is re-run against "
        "the live data and returned with a verdict, so you can see which sentences still "
        "describe reality. Read 'trust' first: 'holds' means every check passed, 'drifted' "
        "means at least one figure has moved and must be re-derived before you quote it, "
        "'unverified' means the note carries no checks and is a lead rather than a finding. "
        "Then read 'rescan_due': the checks confirm what we found, but only a fresh source "
        "scan can tell you whether something better has since been published — when it is "
        "due, ask the user whether to run one before relying on this for a deliverable. "
        "Call this BEFORE researching an indicator's data sources from scratch."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "Measure code, e.g. 'malaria_prevalence'. Omitted returns every note.",
            },
            "topic": {"type": "string", "description": "Narrow to one note by its slug."},
            "include_body": {
                "type": "boolean",
                "description": "Include the full reasoning. Default true; set false for an index.",
            },
        },
        "additionalProperties": False,
    },
)
def targeting_research(user, *, indicator=None, topic=None, include_body=True):
    from connect_labs.labs.indicators import research
    from connect_labs.labs.indicators.models import ResearchNote

    notes = research.for_indicator(indicator, topic=topic)
    described = [research.describe(n, include_body=include_body) for n in notes]

    if not described:
        return {
            "notes": [],
            "count": 0,
            "advice": (
                f"Nothing has been recorded about {indicator!r} yet. "
                if indicator
                else "No research notes exist yet. "
            )
            + "Investigate from first principles, then write what you learn back with "
            "targeting_research_write so the next session inherits it — including the "
            "sources you rejected and why, which is the half that never gets written down.",
        }

    drifted = [d for d in described if d["trust"] == "drifted"]
    unverified = [d for d in described if d["trust"] == "unverified"]
    due = [d for d in described if d["rescan_due"]]

    # Say what is unverified as loudly as what has drifted. A summary reading
    # "all checks hold" over a set of notes that mostly carry no checks is the
    # exact reassurance this whole mechanism exists to withhold.
    verdict = []
    if drifted:
        verdict.append(f"{len(drifted)} have drifted — re-derive anything they cover before quoting it.")
    if unverified:
        verdict.append(
            f"{len(unverified)} carry no checks and cannot be confirmed — treat those as leads, not findings."
        )
    if not drifted and not unverified:
        verdict.append("Every check holds.")

    return {
        "notes": described,
        "count": len(described),
        "scan_interval_days": ResearchNote.SCAN_INTERVAL_DAYS,
        "advice": (
            f"{len(described)} note(s). "
            + " ".join(verdict)
            + (
                f" {len(due)} are due a full source rescan; ask the user whether to run one."
                if due
                else " All have been scanned for new sources recently."
            )
        ),
    }


@register(
    name="targeting_research_write",
    description=(
        "Record what you worked out about an indicator, so the next session inherits it "
        "instead of repeating the work. Writes over an existing note with the same "
        "indicator and topic. "
        "A note is only as useful as its checks: supply claims narrow enough to re-run "
        "(a coverage count, a value for one country, whether a source still supplies the "
        "indicator, whether a measure still has the shape you assumed), and a future "
        "reader is told which of your sentences to stop believing rather than trusting all "
        "of them. A note with no checks is stored, but it is reported as unverified. "
        "Record the sources you REJECTED as well as the one you chose — the reasoning that "
        "rules an option out is what stops it being reconsidered from scratch every time. "
        "Set scanned_now only when you have actually swept the field for alternatives, not "
        "when you looked at one source."
    ),
    is_write=True,
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "Measure code this concerns; omit for research spanning indicators.",
            },
            "topic": {
                "type": "string",
                "description": "Short kebab-case slug naming the question this answers, e.g. 'which-source'.",
            },
            "summary": {"type": "string", "description": "The conclusion in one line."},
            "body": {
                "type": "string",
                "description": (
                    "The reasoning, in markdown: what you tried, what you found, what it means, "
                    "and what would change your mind."
                ),
            },
            "checks": {
                "type": "array",
                "description": (
                    "Claims to re-run on every read. Each is an object with 'kind' and its arguments: "
                    "{'kind':'coverage','indicator':CODE,'level':1,'expected':N} — N units carry it "
                    "(passes if coverage has since grown); "
                    "{'kind':'value','indicator':CODE,'iso':'NGA','level':0,'expected':X,'tolerance':0.05,"
                    "'source':OPTIONAL} — the figure you reasoned from; "
                    "{'kind':'source','indicator':CODE,'source':'map','expected':true} — this source still "
                    "supplies it; "
                    "{'kind':'measure','code':CODE,'expected':{'kind':'rate','family':'coverage'}} — the "
                    "measure still has the shape you assumed."
                ),
                "items": {"type": "object"},
            },
            "alternatives": {
                "type": "array",
                "description": (
                    "Sources considered, each {'name','url','licence','verdict','why'}. Verdict is "
                    "'adopted', 'rejected' or 'candidate'. Include the rejected ones."
                ),
                "items": {"type": "object"},
            },
            "scanned_now": {
                "type": "boolean",
                "description": "True only if you have just swept the field for alternative sources.",
            },
        },
        "required": ["topic", "summary", "body"],
        "additionalProperties": False,
    },
)
def targeting_research_write(
    user, *, topic, summary, body, indicator=None, checks=None, alternatives=None, scanned_now=False
):
    from django.utils import timezone

    from connect_labs.labs.indicators import measures, research
    from connect_labs.labs.indicators.models import ResearchNote

    if indicator:
        try:
            measures.get(indicator)
        except KeyError:
            raise MCPToolError(
                "BAD_REQUEST",
                f"Unknown indicator {indicator!r}. Call targeting_indicators for the list, or omit "
                "the field for research that spans indicators.",
            ) from None

    checks = list(checks or [])
    # Run them now rather than storing a claim that was never true. A check that
    # fails on the way in is a mistake in the note, not drift.
    results = [research.run_check(c) for c in checks]
    failing = [r for r in results if not r.holds]

    # The column is varchar(300). Without this the overflow surfaces as a raw
    # psycopg2 StringDataRightTruncation traceback that names neither the field
    # nor the limit, so the caller's only recourse is to guess which of the
    # several strings they just supplied was the long one.
    if len(summary) > _SUMMARY_MAX:
        raise MCPToolError(
            "BAD_REQUEST",
            f"summary is {len(summary)} characters; the limit is {_SUMMARY_MAX}. "
            "It is meant to be the conclusion in one line — the reasoning belongs in body.",
        )

    defaults = {
        "summary": summary,
        "body": body,
        "checks": checks,
        "alternatives": list(alternatives or []),
        "author": getattr(user, "username", "") or "",
    }
    if scanned_now:
        defaults["scanned_at"] = timezone.now()

    note, created = ResearchNote.objects.update_or_create(indicator=indicator or "", topic=topic, defaults=defaults)
    return {
        "saved": True,
        "created": created,
        "indicator": indicator,
        "topic": topic,
        "checks_run": [r.as_dict() for r in results],
        "warning": (
            None
            if not failing
            else (
                f"{len(failing)} of the checks you supplied do not pass against the data right now. "
                "They are stored, but a future reader will see this note as drifted from the moment it "
                "was written. Fix the expected values or drop those checks."
            )
        ),
        "advice": (
            "Stored with no checks — a future reader will be told this note is unverified. Add checks " "when you can."
            if not checks
            else f"Stored with {len(checks)} checks, re-run on every read."
        ),
    }


@register(
    name="targeting_compare_criteria",
    description=(
        "Run the SAME question through several selection criteria and show where they "
        "disagree. Targeting is usually presented as one answer, and the honest version is "
        "that several defensible screens give different answers: a county can be kept by a "
        "coverage screen, dropped by a prevalence screen and kept again by a mortality one. "
        "Pass two or more criteria (each an indicator and optional threshold) and this "
        "returns, per criterion, what it selects and what it costs in units; then per area, "
        "which criteria keep it — so the contested places are visible rather than hidden "
        "behind whichever screen was run first. "
        "Use this before defending a geographic selection, and put the disagreement in the "
        "write-up: a reviewer who finds it first will not believe the rest. "
        "Read 'empty_because_unanswerable' and 'unanswerable_screens' before quoting any "
        "agreement figure: a screen that could not be asked in the scope requested keeps "
        "nothing, which silently inflates how much the remaining screens appear to agree. "
        "When every screen is unanswerable the result is NOT a finding that no area "
        "qualifies. Also read 'countries_missing_level': targeting selects on geoBoundaries, "
        "so a country with no ADM2 there is dropped from a level-2 answer entirely rather "
        "than falling back to a coarser row."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "minItems": 2,
                "maxItems": 6,
                "description": (
                    "Screens to compare, each {'indicator': CODE, 'threshold': N (optional), "
                    "'label': TEXT (optional)}. Threshold defaults to the indicator's own."
                ),
                "items": {"type": "object"},
            },
            "iso_codes": {"type": "array", "items": {"type": "string"}, "description": "Optional ISO-3 filter."},
            "resolution": {"type": "string", "enum": ["national", "subnational"], "default": "subnational"},
            "target_year": {"type": "integer", "description": "Carry counts to this year first."},
            "count": {
                "type": "string",
                "description": "Count to compare on. Default pop_u5.",
            },
            "admin_level": {
                "type": "integer",
                "description": (
                    "Pin the level compared on: 1 for regions, 2 for districts. Targeting "
                    "selects on geoBoundaries; a country with no ADM2 there is dropped from a "
                    "level-2 answer rather than downgraded, so check 'countries_missing_level' "
                    "on each screen before reading a small result as a finding."
                ),
            },
        },
        "required": ["criteria"],
        "additionalProperties": False,
    },
)
def targeting_compare_criteria(
    user,
    *,
    criteria,
    iso_codes=None,
    resolution="subnational",
    target_year=None,
    count=None,
    admin_level=None,
):
    _, _, _, measures_mod, _, _, _ = _imports()

    count = count or "pop_u5"
    if count not in measures_mod.MEASURES:
        raise MCPToolError("BAD_REQUEST", f"Unknown count {count!r}.")
    # Enforced here as well as in the schema: one screen has nothing to
    # disagree with, and returning it as though it were a comparison would say
    # "no disagreement" about a question that was never asked.
    if len(criteria) < 2:
        raise MCPToolError(
            "BAD_REQUEST",
            "Comparing criteria needs at least two. For a single screen use targeting_select.",
        )

    screens = []
    for i, spec in enumerate(criteria):
        indicator = spec.get("indicator")
        if not indicator:
            raise MCPToolError("BAD_REQUEST", f"criteria[{i}] has no indicator.")
        selection, measure, chosen = _selection(
            indicator,
            spec.get("threshold"),
            resolution,
            spec.get("method"),
            iso_codes,
            extra_counts=(count,),
            target_year=target_year,
            admin_level=admin_level,
        )
        # A coverage screen selects below its threshold and a burden screen
        # above it, so the label has to say which or two screens read the same.
        direction = "below" if indicator in measures_mod.LOWER_IS_WORSE else "above"
        kept = {a.name: a for a in selection.areas}
        screens.append(
            {
                "label": spec.get("label") or f"{indicator} {direction} {selection.threshold:g}",
                "indicator": indicator,
                "threshold": selection.threshold,
                "method": chosen,
                "areas": len(selection.areas),
                "units": sum(a.units_covered for a in selection.areas),
                count: round(selection.totals.get(count) or 0),
                "small_sample_units": selection.small_sample_units,
                # Same honesty flag targeting_select carries, per screen. Without it a
                # comparison run at a level or in a scope the indicator cannot answer
                # returns zeros that read as a finding -- "nowhere is kept by every
                # screen" -- when the truth is that the question was never asked. The
                # The common way in is a scope the method cannot answer at all. A
                # pinned admin_level a country lacks is reported separately, in
                # countries_missing_level -- that country is answerable, it just has
                # no boundary at the level asked for.
                "empty_because_unanswerable": bool(not selection.area_count and not selection.countries_supported),
                "countries_unsupported": selection.countries_unsupported,
                "countries_missing_level": selection.countries_missing_level,
                "_kept": kept,
            }
        )

    everywhere = sorted({name for s in screens for name in s["_kept"]})
    rows = []
    for name in everywhere:
        keeps = [s["label"] for s in screens if name in s["_kept"]]
        area = next(s["_kept"][name] for s in screens if name in s["_kept"])
        rows.append(
            {
                "area": name,
                "country": area.country_name,
                count: round(area.counts.get(count) or 0),
                "kept_by": keeps,
                "kept_by_count": len(keeps),
                "contested": 0 < len(keeps) < len(screens),
            }
        )
    rows.sort(key=lambda r: (-r["kept_by_count"], -r[count]))

    unanimous = [r for r in rows if r["kept_by_count"] == len(screens)]
    contested = [r for r in rows if r["contested"]]
    for s in screens:
        del s["_kept"]

    unanswerable = [s["label"] for s in screens if s["empty_because_unanswerable"]]
    share = round(100 * sum(r[count] for r in contested) / max(sum(r[count] for r in rows), 1))

    # A comparison is only as answerable as its screens. Reporting "0 areas, 0%"
    # for a question that could not be asked is the one output here a reader will
    # quote as a finding, so it never gets phrased as one.
    if unanswerable and not rows:
        advice = (
            "This comparison could not be run: "
            + ", ".join(repr(lbl) for lbl in unanswerable)
            + " cannot be answered anywhere in the scope requested, so there is nothing to "
            "compare. This is NOT a finding that no area qualifies. The usual cause is a "
            "country filter the method cannot answer; if you pinned an admin_level, check "
            "'countries_missing_level' as well -- targeting selects on geoBoundaries and a "
            "country with no ADM2 there is dropped from a level-2 answer outright. Widen the "
            "scope, or call targeting_indicators to see what can be answered."
        )
    elif unanswerable:
        advice = (
            f"{len(unanimous)} areas are selected by every ANSWERABLE screen and {len(contested)} by "
            f"some but not all, holding {share}% of the {count.replace('_', ' ')} in play. "
            "WARNING: " + ", ".join(repr(lbl) for lbl in unanswerable) + " could not be answered in "
            "this scope and contributed nothing -- an area it might have kept cannot show as "
            "contested, so the agreement below is overstated. Fix the scope before quoting these "
            "numbers, or drop the screen and say you did."
        )
    else:
        advice = (
            f"{len(unanimous)} areas are selected by every screen and {len(contested)} by some but not all, "
            f"holding {share}% "
            f"of the {count.replace('_', ' ')} in play. The unanimous set is what a selection can be defended "
            "on without argument; the contested set is what a reviewer will ask about, so name it and say "
            "which screen you chose and why."
        )

    return {
        "screens": screens,
        "count": count,
        "areas": rows,
        "unanimous": len(unanimous),
        "contested": len(contested),
        "contested_share_of_count": (
            round(100 * sum(r[count] for r in contested) / max(sum(r[count] for r in rows), 1), 1)
        ),
        "unanswerable_screens": unanswerable,
        "empty_because_unanswerable": bool(unanswerable and not rows),
        "advice": advice,
    }


def _area_rate(indicator: str, iso_code: str, area: str, admin_level: int, method: str | None = None):
    """One area's value for an indicator, read through the same selection the map uses.

    Selecting with a threshold every area clears keeps this on ``select_above`` --
    the function behind the page, the download and ``targeting_select`` -- so the
    figure a cost-effectiveness answer is built on is the figure the map shows for
    that place, method and all. Returns ``(Resolved | None, method_code)``.
    """
    from connect_labs.labs.indicators import measures

    everything = 1e12 if indicator in measures.LOWER_IS_WORSE else -1e12
    selection, _, chosen = _selection(
        indicator, everything, "subnational", method, [iso_code], rollup=False, admin_level=admin_level
    )
    wanted = area.strip().lower()
    for row in selection.areas:
        if row.name.strip().lower() == wanted:
            return row.values.get(indicator), chosen
    return None, chosen


def _provenance(resolved, method_code: str) -> dict:
    return {
        "value": round(resolved.value, 3),
        "source": resolved.source_ref or resolved.source,
        "year": resolved.measured_year,
        "method": method_code,
        "provenance": resolved.provenance,
        "inherited": resolved.inherited,
        "sample_unweighted": resolved.sample_unweighted,
        "small_sample": resolved.small_sample,
    }


@register(
    name="targeting_cost_effectiveness",
    description=(
        "What a round of spend BUYS in one area, not just what it costs: under-5 deaths averted, "
        "cost per death averted and the multiple of GiveWell's benchmark (bar 6x), for door-to-door "
        "ORS. The chain is GiveWell's ORS/zinc CEA (Aug 2023, as applied to CHAI Bauchi), reproduced "
        "-- every fixed parameter is returned with its source. Two inputs make the answer the area's "
        "own and both come from the registry with provenance: baseline ORS coverage (the "
        "'ors_coverage' value the map shows) and direct diarrhoea mortality, DERIVED from GiveWell's "
        "Bauchi figure scaled by the area's under-5 mortality over Bauchi's (an assumption, stated "
        "as one; override either with a better figure). Returns the result, the inputs with where "
        "each came from, a price x coverage sensitivity grid, the break-even price for the bar, and "
        "the benefits NOT counted (zinc, chlorine, hygiene, transmission) -- so every figure is a "
        "floor on the ORS benefit alone. Read 'caveats' before quoting: a small-sample coverage "
        "figure or an inherited mortality rate is named there. This is a modelled estimate, not "
        "measured impact."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "iso_code": {"type": "string", "description": "ISO-3, e.g. 'NGA'."},
            "area": {"type": "string", "description": "Region name as targeting_select returns it, e.g. 'Borno'."},
            "spend": {"type": "number", "description": "Total USD for the round, all-in."},
            "unit_cost": {"type": "number", "description": "USD per verified household visit, all-in."},
            "children_per_household": {
                "type": "number",
                "default": 1.2,
                "description": (
                    "Under-5s per household visited. Default 1.2; use 1.0 for a conservative case. "
                    "A Connect visit can record the real count, which beats any default."
                ),
            },
            "baseline_ors_coverage": {
                "type": "number",
                "description": (
                    "Override the registry's ORS coverage (share of episodes treated, 0-1). Use when "
                    "a fresher local figure exists -- e.g. outbreak-era humanitarian distribution."
                ),
            },
            "diarrhoea_mortality": {
                "type": "number",
                "description": "Override direct diarrhoea deaths per 1,000 under-5s per year.",
            },
            "admin_level": {"type": "integer", "default": 1, "description": "1 regions, 2 districts."},
            "intervention": {"type": "string", "enum": ["ors"], "default": "ors"},
        },
        "required": ["iso_code", "area", "spend", "unit_cost"],
        "additionalProperties": False,
    },
)
def targeting_cost_effectiveness(
    user,
    *,
    iso_code,
    area,
    spend,
    unit_cost,
    children_per_household=1.2,
    baseline_ors_coverage=None,
    diarrhoea_mortality=None,
    admin_level=1,
    intervention="ors",
):
    from connect_labs.labs.indicators import cost_effectiveness as ce

    if intervention not in ce.INTERVENTIONS:
        raise MCPToolError(
            "BAD_REQUEST", f"Unknown intervention {intervention!r}. Supported: {', '.join(ce.INTERVENTIONS)}."
        )
    for name, value in (
        ("spend", spend),
        ("unit_cost", unit_cost),
        ("children_per_household", children_per_household),
    ):
        if value is None or value <= 0:
            raise MCPToolError("BAD_REQUEST", f"{name} must be a positive number, got {value!r}")
    iso = iso_code.upper()
    caveats: list[str] = []

    # Baseline ORS coverage: the registry's, unless the caller knows better.
    coverage_in: dict
    if baseline_ors_coverage is not None:
        if not 0 <= baseline_ors_coverage < 1:
            raise MCPToolError("BAD_REQUEST", "baseline_ors_coverage is a share between 0 and 1 (e.g. 0.61).")
        coverage = float(baseline_ors_coverage)
        coverage_in = {"value": coverage, "source": "caller override", "overridden": True}
    else:
        resolved, method_code = _area_rate("ors_coverage", iso, area, admin_level)
        if resolved is None:
            raise MCPToolError(
                "BAD_REQUEST",
                f"No ORS coverage for {area!r} in {iso} at admin level {admin_level}. Check the name with "
                "targeting_select (indicator 'ors_coverage', the same iso_code), or pass "
                "baseline_ors_coverage explicitly.",
            )
        coverage = resolved.value / 100.0
        coverage_in = {**_provenance(resolved, method_code), "share": round(coverage, 4), "overridden": False}
        if resolved.small_sample:
            caveats.append(
                f"ORS coverage for {area} rests on {resolved.sample_unweighted} unweighted cases, which the "
                "source flags as too thin to rely on. Quote the sensitivity row, not the point estimate."
            )
        if resolved.inherited:
            caveats.append(f"ORS coverage for {area} was measured at {resolved.measured_at_label}, not in {area}.")
    if coverage >= 1:
        raise MCPToolError("BAD_REQUEST", "Baseline ORS coverage of 100% leaves nothing for a campaign to add.")

    # Direct diarrhoea mortality: derived from Bauchi unless overridden.
    mortality_in: dict
    if diarrhoea_mortality is not None:
        if diarrhoea_mortality <= 0:
            raise MCPToolError("BAD_REQUEST", "diarrhoea_mortality must be positive.")
        mortality = float(diarrhoea_mortality)
        mortality_in = {"value": mortality, "source": "caller override", "overridden": True}
    else:
        area_u5, u5_method = _area_rate("u5mr", iso, area, admin_level)
        anchor_u5, _ = _area_rate("u5mr", ce.ANCHOR_ISO, ce.ANCHOR_AREA, 1, method=u5_method)
        if area_u5 is None or anchor_u5 is None:
            missing = area if area_u5 is None else f"{ce.ANCHOR_AREA} (the anchor)"
            raise MCPToolError(
                "BAD_REQUEST",
                f"No under-5 mortality for {missing} to derive diarrhoea mortality from. Pass "
                "diarrhoea_mortality explicitly (direct diarrhoea deaths per 1,000 under-5s per year).",
            )
        mortality = ce.derived_diarrhoea_mortality(area_u5.value, anchor_u5.value)
        mortality_in = {
            "value": round(mortality, 3),
            "overridden": False,
            "derivation": (
                f"{ce.ANCHOR_DIARRHOEA_MORTALITY.value} (GiveWell, Bauchi) x {area} U5MR "
                f"{area_u5.value:g} / Bauchi U5MR {anchor_u5.value:g}"
            ),
            "assumption": "Diarrhoea's share of under-5 deaths is held at Bauchi's.",
            "area_u5mr": _provenance(area_u5, u5_method),
            "anchor_u5mr": _provenance(anchor_u5, u5_method),
        }
        if area_u5.inherited:
            caveats.append(f"Under-5 mortality for {area} was measured at {area_u5.measured_at_label}.")
        caveats.append(
            "Diarrhoea mortality is derived, not measured: it assumes diarrhoea's share of under-5 deaths "
            "matches Bauchi's. Survey diarrhoea prevalence can disagree with that ratio; pass "
            "diarrhoea_mortality to test another figure."
        )

    try:
        result = ce.ors_chain(
            spend=spend,
            unit_cost=unit_cost,
            children_per_household=children_per_household,
            baseline_coverage=coverage,
            diarrhoea_mortality=mortality,
        )
    except ValueError as exc:
        raise MCPToolError("BAD_REQUEST", str(exc)) from None

    caveats.append(
        "One round is credited with a full year of ORS benefit, as in GiveWell's model; the uptake gain is "
        "Wagner et al.'s trial effect after GiveWell's validity discounts, not a measured Connect effect."
    )
    return {
        "intervention": intervention,
        "iso_code": iso,
        "area": area,
        "admin_level": admin_level,
        "inputs": {
            "spend": spend,
            "unit_cost": unit_cost,
            "households": round(result.households),
            "children_per_household": children_per_household,
            "baseline_ors_coverage": coverage_in,
            "diarrhoea_mortality": mortality_in,
        },
        "result": result.as_dict(),
        "sensitivity": ce.sensitivity(
            spend=spend,
            children_per_household=children_per_household,
            diarrhoea_mortality=mortality,
            baseline_coverage=coverage,
        ),
        "parameters": ce.parameters(),
        "not_counted": list(ce.NOT_COUNTED),
        "caveats": caveats,
        "basis": "GiveWell ORS/zinc CEA (Aug 2023) chain as applied to CHAI Bauchi; modelled, not measured.",
    }


@register(
    name="targeting_pmc_schedules",
    description=(
        "Perennial malaria chemoprevention (PMC) in Nigeria: WHICH delivery schedule, and WHERE. "
        "To RANK states and designs together ('most cost-effective state and design', 'which design where', "
        "'rank the top 10'), call targeting_pmc_rank instead -- it uses each state's own fitted setting. "
        "Returns IDM's "
        "EMOD model comparison of six PMC schedules (no PMC; SP at vaccine visits at 25% coverage; Connect "
        "quarterly, every two months, or monthly through the six-month high season for children 3-24 months; "
        "Connect quarterly for 12-24 months only) -- cases averted in children 3-24 months with a +/- range "
        "across six seeds, doses, and the cost per case averted recomputed at the given visit price -- plus "
        "Nigeria's states with live DHS malaria prevalence, rainfall seasonality, zero-dose and DPT3, the "
        "estimated children 3-24 months, whether each state is near the modelled setting in BOTH "
        "prevalence and rainfall seasonality (fit: near / prevalence_differs / more_seasonal / less_seasonal), "
        "and a "
        "one-year projection for the chosen schedule. The model setting is ONE uncalibrated southern-Nigeria-"
        "like setting, precomputed (EMOD is not run by this call): read 'caveats' and quote results as "
        "illustrative, never as a state's calibrated estimate. Use it to propose states and a schedule for a "
        "PMC proposal, and to show how the answer moves with the price per visit. "
        "States come back RANKED (rank 1 = lowest cost per case averted for the chosen schedule): each "
        "schedule's EMOD effect applied to the state's own MAP malaria incidence; 'confidence' is 'model fit' or "
        "'lower' (prevalence differs). Unranked states are more or less seasonal than the model -- say why, do not "
        "rank them. To answer 'which approach and where', compare schedules (cost_per_case_averted) and then rank "
        "states under the best one. "
        "FROM A TARGETING SELECTION (the targeting page's state carries filters.selected_areas, e.g. "
        "'Kano (NGA), Ondo (NGA)', plus the question that produced them): take the NGA names, pass them as "
        "'states', and give the visitor 'explorer_path' -- the PMC schedule explorer opened on exactly those "
        "states. If selected_areas is absent, call targeting_select with the page's filters to get the areas. "
        "ANSWER BRIEFLY -- it is read in a narrow (~400px) side panel: the best approach in one sentence that "
        "also names the runner-up (no schedule table); the ranked states as a table of AT MOST three columns "
        "(state, $ per case, fit); one line naming the unranked states and why; one line of caveats; then "
        "explorer_path as a short markdown link, e.g. [Open these states in the PMC explorer](...). Use the "
        "page's own fit labels so the panel and the page agree: 'matches' (fit=near), 'prevalence differs', "
        "'more seasonal: SMC, not PMC'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "schedule": {
                "type": "string",
                "description": "Schedule code to project per state. Default: the cheapest per case averted.",
            },
            "states": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only these states (names as returned, case-insensitive). Default: all 37.",
            },
            "cost_per_visit": {"type": "number", "description": "USD per verified visit. Default 0.80."},
            "platform_fee": {"type": "number", "description": "Share added on top, e.g. 0.2 for 20% (the default)."},
            "dose_rate": {"type": "number", "description": "Share of visits that give a dose, 0-1. Default 0.95."},
        },
        "additionalProperties": False,
    },
)
def targeting_pmc_schedules(
    user, *, schedule=None, states=None, cost_per_visit=None, platform_fee=None, dose_rate=None
):
    from connect_labs.labs.indicators import pmc

    try:
        costs = pmc.costs_or_default(cost_per_visit, platform_fee, dose_rate)
        out = pmc.summary(costs)
    except ValueError as e:
        raise MCPToolError("BAD_REQUEST", str(e)) from None
    codes = [s["code"] for s in out["schedules"]]
    chosen = schedule or out["best_schedule"]
    if chosen not in codes:
        raise MCPToolError("BAD_REQUEST", f"Unknown schedule {chosen!r}. Known: {', '.join(codes)}.")
    rows = pmc.state_rows(chosen, costs)
    if states:
        wanted = {s.strip().lower() for s in states}
        found = {r["name"].lower() for r in rows}
        missing = sorted(wanted - found)
        rows = [r for r in rows if r["name"].lower() in wanted]
        if missing:
            out["states_not_found"] = missing
    out["schedule"] = chosen
    out["states"] = rows
    # The explorer opened on this answer: the same states, schedule and price.
    from urllib.parse import urlencode

    from django.urls import reverse

    # Only what differs from the page's own defaults, so the link stays short.
    q = {} if chosen == out["best_schedule"] else {"schedule": chosen}
    q |= {k: v for k, v in costs.items() if v != pmc.costs_or_default()[k]}
    if states:
        q["states"] = ",".join(r["name"] for r in rows)
    out["explorer_path"] = f"{reverse('targeting:pmc')}?{urlencode(q)}"
    return out


# ---- Live EMOD runs (PMC) ----------------------------------------------------------------------------------

LIVE_LABEL = "illustrative \u00b7 live model run"
GRID_LABEL = "illustrative \u00b7 precomputed model run"

_PMC_COST_PROPS = {
    "cost_per_visit": {"type": "number", "description": "USD per verified visit. Default 0.80."},
    "platform_fee": {"type": "number", "description": "Share added on top, e.g. 0.2 for 20% (the default)."},
    "dose_rate": {"type": "number", "description": "Share of visits that give a dose, 0-1. Default 0.95."},
}

_PMC_FALLBACK = " Use the precomputed results from targeting_pmc_schedules instead."

_PMC_BUSY = "The live model is busy with other runs; try again in a few minutes, or use targeting_pmc_schedules."


def page_error(tool_error: str) -> str:
    """A PMC tool's failed/busy error without the agent-only fallback guidance, for the page."""
    from connect_labs.labs.indicators.emod import service

    if tool_error == _PMC_BUSY:
        return service.PUBLIC_BUSY
    return tool_error.removesuffix(_PMC_FALLBACK)


def _pmc_unavailable(internal_error: str | None) -> str:
    """The public sentence for a failed run. The internal error (SDK and SSM messages can name the
    instance) stays on the row and in the logs; it never reaches the agent or the page."""
    from connect_labs.labs.indicators.emod import service

    return service.public_error(internal_error) + _PMC_FALLBACK


def _pmc_costs(cost_per_visit, platform_fee, dose_rate) -> dict:
    from connect_labs.labs.indicators import pmc

    try:
        costs = pmc.costs_or_default(cost_per_visit, platform_fee, dose_rate)
        pmc.cost_per_dose(**costs)
    except ValueError as e:
        raise MCPToolError("BAD_REQUEST", str(e)) from None
    return costs


def _pmc_state_fit_error(state: str) -> str | None:
    """The reason the model cannot speak for this state, or None when it can. ValueError for unknown states."""
    from connect_labs.labs.indicators import pmc
    from connect_labs.labs.indicators.emod import runner

    if runner.fitted_setting(state) is not None:  # the state's own fitted model speaks for it, seasonal or not
        return None
    fit = runner.state_fit(state)
    if fit in pmc.RANKED_FITS:
        return None
    return f"{state} cannot be modelled: {runner.FIT_REASONS.get(fit, fit)}"


def _pmc_present(
    state: str,
    row: dict,
    costs: dict,
    *,
    live: bool,
    description: str,
    code: str,
    run_id=None,
    fitted_to: str | None = None,
) -> dict:
    """A schedule's EMOD effect costed at one state's MAP incidence, beside the grid's best schedule.

    ``row`` has the relative effect (``averted_pct`` ...) and ``doses_per_child_per_year``. The costing
    is the explorer's own (``pmc.state_rows``), so a live result and a grid result are comparable.
    The registry is read once: the grid-best projection reuses the state row of the first call.
    """
    from connect_labs.labs.indicators import pmc

    summary = pmc.summary(costs)
    best_code = summary["best_schedule"]
    grid_rows = {r["code"]: r for r in summary["schedules"]}
    wanted = state.strip().lower()
    mine = next((r for r in pmc.state_rows(code, costs, schedule=row) if r["name"].lower() == wanted), None)
    if mine is None:
        raise MCPToolError("BAD_REQUEST", f"Unknown state {state!r}.")
    noisy = bool(row.get("too_noisy"))
    projection = None if noisy else mine["projection"]
    if projection is None and not noisy and fitted_to:
        # The explorer withholds a projection from a state unlike its one modelled setting; a run in the state's
        # OWN fitted setting has no such mismatch, so it is costed at the state's incidence like any other.
        projection = pmc.project(mine["children_3_24m"], row, pmc.cost_per_dose(**costs), mine["malaria_incidence"])

    best = None
    if best_code:
        g = grid_rows[best_code]
        children = (mine["pop_u5"] or 0) * pmc.SHARE_OF_U5_AGED_3_24M
        best = {
            "code": best_code,
            "label": g["label"],
            "averted_pct": g["averted_pct"],
            "averted_ci": g["averted_ci"],
            "projection": pmc.project(children, g, pmc.cost_per_dose(**costs), mine["malaria_incidence"]),
            # The two effects' ranges overlap: the schedules are not distinguishable at this precision.
            "difference_within_noise": abs(row["averted_pct"] - g["averted_pct"])
            <= row["averted_ci"] + g["averted_ci"],
        }
        if best_code == code:
            best["difference_within_noise"] = True
    from connect_labs.labs.indicators.emod import live as live_mod

    label = GRID_LABEL if not live else live_mod.fitted_label(fitted_to) if fitted_to else LIVE_LABEL
    out = {
        "label": label,
        "schedule": {"description": description},
        "effect": {
            "averted_pct": row["averted_pct"],
            "averted_ci": row["averted_ci"],
            "too_noisy": noisy,
            "doses_per_child_per_year": row["doses_per_child_per_year"],
            "basis": "EMOD relative effect against no PMC, across seeds",
            **{k: row[k] for k in ("averted_u5_pct", "averted_u5_ci", "kids_u5") if k in row},
        },
        "state": {
            "name": mine["name"],
            "fit": mine["fit"],
            "confidence": mine["confidence"],
            "children_3_24m": mine["children_3_24m"],
            "malaria_incidence_per_1000": mine["malaria_incidence"],
        },
        "projection": projection,
        "value": _pmc_value(mine["name"], row, projection),
        "projection_note": (
            "Per year, rounded to two figures: relative EMOD effect x the state's MAP incidence. Cases averted "
            "are a floor and cost per case a ceiling."
            if projection
            else "Not costed: the effect is smaller than its own uncertainty (or the state has no incidence or "
            "population figure)."
        ),
        "best_grid_schedule": best,
        "costs": costs,
        "caveats": list(pmc.CAVEATS),
    }
    if run_id is not None:
        out["run_id"] = run_id
    return out


def _pmc_value(state: str, row: dict, projection: dict | None) -> dict | None:
    """A costed result's under-5 deaths averted and multiple of GiveWell's benchmark, as targeting_pmc_rank values
    a grid design: EMOD's under-5 effect x the state's under-5 malaria deaths (the default basis). None when the run
    has no under-5 effect, it was not costed, or no mortality figures are loaded."""
    from connect_labs.labs.indicators.cost_effectiveness import BAR
    from connect_labs.labs.indicators.emod import mortality, rank

    if not projection or not projection.get("spend_per_year") or row.get("averted_u5_pct") is None:
        return None
    deaths = mortality.malaria_u5_deaths(mortality.registry_burden()).get(state)
    if not deaths:
        return None
    v = mortality.value(row["averted_u5_pct"] / 100 * deaths, projection["spend_per_year"])
    return {
        "deaths_averted_per_year": rank.sig(v["deaths_averted_per_year"]),
        "cost_per_death_averted": rank.sig(v["cost_per_death_averted"]),
        "multiple_of_benchmark": round(v["multiple_of_benchmark"], 1),
        "clears_bar": v["clears_bar"],
        "bar": BAR.value,
        "deaths_basis": mortality.DEFAULT_BASIS,
    }


def _pmc_grid_row(code: str, costs: dict) -> dict:
    from connect_labs.labs.indicators import pmc

    return next(r for r in pmc.schedule_rows(costs) if r["code"] == code)


_PMC_PRESENT_RULES = (
    "HOW TO PRESENT a completed result: the same MAP-anchored costing as targeting_pmc_schedules -- the "
    "schedule's relative EMOD effect applied to the state's own malaria incidence: cases averted per year "
    "(a floor) and cost per case averted (a ceiling), against best_grid_schedule, the grid's best schedule "
    "for the same state. If best_grid_schedule.difference_within_noise is true, say the two are not "
    "distinguishable at this precision -- do NOT say one is cheaper or dearer; otherwise say which is cheaper "
    "per case and by how much. Use the wording in result.label (precomputed run vs live run) as the label, "
    "and state the first caveat in result.caveats (a default-setting run is one simulated setting fitted to no real "
    "state; a run whose label says 'fitted to <State>' used that state's own fitted setting, which is still not "
    "a full "
    "calibration: every figure is illustrative, never a state's calibrated estimate). Keep it short (a narrow side "
    "panel): one sentence of result, a two-row comparison (this schedule vs the grid's best: cases averted "
    "per year, $ per case), one line of caveats. When result.value is present, lead with it instead, as "
    "targeting_pmc_rank does: under-5 deaths averted per year, $ per death averted and the multiple of GiveWell's "
    "benchmark against the bar (value.bar, 6x)."
)


@register(
    name="targeting_pmc_run_model",
    description=(
        "Live runs now use the state's own fitted model (its prevalence and rainfall) when it has one -- say so in "
        "the answer. Run IDM's EMOD malaria model LIVE for a perennial malaria chemoprevention (PMC) schedule "
        "that the "
        "precomputed grid does not have: a different number of rounds, specific calendar months, another age "
        "band (up to 59 months, e.g. SMC-style) or another coverage "
        "(e.g. 'what if we only did four monthly rounds from May, ages 3-24 months, "
        "in Ondo?'). The grid already holds six schedules: no PMC; SP at vaccine visits at 25% coverage; "
        "Connect quarterly, every two months, or monthly April-September for children 3-24 months at 85% "
        "coverage; Connect quarterly for 12-24 months only. If the ask matches one of those, call "
        "targeting_pmc_schedules instead -- it answers instantly. "
        "Give 'state' (a Nigerian state, as in targeting_pmc_schedules) and 'schedule': EITHER a grid schedule "
        "code (answered instantly from the grid) OR an object with exactly one of "
        "rounds_per_year (e.g. 4), months (calendar month numbers, e.g. [5,6,7,8] = May to August, repeated in "
        "both modelled years) or interval_days, plus age_min_months and age_max_months (default 3 and 24; whole "
        "months) and coverage (default 0.85, the grid's Connect coverage; rounded to 5-point steps). "
        "The default demo question maps to schedule={months:[5,6,7,8], age_min_months:3, age_max_months:24} "
        "for state 'Ondo'. 'seeds' (1-6, default 3) is the number of random replicates averaged. "
        "Returns {run_id, status, cached, eta_s, wait}. "
        "WHAT TO SAY: ONLY when status is queued or running, say first 'I'm running IDM's EMOD model now' and "
        "give the wait in 'wait' word for word: 'this takes ' + wait. 'wait' is set from whether the model "
        "server was already up when the run was submitted: 'about two minutes' when it was, 'about five minutes "
        "-- the model server is starting up' when it was not, plus 'after the run ahead finishes' when another "
        "run is ahead. Do not derive the wait from eta_s, which is only a countdown. When cached is true (a "
        "grid schedule, or a run done "
        "before) the result is in this response: say nothing about running a model, just present it. "
        "HOW TO WAIT -- call targeting_pmc_run_status with the run_id and the SAME state (and prices) "
        "every 10-15 seconds, for up to 8 minutes, until it says completed or failed. Do not start a second "
        "run for the same question while one is in flight. "
        "status=refused: the model cannot speak for that state (a seasonal state with no fitted setting, "
        "'more seasonal: SMC, not PMC'); nothing was started. Say why, do not work around it. "
        "status=busy: the model is occupied with other runs; say so, offer to try again in a few minutes, and "
        "meanwhile answer from targeting_pmc_schedules. "
        "status=failed: say what 'error' says (that the live model is unavailable right now) in one line, add "
        "no reason of your own, and answer from targeting_pmc_schedules instead. " + _PMC_PRESENT_RULES
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "description": "Nigerian state name, e.g. 'Ondo'."},
            "schedule": {
                "description": (
                    "A grid schedule code, or an object: exactly one of rounds_per_year (1-24), months (list of "
                    "calendar months 1-12) or interval_days (7-730); plus optional age_min_months (default 3), "
                    "age_max_months (default 24, up to 59), coverage (0-1, default 0.85)."
                ),
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {
                            "rounds_per_year": {"type": "integer", "minimum": 1, "maximum": 24},
                            "months": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 1, "maximum": 12},
                                "minItems": 1,
                            },
                            "interval_days": {"type": "number"},
                            "age_min_months": {"type": "number"},
                            "age_max_months": {"type": "number", "maximum": 59},
                            "coverage": {"type": "number"},
                        },
                        "additionalProperties": False,
                    },
                ],
            },
            "seeds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 6,
                "description": "Random-seed replicates averaged for the effect. Default 3.",
            },
            **_PMC_COST_PROPS,
        },
        "required": ["state", "schedule"],
        "additionalProperties": False,
    },
)
def targeting_pmc_run_model(user, *, state, schedule, seeds=3, cost_per_visit=None, platform_fee=None, dose_rate=None):
    from connect_labs.labs.indicators import pmc
    from connect_labs.labs.indicators.emod import live, runner, service
    from connect_labs.labs.indicators.models import PmcModelRun

    costs = _pmc_costs(cost_per_visit, platform_fee, dose_rate)
    if not isinstance(seeds, int) or isinstance(seeds, bool) or not 1 <= seeds <= service.MAX_SEEDS:
        raise MCPToolError("BAD_REQUEST", f"seeds must be a whole number from 1 to {service.MAX_SEEDS}.")
    if isinstance(schedule, str):
        grid_codes = {r["code"] for r in pmc.schedule_rows(costs)} - {"none"}
        if schedule not in grid_codes:
            raise MCPToolError(
                "BAD_REQUEST", f"Unknown schedule {schedule!r}. Known: {', '.join(sorted(grid_codes))}."
            )
        rounds, description, grid_code = None, None, schedule
    else:
        try:
            rounds, description = live.to_rounds(schedule)
        except ValueError as e:
            raise MCPToolError("BAD_REQUEST", str(e)) from None
        grid_code = live.grid_match(rounds)

    try:
        refusal = _pmc_state_fit_error(state)
    except ValueError as e:
        raise MCPToolError("BAD_REQUEST", str(e)) from None
    if refusal:
        return {"status": "refused", "cached": False, "run_id": None, "error": refusal}

    if grid_code:
        row = _pmc_grid_row(grid_code, costs)
        result = _pmc_present(state, row, costs, live=False, description=row["detail"], code=grid_code)
        return {"run_id": None, "status": "completed", "cached": True, "eta_s": 0, "result": result}

    code = live.schedule_code(rounds)
    fitted_to = runner.fitted_state_name(runner.fitted_setting(state) or {})
    http_status, payload = service.submit_run(state, [{"code": code, "rounds": rounds}], seeds)
    if http_status == 400:
        raise MCPToolError("BAD_REQUEST", payload["error"])
    if http_status == 429:
        return {"status": "busy", "cached": False, "run_id": None, "error": _PMC_BUSY}
    if http_status == 503:
        return {"status": "failed", "cached": False, "run_id": None, "error": _pmc_unavailable(None)}
    run = PmcModelRun.objects.get(pk=payload["run_id"])
    base = {"run_id": run.pk, "schedule": description}
    if payload["cached"]:
        row = live.summarise(run.result, code)
        result = _pmc_present(
            state, row, costs, live=True, description=description, code=code, run_id=run.pk, fitted_to=fitted_to
        )
        return {**base, "status": "completed", "cached": True, "eta_s": 0, "result": result}
    return {
        **base,
        "status": run.status,
        "cached": False,
        "eta_s": service.run_eta(run),
        "wait": service.wait_phrase(run),
    }


@register(
    name="targeting_pmc_run_status",
    description=(
        "Check on a live EMOD run started by targeting_pmc_run_model. Call it every 10-15 seconds, for up to "
        "8 minutes, with the run_id and the SAME state and prices you started the run with (the state is "
        "required: the finished result is costed at it). While status is queued or running, say nothing new "
        "beyond a short 'still running' line every minute or so; eta_s is the seconds left and 'wait' the "
        "phrase for the whole run (do not restate it as a new estimate). "
        "When status is completed the response carries the result, costed exactly as targeting_pmc_run_model "
        "describes. " + _PMC_PRESENT_RULES + " When status is failed, 'error' already says the live model is "
        "unavailable: tell the user that in one line, add no reason of your own, and answer from "
        "targeting_pmc_schedules instead. If the "
        "8 minutes pass with the run still going, say it is taking longer than expected and offer to check "
        "again; do not start a second run."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer", "description": "The run_id returned by targeting_pmc_run_model."},
            "state": {
                "type": "string",
                "description": "The state the run was started for; the result is costed there.",
            },
            **_PMC_COST_PROPS,
        },
        "required": ["run_id", "state"],
        "additionalProperties": False,
    },
)
def targeting_pmc_run_status(user, *, run_id, state, cost_per_visit=None, platform_fee=None, dose_rate=None):
    from connect_labs.labs.indicators.emod import live, runner, service
    from connect_labs.labs.indicators.models import PmcModelRun

    costs = _pmc_costs(cost_per_visit, platform_fee, dose_rate)
    try:
        run = PmcModelRun.objects.get(pk=run_id)
    except PmcModelRun.DoesNotExist:
        raise MCPToolError("NOT_FOUND", f"No live model run {run_id}.") from None
    run = service.heal(run)
    if run.status == PmcModelRun.FAILED:
        return {
            "run_id": run.pk,
            "status": "failed",
            "eta_s": 0,
            "error": _pmc_unavailable(run.error),
            "timings": run.timings,
        }
    if run.status != PmcModelRun.COMPLETED:
        return {
            "run_id": run.pk,
            "status": run.status,
            "eta_s": service.run_eta(run),
            "wait": service.wait_phrase(run),
            "timings": run.timings,
        }

    custom = next((s for s in run.request["schedules"] if s["code"] != "none"), None)
    try:
        code = custom["code"]
        effect = live.summarise(run.result, code)
        description = live.describe_rounds(custom["rounds"])
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("live EMOD run %s: the result could not be read: %s", run.pk, e)
        return {
            "run_id": run.pk,
            "status": "failed",
            "eta_s": 0,
            "error": _pmc_unavailable(None),
        }
    result = _pmc_present(
        state,
        effect,
        costs,
        live=True,
        description=description,
        code=code,
        run_id=run.pk,
        fitted_to=runner.fitted_state_name(run.request.get("setting")),
    )
    return {"run_id": run.pk, "status": "completed", "eta_s": 0, "timings": run.timings, "result": result}


# ---- Ranking states x designs (per-state EMOD grid) ----------------------------------------------------


@register(
    name="targeting_pmc_rank",
    description=(
        "Rank (state, design) pairs for malaria chemoprevention in Nigeria by cost per under-5 death averted, "
        "at the visitor's delivery costs, each with its multiple of GiveWell's benchmark and whether it clears "
        "GiveWell's bar ('bar', 6x). CALL IT for 'rank states and designs', 'most cost-effective state and "
        "design', 'which design where', 'rank the top 10' -- any question that compares designs across "
        "several states. For a single schedule what-if the grid does not hold (other months, rounds, ages or "
        "coverage in one state) use targeting_pmc_run_model; for the national comparison of the six PMC "
        "schedules in one modelled setting use targeting_pmc_schedules. "
        "ANSWER BRIEFLY -- it is read in a narrow (~400px) side panel: one sentence naming the top pair and "
        "how many of the ranked states clear the bar (states_clearing_bar; say plainly when few or none do -- a "
        "top 10 is not a recommendation if it sits below the bar); the ranked pairs as a table of AT MOST "
        "three columns ('State \u00b7 design' from state_design, '$ per death' from cost_per_death_averted, "
        "'x GiveWell' from multiple_of_benchmark); if 'note' is set, say it; one line naming the excluded "
        "states and why (mention excluded designs only if asked); costs_line as one line; two caveat lines "
        "(the first caveat: one fitted setting per state, not a full calibration; and the deaths caveat, the "
        "last: the multiple is a floor counting deaths only); then explorer_path as a short markdown link, "
        "[Open this ranking in the PMC explorer](...), with a few words beside it saying the explorer shows the "
        "same ranking for these states, where the visitor can re-price it, switch the deaths estimate and run a "
        "custom schedule in one state. Label every result with 'label' -- 'illustrative \u00b7 fitted to "
        "each state's prevalence and rainfall' -- and never call it calibrated. "
        "LEAD WITH 'recommendation' whenever the visitor asks what to do, which approach, or which schedule for a "
        "set of states (they should not have to pick a design themselves): say the approach (recommendation.label), "
        "that each state starts with its own rains, how many states it keeps and which it drops (below the bar), "
        "and the 'unclear' states (the approach's effect there is within the model's noise: never count them as "
        "passing or failing; name each one's best clear design and its multiple instead), "
        "deaths averted, cost, $ per death and x GiveWell for the kept states; then versus_quarterly (the "
        "proposal's base, x and the deaths-per-dollar ratio) and step_up (what more money buys at the margin, its "
        "cost per extra death and whether that increment clears the bar). Then say WHY in one or two sentences from "
        "the evidence (by_design: how the approaches compare; SP protects about a month, so doses in the rainy "
        "months count most). If recommendation is null, say why (no approach runs in every state, or no deaths "
        "loaded). "
        "WHICH SCHEDULE (asked, or a design the visitor names, such as quarterly, is not in the top pairs): answer "
        "from 'by_design', each design pooled across the states, with its states_no_effect (the states where its "
        "effect alone is not measurable); a design missing from 'ranked' is weak, not unmodelled. "
        "FROM A TARGETING SELECTION (the targeting page's state carries filters.selected_areas, e.g. "
        "'Kano (NGA), Ondo (NGA)', plus the question that produced them): take the NGA names, pass them as "
        "'states', and give the visitor 'explorer_path' -- the PMC explorer opened on those states, for "
        "context. If selected_areas is absent, call targeting_select with the page's filters to get the areas. "
        "Pass the visitor's costs if they gave any (cost_per_visit, platform_fee, dose_rate). "
        "Each state's results come from IDM's EMOD run in THAT state's own setting -- transmission fitted to "
        "its DHS prevalence, season from its rainfall -- for PMC (SP, children 3-24 months: 4, 6 or 8 monthly "
        "rounds from the rain onset, year-round monthly, quarterly, every two months) and, in seasonal states "
        "only, SMC (SPAQ, 3-59 months, 4 monthly rounds). Cases averted = the design's EMOD reduction in "
        "under-5 cases x the state's MAP incidence x its under-5 population (a floor); spend = doses x the "
        "targeted children x cost per dose, the same price per visit for PMC and SMC. Deaths averted = the "
        "same EMOD reduction x the state's under-5 malaria deaths (deaths_basis), valued at GiveWell's moral "
        "weight for an under-5 death; only deaths are counted, so the multiple is a floor. If asked whether a "
        "low-prevalence state is worth it, the multiple and the bar answer it -- cost per case barely varies "
        "between states because clinical incidence saturates as transmission rises. Returns 'ranked' (cheapest "
        "per death first, ties to more deaths averted; each pair also carries cases and cost per case; figures to two "
        "significant figures), 'best_per_state' (each state's own best design, if asked 'best per state'), "
        "'excluded' (states the model could not fit, states not in the grid, states with no design that had "
        "a measurable effect -- never silently dropped), 'excluded_designs', 'note' (set when fewer pairs "
        "exist than asked for), 'costs_line', 'caveats', 'label' and 'explorer_path'. If 'available' is false "
        "the per-state results are not computed yet: say so in one line and answer from "
        "targeting_pmc_schedules instead."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "states": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only these states (case-insensitive; 'Kano (NGA)' is accepted). Default: all.",
            },
            "top_n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "How many pairs to return. Default 10, at most 50.",
            },
            "deaths_basis": {
                "type": "string",
                "enum": ["prevalence_scaled", "map"],
                "description": (
                    "How each state's under-5 malaria deaths are estimated. Default 'prevalence_scaled' (under-5 "
                    "deaths x malaria's national share, spread by DHS prevalence); 'map' uses MAP's modelled "
                    "malaria deaths -- a cross-check, its state pattern does not track DHS prevalence."
                ),
            },
            **_PMC_COST_PROPS,
        },
        "additionalProperties": False,
    },
)
def targeting_pmc_rank(
    user,
    *,
    states=None,
    top_n=10,
    cost_per_visit=None,
    platform_fee=None,
    dose_rate=None,
    deaths_basis="prevalence_scaled",
):
    from urllib.parse import urlencode

    from django.conf import settings
    from django.urls import reverse

    from connect_labs.labs.indicators import pmc
    from connect_labs.labs.indicators.emod import mortality, rank

    grid = rank.load_grid(getattr(settings, "PMC_STATE_GRID_PATH", None))
    if grid is None:
        return {
            "available": False,
            "label": rank.LABEL,
            "message": (
                "Per-state results are not available yet: the per-state model grid has not been computed. "
                "Use targeting_pmc_schedules for the national schedule comparison and its state ranking."
            ),
        }
    try:
        costs = pmc.costs_or_default(cost_per_visit, platform_fee, dose_rate)
        # An unloaded registry gives no deaths at all: rank by cost per case and say so, rather than
        # excluding every state.
        deaths = mortality.malaria_u5_deaths(mortality.registry_burden(), deaths_basis) or None
        out = rank.rank_pairs(
            states, **costs, top_n=min(top_n, rank.MAX_TOP_N), grid=grid, deaths=deaths, deaths_basis=deaths_basis
        )
        if deaths is None:
            out["note"] = " ".join(
                filter(None, [out["note"], "No mortality figures are loaded, so this is ranked by cost per case."])
            )
    except (ValueError, TypeError) as e:
        raise MCPToolError("BAD_REQUEST", str(e)) from None

    q = {k: v for k, v in costs.items() if v != pmc.costs_or_default()[k]}
    if deaths_basis != mortality.DEFAULT_BASIS:
        q["deaths_basis"] = deaths_basis
    if states:
        # The selection as the grid names it, ranked or not: the explorer shows why a state was excluded.
        names = [p["state"] for p in out["best_per_state"]] + [e["state"] for e in out["excluded"]]
        q["states"] = ",".join(n for n in dict.fromkeys(names) if n in grid["states"])
    return {"available": True, **out, "explorer_path": f"{reverse('targeting:pmc')}?{urlencode(q)}"}
