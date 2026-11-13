"""The cost-effectiveness explorer, for an agent: the same engine the Models page calls.

``cea_model_run`` runs one of Connect's models (CHC, MBW, KC) at inputs the caller sets and returns what
the Models page shows: the headline, the chain row by row, sensitivity, and the model's own scenarios and
country figures on request. It calls ``effectiveness.explore``, so an answer here is the answer on the page for
the same inputs, and it hands back the page's link with those inputs set, so the person asking can open
exactly what the agent computed.

Public inputs and arithmetic on vendored models: nothing here is specific to the caller, so it sits in the
``targeting:read`` scope with the targeting tools.

``cea_opportunities`` is the other half: the same models applied to what Connect actually delivered, read
from ``effectiveness.implementation`` (the Implementations page's figures, one function for every roll-up). It
carries what each opportunity cost and how many people it reached, so it is for full tokens only, not the
no-visit-data ones.
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..tool_registry import MCPToolError, register
from .indicators import _selection

INCLUDE = ("sensitivity", "scenarios", "places", "inputs")


@register(
    name="cea_model_run",
    description=(
        "Run one of Connect's cost-effectiveness models (chc = Child Health Campaign: ORS and zinc, vitamin A, "
        "deworming; mbw = Mother Baby Wellness; kc = Kangaroo Care) at inputs you set, and read the answer as a "
        "multiple of GiveWell's benchmark, with the price per person, deaths averted per 100,000 and whether it "
        "clears the bar. These are the models' own formulas, the same ones behind the live Sheets, the "
        "delivered-opportunity ranking and the cost-effectiveness explorer's Models page, so a figure here is the "
        'figure there. `overrides` maps an input key to a number; call with `include: ["inputs"]` to see every '
        "input's key, unit, low and high case, meaning and source. Which components count for CHC are inputs: "
        "`va_on` and `dw_on` are 1/0 switches for vitamin A and deworming (ORS and zinc are always in). "
        "`include` adds: `sensitivity` (default; each input with a low and high case alone, then everything switched "
        "on together worst and best, per group: baseline, counterfactual, effectiveness, cost), `scenarios` (the "
        "cases the model's builders wrote, each run), `places` (the model at each country's own published figures), "
        "`inputs`. `grid` runs two inputs against each other. The chain lists every row of the model. Returns "
        "`explorer_path`: the explorer page with these inputs set, to give the person asking. Modelled, not "
        "measured; quote the input values you set."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "model": {"type": "string", "enum": ["chc", "mbw", "kc"], "default": "chc"},
            "overrides": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "description": 'Input key to value, e.g. {"cost": 2.5, "ors": 0.4, "va_on": 0}. Shares are 0-1.',
            },
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": list(INCLUDE)},
                "default": ["sensitivity"],
            },
            "sensitivity_inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Input keys in the sensitivity; default every input with a low and a high case.",
            },
            "grid": {
                "type": "object",
                "properties": {
                    "x": {"type": "string", "description": "Input key across."},
                    "xs": {"type": "array", "items": {"type": "number"}},
                    "y": {"type": "string", "description": "Input key down."},
                    "ys": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["x", "xs", "y", "ys"],
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    },
)
def cea_model_run(user, *, model="chc", overrides=None, include=None, grid=None, sensitivity_inputs=None):
    from django.urls import reverse

    from connect_labs.effectiveness import engine, explore

    include = ["sensitivity"] if include is None else list(include)
    unknown = [i for i in include if i not in INCLUDE]
    if unknown:
        raise MCPToolError("BAD_REQUEST", f"Unknown include {unknown}. Choose from {', '.join(INCLUDE)}.")
    try:
        clean = explore.clean_overrides(model, overrides)
        out = {"model": model, "run": explore.run(model, clean)}
        if "sensitivity" in include:
            out["sensitivity"] = explore.sensitivity(model, clean, sensitivity_inputs)
        if "scenarios" in include:
            out["scenarios"] = explore.scenarios(model)
        if "places" in include:
            out["places"] = [{k: v for k, v in p.items() if k != "inputs"} for p in explore.places(model)]
        if "inputs" in include:
            out["inputs"] = explore.inputs(model)
        if grid:
            out["grid"] = explore.grid(model, grid["x"], grid["xs"], grid["y"], grid["ys"], clean)
    except explore.ExploreError as exc:
        raise MCPToolError("BAD_REQUEST", exc.message) from None
    query = {"model": model, **{f"i_{k}": v for k, v in clean.items()}}
    out["explorer_path"] = f"{reverse('cea:models')}?{urlencode(query)}"
    out["spec"] = {"version": engine.spec(model).get("version"), **engine.source()}
    return out


ROW_FIELDS = (
    "id",
    "name",
    "org_slug",
    "program_id",
    "service_name",
    "country_name",
    "active",
    "status",
    "status_text",
    "rank",
    "total_usd",
    "reached",
    "reach_basis",
    "cost_per_person",
    "model_cost_per_person",
    "model_x",
    "x",
    "clears_bar",
    "deaths_averted",
    "cost_per_death",
    "baseline_level",
    "areas_n",
)


@register(
    name="cea_opportunities",
    description=(
        "What Connect's delivered opportunities achieved, valued with Connect's cost-effectiveness models at "
        "what each actually cost: the Implementation tab's figures. Each opportunity is valued where its visits "
        "happened (district, else state, else the model's country figure; `baseline_level` says which), at its "
        "real cost per person reached, so `x` is the multiple of GiveWell's benchmark at actual cost and "
        "`model_x` is what the model gives at its own price. Select with `opportunity_ids`, `program_id`, "
        "`org_slug`, `country` (name or alpha-2), `model` (chc, mbw or kc), `active_only`; the answer carries "
        "`rollup` for exactly that selection (spend, people, deaths averted, cost per death and the "
        "spend-weighted multiple, each dollar counted once), and `programmes` splits it by Connect programme. "
        "With one opportunity and `detail: true` it adds every step: the areas, each place input with its "
        "level and source, the model's chain, and `explore` links to open it in the model explorer with its "
        "place and cost set. Rows are ranked by multiple; `limit` caps how many come back (the roll-up always "
        "covers the whole selection). Modelled, not measured; cost includes invoices paid through a "
        "programme's cost-only opportunities."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": {"type": "array", "items": {"type": "integer"}},
            "program_id": {"type": "integer"},
            "org_slug": {"type": "string"},
            "country": {"type": "string", "description": "Country name or ISO alpha-2, e.g. 'Nigeria' or 'NG'."},
            "model": {"type": "string", "enum": ["chc", "mbw", "kc"]},
            "active_only": {"type": "boolean", "default": False},
            "modelled_only": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "default": 25, "minimum": 1, "maximum": 300},
            "detail": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
)
def cea_opportunities(
    user,
    *,
    opportunity_ids=None,
    program_id=None,
    org_slug=None,
    country=None,
    model=None,
    active_only=False,
    modelled_only=True,
    limit=25,
    detail=False,
):
    from connect_labs.effectiveness import implementation as cea

    data = cea.ranking()
    wanted = set(opportunity_ids or [])
    want_country = (country or "").strip().lower()
    rows = [
        r
        for r in data["rows"]
        if (not wanted or r["id"] in wanted)
        and (program_id is None or r["program_id"] == program_id)
        and (not org_slug or r["org_slug"] == org_slug)
        and (not want_country or want_country in (r["country"].lower(), r["country_name"].lower()))
        and (model is None or r.get("model") == model)
        and (not active_only or r["active"])
    ]
    if modelled_only and not wanted:
        rows = [r for r in rows if r["status"] == cea.STATUS_MODELLED]
    if wanted and len(wanted - {r["id"] for r in data["rows"]}) == len(wanted):
        raise MCPToolError("NOT_FOUND", f"None of opportunities {sorted(wanted)} is a real (non-test) opportunity.")
    shown = sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 0, -r["total_usd"]))[:limit]
    out = {
        "count": len(rows),
        "shown": len(shown),
        "bar_x": data["bar_x"],
        "rollup": cea.rollup(rows),
        "programmes": cea.programmes(rows),
        "rows": [{k: r.get(k) for k in ROW_FIELDS} for r in shown],
        "spec": {"generated": data["generated"], "source": data["source"]},
    }
    if detail:
        if len(rows) != 1:
            raise MCPToolError(
                "BAD_REQUEST", f"`detail` needs exactly one opportunity; this selection has {len(rows)}."
            )
        d = cea.opportunity_detail(rows[0]["id"])
        r = (d or {}).get("result") or {}
        out["detail"] = {
            "placement": d.get("placement") if d else None,
            "areas": r.get("areas"),
            "other_inputs": r.get("other_inputs"),
            "explore": (d or {}).get("explore") or [],
        }
    return out


@register(
    name="cea_market_size",
    description=(
        "Size a market: what it would cost to reach everyone a selection of places holds. With `model` (chc, mbw "
        "or kc) it prices them at that Connect model's own cost per person per year, runs the model at each "
        "country's published figures and says where it clears GiveWell's bar: the Market size page's answer, with "
        "a link to it. Without `model` it takes a unit cost you set. For the unit-cost mode two things must be "
        "fixed and neither can be inferred from the "
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
            "model": {
                "type": "string",
                "enum": ["chc", "mbw", "kc"],
                "description": (
                    "Size the market at this Connect model's own cost per person per year instead of `basis` and "
                    "`unit_cost`: the same answer as the Market size page for the same places and inputs, run at each "
                    "country's published figures, with the part where the model clears the bar. Returns "
                    "`explorer_path`, the page with these inputs set."
                ),
            },
            "overrides": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "description": "With `model`: input key to value, as for cea_model_run.",
            },
            "bar": {"type": "number", "description": "With `model`: GiveWell's bar in multiples of the benchmark."},
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
                "description": "Carry counts to the delivery year before costing. See indicators_select.",
            },
        },
        "required": ["indicator"],
        "additionalProperties": False,
    },
)
def cea_market_size(
    user,
    *,
    indicator,
    basis=None,
    unit_cost=None,
    model=None,
    overrides=None,
    bar=None,
    threshold=None,
    resolution="subnational",
    method=None,
    iso_codes=None,
    reach=1.0,
    target_year=None,
):
    if model:
        return _market_size_by_model(
            model=model,
            overrides=overrides,
            bar=bar,
            indicator=indicator,
            threshold=threshold,
            resolution=resolution,
            method=method,
            iso_codes=iso_codes,
            target_year=target_year,
        )
    if basis is None or unit_cost is None:
        raise MCPToolError(
            "BAD_REQUEST",
            "Give `model` (chc, mbw or kc) to size a market at that model's price, "
            "or `basis` and `unit_cost` to size it at your own.",
        )
    from connect_labs.effectiveness import interventions as interventions_mod

    legal = [b.value for b in interventions_mod.UnitBasis]
    # The count fields every indicators tool speaks -- rows and totals carry
    # 'pop_u5'/'births'/'pop_total', and indicators_compare_criteria's `count` takes
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


def _market_size_by_model(*, model, overrides, bar, indicator, threshold, resolution, method, iso_codes, target_year):
    """``cea_market_size`` with a model: ``effectiveness.market`` over the baseline's people, as the page does."""
    from urllib.parse import urlencode

    from django.urls import reverse

    from connect_labs.effectiveness import engine, explore, market, people, valuation

    if model not in engine.MODELS:
        raise MCPToolError("BAD_REQUEST", f"Unknown model {model!r}. Choose one of: {', '.join(engine.MODELS)}.")
    try:
        clean = explore.clean_overrides(model, overrides)
    except explore.ExploreError as exc:
        raise MCPToolError("BAD_REQUEST", exc.message) from None
    selection, _, chosen = _selection(
        indicator,
        threshold,
        resolution,
        method,
        iso_codes,
        extra_counts=market.count_measures(),
        target_year=target_year,
    )
    found = people.group_selection(selection)
    bar = float(bar) if bar is not None else valuation.bar()
    try:
        sized = market.size(model, people.for_model(found, model), clean, bar)
    except ZeroDivisionError:
        raise MCPToolError("BAD_REQUEST", f"Those inputs divide by zero in the {model} model.") from None
    query = {"models": model, "indicator": indicator, "threshold": selection.threshold, "bar": bar}
    query.update({f"i.{model}.{k}": v for k, v in clean.items()})
    return {
        "indicator": indicator,
        "threshold": selection.threshold,
        "method": chosen,
        "bar": bar,
        "overrides": clean,
        "countries": len(found["countries"]),
        "countries_with_no_data": found["unsupported"],
        "market": sized,
        "explorer_path": f"{reverse('cea:market')}?{urlencode(query)}",
        "caveat": (
            "A year of cost to reach everyone in these places at the model's price. A country with no published "
            "model figures counts in the market and never in the part that clears the bar; a place with no count "
            "makes the total a floor."
        ),
    }
