"""The cost-effectiveness explorer, for an agent: the same engine the Models page calls.

``cea_model_run`` runs one of Connect's models (CHC, MBW, KC) at inputs the caller sets and returns what
the Models page shows: the headline, the chain row by row, sensitivity, and the model's own scenarios and
country figures on request. It calls ``effectiveness.explore``, so an answer here is the answer on the page for
the same inputs, and it hands back the page's link with those inputs set, so the person asking can open
exactly what the agent computed.

Public inputs and arithmetic on vendored models: nothing here is specific to the caller, so it sits in the
``targeting:read`` scope with the targeting tools.

``cea_scenarios`` and ``cea_scenario_save`` keep a scenario (places, programmes, dials, assumptions, the bar) under a
name, as the scenario panel on Market size and the workspace does, under the same rules (``effectiveness.saved``): a
scenario is private until shared, and a saved name is replaced only when asked.

``cea_opportunities`` is the other half: the same models applied to what Connect actually delivered, read
from ``effectiveness.implementation`` (the Implementations page's figures, one function for every roll-up). It
carries what each opportunity cost and how many people it reached, so it is for full tokens only, not the
no-visit-data ones.
"""

from __future__ import annotations

from urllib.parse import urlencode

from connect_labs.effectiveness.scenario import CAP_KEYS, CAPS  # pure: the ceilings a plan can be held to

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


# The place-and-plan choices a scenario can carry (grain, target, limits, ceilings, price basis, trust, place-scaled
# risk), shared by the sizing tool and the save tool so one scenario means the same thing to both.
_PLAN_PROPERTIES = {
    "grain": {
        "type": "string",
        "enum": ["country", "territory"],
        "default": "country",
        "description": (
            "With `model`: size countries, or each state or province (`territory`), where the best places to "
            "work show."
        ),
    },
    "target": {
        "type": "number",
        "description": (
            "With `model`: how many people to reach (the model's own unit: low-birthweight newborns for kc). "
            "Returns `plan`: the best places first, each up to the most that can be reached there (`dials."
            "reach`), with deaths averted, cost per death averted, the blended multiple and the gain over "
            "spreading the same target evenly."
        ),
    },
    "min_place": {
        "type": "number",
        "description": (
            "With `model` and `target`: leave out any place that can give fewer people than this (too small "
            "to be worth a team)."
        ),
    },
    "max_countries": {
        "type": "integer",
        "minimum": 1,
        "description": (
            "With `model` and `target`: keep to this many countries, the ones that matter most to the best "
            "plan; `plan.countries_needed` says how many it takes to reach the target at all."
        ),
    },
    # one ceiling per scenario.CAPS entry (max_conflict, max_remote, ...): a plan leaves out a place above it
    **{
        c.key: {
            "type": "number",
            "description": (
                f"With `model` and `target`: {c.label.lower()} ({c.unit}). {c.hint} " "It never changes a multiple."
            ),
        }
        for c in CAPS
    },
    "cost_basis": {
        "type": "string",
        "enum": ["model", "observed"],
        "description": (
            "With `model`: `model` (default) prices a person at the model's own figure everywhere; "
            "`observed` moves the price toward what delivered opportunities cost in that country, by how "
            "much delivery stands behind it (rows carry `price_model`, `price_observed` and how many "
            "opportunities). Observed costs come from small early programmes: read them as evidence."
        ),
    },
    "trust": {
        "type": "number",
        "minimum": 0,
        "maximum": 100,
        "default": 100,
        "description": (
            "With `model`: how far to believe each place's own figures, in %. 100 uses them fully; lower "
            "pulls "
            "a territory toward its country's and a country toward the model's, to see whether a plan "
            "survives "
            "noisy data. A plan also carries `check`: how it holds up at 50."
        ),
    },
    "place_risk": {
        "type": "boolean",
        "default": True,
        "description": (
            "With `model`: kc's baseline risk is one number for the world in the model itself, but cost-"
            "effectiveness is proportional to it, so by default it is scaled to each place's newborn "
            "mortality "
            "(a Labs reading, calibrated on the model's own Nigeria, Liberia and Uganda scenarios). False "
            "gives the model's flat figures."
        ),
    },
}


