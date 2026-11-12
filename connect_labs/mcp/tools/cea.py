"""The cost-effectiveness explorer, for an agent: the same engine the Models page calls.

``cea_model_run`` runs one of Connect's models (CHC, MBW, KC) at inputs the caller sets and returns what
the Models page shows: the headline, the chain row by row, sensitivity, and the model's own scenarios and
country figures on request. It calls ``impact.explore``, so an answer here is the answer on the page for
the same inputs, and it hands back the page's link with those inputs set, so the person asking can open
exactly what the agent computed.

Public inputs and arithmetic on vendored models: nothing here is specific to the caller, so it sits in the
``targeting:read`` scope with the PMC tools.
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
