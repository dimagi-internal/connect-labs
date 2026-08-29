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
  * ``off_method_units`` — units answered by a source the chosen method does not
    declare, inherited from a coarser unit. Most "Survey as measured" rows for
    DR Congo are IGME's national figure applied downward.
  * ``countries_unsupported`` — countries the method cannot answer at all,
    listed rather than silently dropped.
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


def _selection(indicator, threshold, resolution, method, iso_codes, extra_counts=()):
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
        "range, and which methods can actually answer it, since IGME publishes mortality "
        "only and cannot answer 14 of the 21. Start here."
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
        "FLOOR, not a measurement); 'off_method_units' says how many were answered by a "
        "source this method does not declare, inherited from a coarser unit; and "
        "'countries_unsupported' lists countries the method cannot answer at all."
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
        },
        "required": ["indicator"],
        "additionalProperties": False,
    },
)
def targeting_select(
    user, *, indicator, threshold=None, resolution="subnational", method=None, iso_codes=None, limit=None
):
    _, _, _, measures_mod, _, _, _ = _imports()
    selection, measure, chosen = _selection(indicator, threshold, resolution, method, iso_codes)
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
        "off_method_units": selection.off_method_units,
        "countries_fully_above": selection.countries_fully_above,
        "countries_partly_above": selection.countries_partly_above,
        "countries_unsupported": selection.countries_unsupported,
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
        },
        "required": ["indicator"],
        "additionalProperties": False,
    },
)
def targeting_methodology(user, *, indicator, threshold=None, resolution="subnational", method=None, iso_codes=None):
    _, export_mod, _, _, _, _, _ = _imports()
    selection, _, chosen = _selection(indicator, threshold, resolution, method, iso_codes)
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
        "programme (KMC is priced per newborn, a bednet per child, a water connection per "
        "household), so it is chosen, not guessed. Returns the absorbable spend, the unit "
        "count behind it, and any caveat. Where an indicator implies no case count the "
        "'case' basis is declined rather than approximated."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "indicator": {"type": "string"},
            "threshold": {"type": "number"},
            "basis": {
                "type": "string",
                "enum": ["birth", "under_5", "person", "household", "case"],
                "description": "What one unit of cost buys.",
            },
            "unit_cost": {"type": "number", "description": "USD per unit."},
            "resolution": {"type": "string", "enum": ["national", "subnational"], "default": "subnational"},
            "method": {"type": "string"},
            "iso_codes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["indicator", "basis", "unit_cost"],
        "additionalProperties": False,
    },
)
def targeting_scenario(
    user, *, indicator, basis, unit_cost, threshold=None, resolution="subnational", method=None, iso_codes=None
):
    _, _, interventions_mod, _, _, _, _ = _imports()
    try:
        unit_basis = interventions_mod.UnitBasis(basis)
    except ValueError:
        raise MCPToolError("BAD_REQUEST", f"Unknown basis {basis!r}") from None

    cases_measure = interventions_mod.measure_for(unit_basis, indicator)
    if cases_measure is None:
        raise MCPToolError(
            "BAD_REQUEST",
            f"A {basis!r} basis has no count for {indicator!r} — that indicator implies no "
            "case count, so pricing per case would be an approximation dressed as a figure. "
            "Choose person, household, birth or under_5.",
        )

    selection, measure, chosen = _selection(
        indicator, threshold, resolution, method, iso_codes, extra_counts=(cases_measure,)
    )
    units = selection.totals.get(cases_measure)
    got, of = selection.coverage.get(cases_measure, (0, 0))
    return {
        "indicator": indicator,
        "threshold": selection.threshold,
        "method": chosen,
        "basis": basis,
        "counts_measure": cases_measure,
        "units": round(units) if units is not None else None,
        "unit_cost": unit_cost,
        "absorbable_usd": round(units * unit_cost) if units is not None else None,
        "coverage": {"units_with_a_figure": got, "of_units_selected": of},
        "is_floor": bool(of and got < of),
        "caveat": (
            f"{of - got} of {of} selected units carry no {cases_measure} figure and contribute "
            "nothing, so this is a floor rather than a total."
        )
        if of and got < of
        else None,
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
    from collections import defaultdict

    from connect_labs.labs.admin_boundaries.models import AdminBoundary

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

    return {
        "loaded": {iso: loaded.get(iso, {}) for iso in wanted},
        "note": (
            "AdminBoundary is shared across labs apps and holds several sources — they are "
            "alternative tessellations of the same land, not a hierarchy. Never mix two "
            "sources inside one count or it double-counts. Targeting uses geoBoundaries."
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
