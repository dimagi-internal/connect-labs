"""The cost-effectiveness explorer, for an agent: the same engine the Models page calls.

``cea_model_run`` runs one of Connect's models (CHC, MBW, KC) at inputs the caller sets and returns what
the Models page shows: the headline, the chain row by row, sensitivity, and the model's own scenarios and
country figures on request. It calls ``impact.explore``, so an answer here is the answer on the page for
the same inputs, and it hands back the page's link with those inputs set, so the person asking can open
exactly what the agent computed.

Public inputs and arithmetic on vendored models: nothing here is specific to the caller, so it sits in the
``targeting:read`` scope with the targeting tools.

``cea_opportunities`` is the other half: the same models applied to what Connect actually delivered, read
from ``pulse.cea`` (the Implementation tab's figures, one function for every roll-up). It carries what
each opportunity cost and how many people it reached, so it is for full tokens only, not the
no-visit-data ones.
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..tool_registry import MCPToolError, register

INCLUDE = ("tornado", "scenarios", "places", "inputs")


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
        "`include` adds: `tornado` (default; one-at-a-time sensitivity, biggest swing first), `scenarios` (the "
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
                "default": ["tornado"],
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
def cea_model_run(user, *, model="chc", overrides=None, include=None, grid=None):
    from django.urls import reverse

    from connect_labs.impact import engine, explore

    include = ["tornado"] if include is None else list(include)
    unknown = [i for i in include if i not in INCLUDE]
    if unknown:
        raise MCPToolError("BAD_REQUEST", f"Unknown include {unknown}. Choose from {', '.join(INCLUDE)}.")
    try:
        clean = explore.clean_overrides(model, overrides)
        out = {"model": model, "run": explore.run(model, clean)}
        if "tornado" in include:
            out["tornado"] = explore.tornado(model, clean)
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
    from connect_labs.pulse import cea

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
