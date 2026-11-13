"""Explore a Connect model: set its inputs, read its chain, test how sensitive it is.

This is the cost-effectiveness explorer's engine. It is a thin layer over ``engine`` (the models' own
formulas) and ``valuation``: it adds no arithmetic of its own, so what the explorer shows is exactly
what the delivered ranking and any scenario compute for the same inputs.

A model's inputs come with the metadata its builders wrote in the spec: a label, what it means, where
to find it, the range seen in the countries, and a low and a high case. Those drive the explorer's form
and its sensitivity (``sensitivity``): the low and high case of each input that has them, one at a
time, against the multiple the model gives. ``grid`` varies two inputs together; ``scenarios`` and
``places`` run the cases the spec already holds.

Pure functions: nothing here touches the database.
"""

from __future__ import annotations

import json
import math

from connect_labs.effectiveness import engine, groups, result, valuation

#: Blocks a person sets for a place and a programme; the rest are the model's published and fixed values.
EDITABLE_BLOCKS = ("location", "program", "judgement call")


class ExploreError(ValueError):
    """A request the explorer cannot run, in words a person can act on.

    ``message`` is the only text a caller shows: it is written here, never an exception's own text.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def models() -> dict[str, dict]:
    """Each model's label, unit, variant and live Sheet, from the vendored summary."""
    with valuation.SUMMARY.open(encoding="utf-8") as f:
        return json.load(f)["models"]


def _spec_inputs(model: str) -> list[dict]:
    if model not in engine.MODELS:
        raise ExploreError(f"Unknown model {model!r}. The models are: {', '.join(engine.MODELS)}.")
    return engine.spec(model)["inputs"]


