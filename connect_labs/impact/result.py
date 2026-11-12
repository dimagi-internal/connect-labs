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

Re-pricing is ``reprice`` below: every model here runs with GiveWell's leveraging adjustment at 1, so
benefit per person is fixed by the place and cost per person scales the multiple exactly.
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


def reprice(
    model_x: float,
    model_price: float,
    deaths_per_100k: float,
    reached: float,
    total_usd: float,
    bar_x: float,
    *,
    effect: float = 1.0,
    reach_share: float = 1.0,
    extra_per_person: float = 0.0,
    at_model_price: bool = False,
) -> dict | None:
    """What a delivery achieved, from the model's result at its place and what was actually spent.

    The one place this arithmetic lives: ``evaluate`` calls it, the Implementation what-if calls it, and
    so does anything that reports a delivered opportunity. Every model here runs with GiveWell's
    leveraging adjustment at 1, so benefit per person is fixed by the place and the multiple scales
    exactly with price:

        multiple at actual cost = model multiple x model price / cost per person

    The keywords are the what-if: ``effect`` scales the model's effect on deaths (1 = as modelled),
    ``reach_share`` the people counted, ``extra_per_person`` adds cost bought outside the programme, and
    ``at_model_price`` values the people at the model's own price. Defaults are the delivery as observed.
    Returns None when nobody is counted.
    """
    people = reached * reach_share
    if people <= 0:
        return None
    cost = model_price * people if at_model_price else total_usd + extra_per_person * people
    cost_per_person = cost / people
    x = model_x * effect * model_price / cost_per_person
    deaths = people * deaths_per_100k / 100_000 * effect
    return {
        "reached": people,
        "cost_used": cost,
        "cost_per_person": cost_per_person,
        "x": x,
        "deaths_averted": deaths,
        "cost_per_death": (cost / deaths) if deaths else None,
        "clears_bar": x >= bar_x,
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
        r = reprice(model_x, price, deaths_per_100k, reached, total_usd, bar_x)
        out["x"] = r["x"]
        out["deaths_averted"] = r["deaths_averted"]
        out["cost_per_death"] = r["cost_per_death"]
        out["clears_bar"] = r["clears_bar"]
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