def _plan_params(
    grain="country",
    target=None,
    min_place=None,
    max_countries=None,
    cost_basis=None,
    trust=100,
    place_risk=True,
    caps=None,
) -> dict:
    """The link keys for those choices; only what differs from the default is returned."""
    out = {}
    if grain and grain != "country":
        out["grain"] = grain
    for key, value in (
        ("target", target),
        ("min_place", min_place),
        ("max_countries", max_countries),
        *[(k, v) for k, v in (caps or {}).items() if k in CAP_KEYS],
        ("cost_basis", None if cost_basis in (None, "model") else cost_basis),
        ("trust", None if trust == 100 else trust),
        ("place_risk", None if place_risk else "0"),
    ):
        if value is not None:
            out[key] = value
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
            **_PLAN_PROPERTIES,
            "dials": {
                "type": "object",
                "properties": {k: {"type": "number"} for k in ("extra", "effect", "reach")},
                "additionalProperties": False,
                "description": (
                    "With `model`: `extra` dollars a person a year the model does not count (below zero when delivery "
                    "is cheaper), `effect` as % of the model's, `reach` as the most % of a place's cases that can be "
                    "reached (kc starts at 50, the others at 100)."
                ),
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
    grain="country",
    target=None,
    dials=None,
    place_risk=True,
    min_place=None,
    max_countries=None,
    cost_basis=None,
    trust=100,
    **caps,
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
            grain=grain,
            target=target,
            dials=dials,
            place_risk=place_risk,
            min_place=min_place,
            max_countries=max_countries,
            cost_basis=cost_basis,
            trust=trust,
            caps=caps,
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


def _market_size_by_model(
    *,
    model,
    overrides,
    bar,
    indicator,
    threshold,
    resolution,
    method,
    iso_codes,
    target_year,
    grain="country",
    target=None,
    dials=None,
    place_risk=True,
    min_place=None,
    max_countries=None,
    cost_basis=None,
    trust=100,
    caps=None,
):
    """``cea_market_size`` with a model: the same ``effectiveness.sizing`` the Market size and Workspace pages call,
    so an agent's answer is the page's for the same scenario. The tool only turns its arguments into a scenario."""
    from urllib.parse import urlencode

    from django.urls import reverse

    from connect_labs.effectiveness import engine, explore, sizing
    from connect_labs.effectiveness.scenario import EVERYWHERE, KNOBS, Scenario

    from .indicators import _imports, _resolve_method

    if model not in engine.MODELS:
        raise MCPToolError("BAD_REQUEST", f"Unknown model {model!r}. Choose one of: {', '.join(engine.MODELS)}.")
    availability_mod, _, _, measures_mod, methods_mod, _, _ = _imports()
    params = {"models": model, "indicator": indicator}
    if indicator != EVERYWHERE:
        if indicator not in measures_mod.MEASURES:
            raise MCPToolError("BAD_REQUEST", f"Unknown indicator {indicator!r}. Call indicators_list to list them.")
        params["method"] = _resolve_method(availability_mod, methods_mod, indicator, resolution, method)
    for key, value in (
        ("threshold", threshold),
        ("iso", ",".join(iso_codes) if iso_codes else None),
        ("target_year", target_year),
        ("bar", bar),
    ):
        if value is not None:
            params[key] = value
    params.update(_plan_params(grain, target, min_place, max_countries, cost_basis, trust, place_risk, caps))
    for key, value in (dials or {}).items():
        if key not in KNOBS:
            raise MCPToolError("BAD_REQUEST", f"{key!r} is not a dial: {', '.join(KNOBS)}.")
        params[f"{model}_{key}"] = value
    params.update({f"i.{model}.{k}": v for k, v in (overrides or {}).items()})
    try:
        scenario = Scenario.from_params({k: str(v) for k, v in params.items()})
        payload = sizing.market_of(scenario)
    except explore.ExploreError as exc:
        raise MCPToolError("BAD_REQUEST", exc.message) from None
    sel, sized = payload["selection"], payload["models"][0]
    # The page's own link: the map and the plan are on the workspace, a country table is on Market size.
    page = "cea:workspace" if (scenario.grain == "territory" or scenario.target) else "cea:market"
    out = {
        "indicator": indicator,
        "threshold": sel["threshold"],
        "method": sel["method"],
        "bar": payload["bar"],
        "overrides": scenario.overrides_for(model),
        "dials": scenario.dials_for(model),
        "grain": scenario.grain,
        "countries": sel["countries"],
        "places": sel["places"],
        "countries_with_no_data": sel["unsupported"],
        "market": sized,
        "explorer_path": f"{reverse(page)}?{urlencode(payload['link'])}",
        "caveat": (
            "A year of cost to reach the people a programme can reach in these places, at the model's price. A place "
            "with no published model figures counts in the market and never in the part that clears the bar; a place "
            "with no count makes the total a floor. Kangaroo Care's risk is scaled to each place's newborn mortality "
            "by "
            "Labs (the model itself holds one figure for the world): `market.place_risk` says whether, and "
            "`place_risk: false` gives the model's flat figures. Cost per place is the model's, not measured."
        ),
    }
    if sized.get("plan"):
        out["plan"] = sized["plan"]
    return out


_SAVED_STATUS = {403: "PERMISSION_DENIED", 404: "NOT_FOUND", 409: "CONFLICT"}


def _saved_error(exc) -> MCPToolError:
    return MCPToolError(_SAVED_STATUS.get(exc.status, "BAD_REQUEST"), exc.message)


@register(
    name="cea_scenarios",
    description=(
        "Saved cost-effectiveness scenarios: the places, programmes, dials, assumptions and bar someone chose on "
        "Market size or the Workspace, kept under a name. With no `id`, lists the caller's own scenarios and the "
        "ones others have shared (`mine`, `shared`), each with its `params` (the page link as a dict), `note`, "
        "`models_changed` (the programmes whose model has moved since it was saved, so its numbers may differ) and "
        "`open_path`, where it opens on the workspace. With an `id`, returns that one; add `run: true` to size its "
        "market now, with the models as they are today (the answer `cea_market_size` gives for the same scenario)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "run": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
)
def cea_scenarios(user, *, id=None, run=False):
    from connect_labs.effectiveness import explore, saved, sizing

    try:
        if id is None:
            return saved.listing(user)
        record = saved.get(user, id)
        out = saved.as_dict(record, user)
        if run:
            try:
                out["market"] = sizing.market_of(saved.scenario_of(record))
            except explore.ExploreError as exc:
                raise MCPToolError("BAD_REQUEST", f"That scenario can no longer be run: {exc.message}") from None
        return out
    except saved.SavedError as exc:
        raise _saved_error(exc) from None


@register(
    name="cea_scenario_save",
    is_write=True,
    description=(
        "Save a cost-effectiveness scenario under a name, for the caller. Private unless `shared` is true (then every "
        "signed-in Labs user can read and copy it; only the caller can replace or delete it). Saving a name the "
        "caller already has is refused unless `overwrite` is true. Give the scenario as the pages do: `models` "
        "(chc, kc, "
        "mbw), `indicator` (a burden indicator such as u5mr, or `all` for everywhere with data) and its `threshold`, "
        "`iso_codes` (blank for all Africa), `bar`, `dials` per model (`extra` dollars a person a year, `effect` and "
        "`reach` as percent of the model's and of the people in the places) and `overrides` per model "
        "(input key to number; `cea_model_run` with include `inputs` lists the keys). Anything left out is the models "
        "as published. Also `grain` (country or territory), `target` (people to reach), the plan limits (`min_place`, "
        "`max_countries` and the ceilings `max_conflict`, `max_remote`), `cost_basis`, `trust` and `place_risk`, "
        "as for cea_market_size. Returns the saved scenario and `open_path`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 200},
            "note": {"type": "string"},
            "shared": {"type": "boolean", "default": False},
            "overwrite": {"type": "boolean", "default": False},
            "models": {"type": "array", "items": {"type": "string", "enum": ["chc", "kc", "mbw"]}, "minItems": 1},
            "indicator": {"type": "string", "default": "all"},
            "threshold": {"type": "number"},
            "iso_codes": {"type": "array", "items": {"type": "string"}},
            "bar": {"type": "number"},
            "dials": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {k: {"type": "number"} for k in ("extra", "effect", "reach")},
                    "additionalProperties": False,
                },
            },
            "overrides": {
                "type": "object",
                "additionalProperties": {"type": "object", "additionalProperties": {"type": "number"}},
            },
            **_PLAN_PROPERTIES,
        },
        "required": ["name", "models"],
        "additionalProperties": False,
    },
)
def cea_scenario_save(
    user,
    *,
    name,
    models,
    note="",
    shared=False,
    overwrite=False,
    indicator="all",
    threshold=None,
    iso_codes=None,
    bar=None,
    dials=None,
    overrides=None,
    grain="country",
    target=None,
    min_place=None,
    max_countries=None,
    cost_basis=None,
    trust=100,
    place_risk=True,
    **caps,
):
    from connect_labs.effectiveness import engine, saved
    from connect_labs.effectiveness.scenario import KNOBS

    params = {"models": ",".join(models), "indicator": indicator}
    if threshold is not None:
        params["threshold"] = threshold
    if iso_codes:
        params["iso"] = ",".join(iso_codes)
    if bar is not None:
        params["bar"] = bar
    params.update(_plan_params(grain, target, min_place, max_countries, cost_basis, trust, place_risk, caps))
    for kind, per_model in (("dials", dials), ("overrides", overrides)):
        for model, changes in (per_model or {}).items():
            if model not in engine.MODELS:
                raise MCPToolError(
                    "BAD_REQUEST", f"`{kind}` names {model!r}, which is not a model: {', '.join(engine.MODELS)}."
                )
            for key, value in changes.items():
                if kind == "dials":
                    if key not in KNOBS:
                        raise MCPToolError("BAD_REQUEST", f"{key!r} is not a dial: {', '.join(KNOBS)}.")
                    params[f"{model}_{key}"] = value
                else:
                    params[f"i.{model}.{key}"] = value
    try:
        record, created = saved.save(user, name, params, note=note, shared=shared, overwrite=overwrite)
    except saved.SavedError as exc:
        raise _saved_error(exc) from None
    return {**saved.as_dict(record, user), "created": created}