def _number(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def inputs(model: str) -> list[dict]:
    """Every input with the metadata the form needs, in the order the spec lists them."""
    out = []
    for i in _spec_inputs(model):
        out.append(
            {
                "key": i["key"],
                "label": i["label"],
                "unit": i["unit"],
                "block": i["block"],
                "type": i.get("type"),
                "value": i["value"],
                "low": _number(i.get("low")),
                "high": _number(i.get("high")),
                "meaning": i.get("meaning"),
                "where": i.get("where"),
                "range": i.get("range"),
                "format": i.get("format"),
                "group": groups.group_of(model, i["key"]),
                "editable": i["block"] in EDITABLE_BLOCKS,
            }
        )
    return out


def clean_overrides(model: str, raw: dict | None) -> dict[str, float]:
    """Keep only valid input keys with finite numbers; say which were refused."""
    keys = {i["key"] for i in _spec_inputs(model)}
    out: dict[str, float] = {}
    bad = []
    for k, v in (raw or {}).items():
        n = _number(v)
        if k not in keys or n is None:
            bad.append(k)
        else:
            out[k] = n
    if bad:
        raise ExploreError(f"Not an input of {model}, or not a number: {', '.join(sorted(bad))}.")
    return out


def _headline(model: str, overrides: dict) -> tuple[float, float, float]:
    """(multiple of the benchmark, price per person, deaths per 100,000) at these inputs."""
    try:
        return result.OUTPUTS[model](engine.run(model, overrides))
    except ZeroDivisionError:
        raise ExploreError(
            "Those inputs divide by zero somewhere in the model: check for a zero or a blank."
        ) from None


def run(model: str, overrides: dict | None = None) -> dict:
    """The model at its own inputs with ``overrides`` applied: the headline and every row of the chain."""
    overrides = clean_overrides(model, overrides)
    try:
        rows = engine.run(model, overrides)
    except ZeroDivisionError:
        raise ExploreError(
            "Those inputs divide by zero somewhere in the model: check for a zero or a blank."
        ) from None
    x, price, deaths = result.OUTPUTS[model](rows)
    chain = [
        {"key": r["key"], "label": r["label"], "step": r["step"], "value": rows[r["key"]], "format": r.get("format")}
        for r in engine.spec(model)["rows"]
        if not r.get("text") and r["key"] in rows
    ]
    bar = valuation.bar()
    return {
        "model": model,
        "overrides": overrides,
        "headline": {
            "multiple": x,
            "price_per_person": price,
            "deaths_per_100k": deaths,
            "bar": bar,
            "clears_bar": x >= bar,
        },
        "chain": chain,
    }


def default_included(model: str) -> list[str]:
    """The inputs sensitivity covers until the caller chooses: every input the spec gives a low and a high case."""
    return [i["key"] for i in inputs(model) if i["low"] is not None and i["high"] is not None]


def sensitivity(model: str, overrides: dict | None = None, include: list[str] | None = None) -> dict:
    """What moves the multiple, for the inputs the caller has switched on.

    ``rows`` has one entry per input that has a low and a high case, whether or not it is included; an included
    one carries the multiple at each case (each moved alone, the rest where they are). ``combined`` moves every
    included input to its worse case together, and to its better case together, so the spread of the whole set
    is visible and not only each input's own. ``groups`` gives the same per group (baseline, counterfactual,
    effectiveness, cost, other). Which case is worse is read per input from the model, so an input whose "low"
    case helps is still counted on the right side.
    """
    overrides = clean_overrides(model, overrides)
    ranged = {i["key"]: i for i in inputs(model) if i["low"] is not None and i["high"] is not None}
    chosen = default_included(model) if include is None else [k for k in include if k in ranged]
    base = _headline(model, overrides)[0]
    rows, worst, best = [], {}, {}
    for key, i in ranged.items():
        row = {
            "key": key,
            "label": i["label"],
            "unit": i["unit"],
            "format": i["format"],
            "group": i["group"],
            "low": i["low"],
            "high": i["high"],
            "included": key in chosen,
            "multiple_at_low": None,
            "multiple_at_high": None,
            "swing": None,
        }
        if key in chosen:
            x_low = _headline(model, {**overrides, key: i["low"]})[0]
            x_high = _headline(model, {**overrides, key: i["high"]})[0]
            row.update(multiple_at_low=x_low, multiple_at_high=x_high, swing=abs(x_high - x_low))
            worst[key], best[key] = (i["low"], i["high"]) if x_low <= x_high else (i["high"], i["low"])
        rows.append(row)

    def spread(keys):
        if not keys:
            return None
        return {
            "worst": _headline(model, {**overrides, **{k: worst[k] for k in keys}})[0],
            "best": _headline(model, {**overrides, **{k: best[k] for k in keys}})[0],
            "n": len(keys),
        }

    by_group = {}
    for g in groups.ORDER:
        in_group = [k for k in chosen if ranged[k]["group"] == g]
        if in_group:
            by_group[g] = spread(in_group)
    return {"base": base, "rows": rows, "combined": spread(chosen), "groups": by_group}


def grid(model: str, x_key: str, xs: list[float], y_key: str, ys: list[float], overrides: dict | None = None) -> dict:
    """The multiple across two inputs: rows are ``y_key`` values, columns are ``x_key`` values."""
    overrides = clean_overrides(model, overrides)
    keys = {i["key"] for i in _spec_inputs(model)}
    if x_key not in keys or y_key not in keys or x_key == y_key:
        raise ExploreError("Pick two different inputs of the model for the grid.")
    xs = [clean_overrides(model, {x_key: v})[x_key] for v in xs]
    ys = [clean_overrides(model, {y_key: v})[y_key] for v in ys]
    if len(xs) * len(ys) > 400:
        raise ExploreError("A grid is at most 400 cells.")
    cells = [[_headline(model, {**overrides, x_key: x, y_key: y})[0] for x in xs] for y in ys]
    return {"x_key": x_key, "xs": xs, "y_key": y_key, "ys": ys, "multiples": cells}


def delivery_overrides(model: str, place_inputs: dict, cost_per_person: float | None) -> dict[str, float]:
    """Inputs that put one delivered opportunity into the model, so its answer can be explored.

    ``place_inputs`` are the opportunity's place inputs as the baseline resolved them (location inputs
    only are kept); ``cost_per_person`` is what it actually cost per person reached. The model's price per
    person is its cost input plus fixed extras (a deworming tablet, say), so the cost input is shifted by
    the difference, which makes the model's price equal the observed one. The explorer then gives the same
    multiple ``result.evaluate`` gives for that opportunity at that place, because both are the model's
    result re-priced (``result.reprice``).
    """
    location = set(engine.location_keys(model))
    overrides = clean_overrides(model, {k: v for k, v in (place_inputs or {}).items() if k in location})
    if cost_per_person and cost_per_person > 0:
        price_now = _headline(model, overrides)[1]
        shifted = engine.defaults(model)["cost"] + (cost_per_person - price_now)
        if shifted > 0:
            overrides["cost"] = shifted
    return overrides


def scenarios(model: str) -> list[dict]:
    """The scenarios the model's builders wrote, each run: their name, what they set, and the result."""
    out = []
    for s in engine.spec(model).get("scenarios") or []:
        x, price, deaths = _headline(model, clean_overrides(model, s["sets"]))
        out.append(
            {"name": s["name"], "sets": s["sets"], "multiple": x, "price_per_person": price, "deaths_per_100k": deaths}
        )
    return out


def places(model: str) -> list[dict]:
    """The model at each country's own figures, where the spec holds them, best multiple first."""
    out = []
    for iso3, p in (engine.spec(model).get("places") or {}).items():
        r = result.model_country_result(model, iso3)
        if r:
            out.append({"iso3": iso3, "name": p.get("name") or iso3, "inputs": p.get("inputs") or {}, **r})
    out.sort(key=lambda r: -r["x"])
    return out
