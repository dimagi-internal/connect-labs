"""Value one delivery end to end: where it happened, the baseline there, the model, its cost.

``evaluate()`` takes an opportunity's areas (``places``), its observed people and
cost (Pulse), and runs the model's own formulas (``engine``) once per area with
that area's baseline (``baseline``). It returns every step, so a page can show
the whole chain and a reader can check any number against its source:

    where      each area, its share of approved visits
    baseline   each location input per area: value, level, source, how
    model      every other input, from the model's spec (value, type, where from)
    per area   the model's multiple, price and deaths averted per 100,000 there
    combined   visit-weighted model multiple and deaths per 100,000
    observed   people reached, total cost, cost per person (and invoice pooling)
    result     multiple at actual cost, deaths averted, cost per death, the bar

Re-pricing: every model here runs with GiveWell's leveraging adjustment at 1, so
benefit per person is fixed by the place and cost per person scales the multiple
exactly: multiple at actual cost = model multiple x model price / actual price.
"""

from __future__ import annotations

from connect_labs.impact import engine

#: How each model's spec rows give the three figures a delivery is valued by.
#: Price and deaths are per person the opportunity counts once: a child per
#: campaign (CHC, at the model's default of two campaigns a year), a baby (KC),
#: a mother and baby pair (MBW). These match planning/model_results.js upstream.
OUTPUTS = {
    "chc": lambda o: (o["full_x"], o["full_cost"] / o["campaigns"], o["bundle_d"] / o["campaigns"]),
    "kc": lambda o: (o["x"], o["cost"], o["deaths"]),
    "mbw": lambda o: (o["x"], o["cost"], o["post_adj"] + o["neo_adj"]),
}

#: Rows a reader most needs to follow the chain, per model, in order.
CHAIN = {
    "chc": [
        "deaths",
        "ors",
        "ors_d",
        "vas",
        "va_d",
        "bundle_d",
        "full_cost",
        "full_units",
        "lev",
        "full_x",
    ],
    "kc": ["babies", "deaths", "units", "cost", "lev", "x"],
    "mbw": ["pairs", "post_d", "neo_d", "post_adj", "neo_adj", "units", "cost", "lev", "x"],
}


def model_country_result(model: str, iso3: str) -> dict | None:
    """The model at a country's own figures: (x, price, deaths per 100k), or None outside the models' countries."""
    if model != "kc" and engine.country(model, iso3) is None:
        return None
    o = engine.run(model, (engine.country(model, iso3) or {}).get("inputs") or {})
    x, price, d = OUTPUTS[model](o)
    return {"x": x, "price": price, "deaths_per_100k": d}


def _row_labels(model: str) -> dict:
    return {r["key"]: r for r in engine.spec(model)["rows"]}


def evaluate(
    model: str,
    iso3: str,
    areas: list[dict],
    reached: int,
    total_usd: float,
    bar_x: float,
    *,
    detail: bool = False,
) -> dict:
    """Value one delivery.

    ``areas``: ``[{"name", "level", "share", "inputs": {key: baseline.Value}}]``;
    an empty list values it at the country's figures. Shares must sum to 1.
    """
    per_area = []
    for a in areas:
        overrides = {k: v.value for k, v in a["inputs"].items()}
        o = engine.run(model, overrides)
        x, price, d = OUTPUTS[model](o)
        entry = {
            "name": a["name"],
            "level": a["level"],
            "share": a["share"],
            "visits": a.get("visits"),
            "model_x": x,
            "price": price,
            "deaths_per_100k": d,
            "inputs": {k: v.as_dict() for k, v in a["inputs"].items()},
        }
        if detail:
            labels = _row_labels(model)
            entry["chain"] = [
                {"key": k, "label": labels[k]["label"], "step": labels[k]["step"], "value": o[k]}
                for k in CHAIN[model]
                if k in o and k in labels
            ]
        per_area.append(entry)

    model_x = sum(e["share"] * e["model_x"] for e in per_area)
    price = per_area[0]["price"] if per_area else None
    deaths_per_100k = sum(e["share"] * e["deaths_per_100k"] for e in per_area)
    country = model_country_result(model, iso3)

    out = {
        "model": model,
        "areas": per_area,
        "model_x": model_x,
        "model_cost_per_person": price,
        "model_deaths_per_100k": deaths_per_100k,
        "country_model_x": country["x"] if country else None,
        "cost_per_person": (total_usd / reached) if reached else None,
        "x": None,
        "deaths_averted": None,
        "cost_per_death": None,
        "clears_bar": None,
    }
    if reached and total_usd > 0 and per_area:
        out["x"] = model_x * price / out["cost_per_person"]
        out["deaths_averted"] = reached * deaths_per_100k / 100_000
        out["cost_per_death"] = (total_usd / out["deaths_averted"]) if out["deaths_averted"] else None
        out["clears_bar"] = out["x"] >= bar_x
    if detail:
        loc = set(engine.location_keys(model))
        out["other_inputs"] = [
            {
                "key": i["key"],
                "label": i["label"],
                "value": i["value"],
                "unit": i["unit"],
                "block": i["block"],
                "type": i["type"],
                "where": i.get("where"),
            }
            for i in engine.spec(model)["inputs"]
            if i["key"] not in loc
        ]
        out["location_labels"] = {i["key"]: i["label"] for i in engine.spec(model)["inputs"] if i["key"] in loc}
        out["spec"] = {"version": engine.spec(model).get("version"), **engine.source()}
    return out
