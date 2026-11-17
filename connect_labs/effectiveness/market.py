"""Market size: what it would cost to deliver a Connect programme to the people in the chosen places, and the best way
to reach a number of them.

Pick the places (the baseline's selection: an indicator over a threshold, in some countries or territories), pick one
or more cost-effectiveness models, and set the models' inputs. For each model and place the market is the people the
programme can reach there times what the model says it costs per person per year, and the model is run at that place's
own figures to say whether it clears GiveWell's bar and how many deaths it averts. Nothing here has arithmetic of its
own: the cost per person, the multiple and the deaths are the models' (``effectiveness.engine``).

Two things the models do not say are said here, and labelled as such wherever they show:

* ``PLACE_RISK``: Kangaroo Care's baseline risk is one number for the world, but cost-effectiveness is proportional to
  it, so where small babies are likelier to die it is worth more. The model owner's own three anchor scenarios
  (Nigeria 18%, Liberia 15.2%, Uganda 9.6%) are each about 4.6 times the national neonatal mortality, so the risk is
  scaled by local neonatal mortality from the point where it is the model's own 13%.
* ``plan``: with a target number of people, the best places are filled first, each up to the most that can be reached
  there.

Pure: the caller gives the people per place (from the baseline), this module prices and tests them.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.effectiveness import engine, explore, groups, result
from connect_labs.effectiveness.scenario import KNOB_DEFAULT


@dataclass(frozen=True)
class Unit:
    """Who a model serves, as a count the baseline holds, what one of them costs a year in the model, and how many of
    them per 100,000 the programme saves from dying in a year."""

    measure: str
    noun: str
    annual_cost: callable
    deaths_per_100k: callable


#: Per model: the baseline count of the people it serves, and the model's own cost per person per year.
#: CHC is priced per child per campaign and runs ``campaigns`` a year (``full_cost``); KC and MBW are priced per
#: baby, and per mother and baby pair, for the whole programme. Deaths are per 100,000 people a year at the model's own
#: effect, the rows each model's own headline is built from (CHC's ``bundle_d``: ORS and vitamin A).
UNITS = {
    "chc": Unit("pop_u5", "children under 5", lambda o: o["full_cost"], lambda o: o["bundle_d"]),
    "kc": Unit("births_lbw", "low-birthweight newborns", lambda o: o["cost"], lambda o: o["deaths"]),
    "mbw": Unit("births", "births", lambda o: o["cost"], lambda o: o["post_adj"] + o["neo_adj"]),
}


@dataclass(frozen=True)
class PlaceRisk:
    """A model input that is a risk, and the baseline measure that says how risky a place is."""

    input: str
    measure: str
    #: The value of ``measure`` at which the model's own input is right (it is that input's published value).
    reference: float
    label: str


#: Kangaroo Care's ``post_ref`` (the 28-day death risk of an enrolled small baby, 13%) scales with a place's neonatal
#: mortality (per 1,000 live births). ``reference`` is the mortality at which 13% holds: the model's own scenarios
#: put a small baby's risk at 18% for Nigeria (national mortality 39.0), 15.2% for Liberia (32.9) and 9.6% for Uganda
#: (21.3), 4.62, 4.62 and 4.51 times it, so 13% sits at 13 / 4.58 = 28.4. ``test_market`` pins the three anchors.
PLACE_RISK = {"kc": PlaceRisk("post_ref", "nmr", 28.4, "newborn mortality")}


def count_measures() -> tuple[str, ...]:
    return tuple(sorted({u.measure for u in UNITS.values()}))


def risk_measures() -> tuple[str, ...]:
    """The baseline measures a place needs for its risk to be scaled to it."""
    return tuple(sorted({r.measure for r in PLACE_RISK.values()}))


def key_inputs(model: str, inputs: list[dict]) -> list[dict]:
    """The inputs worth changing when sizing: the four key groups, less the place figures the country sets."""
    return [i for i in inputs if i["group"] in groups.KEY_GROUPS and i["block"] != "location"]


def _country_inputs(model: str, iso3: str) -> tuple[dict, bool]:
    place = engine.country(model, iso3)
    return ((place or {}).get("inputs") or {}), place is not None or model == "kc"


def size(
    model: str,
    places: dict[str, dict],
    overrides: dict | None,
    bar: float,
    dials: dict | None = None,
    *,
    place_risk: bool = True,
    target: float | None = None,
) -> dict:
    """The model's market over ``places`` = ``{key: {"name", "iso3", "units", "measures"}}``, run at each place's
    figures.

    ``overrides`` are the caller's input changes, applied on top of every place. ``dials`` are the scenario's three
    (``scenario.DIALS``): ``reach`` is the most of a place's cases that can be reached, ``extra`` adds dollars a
    person a year the model does not count (below zero when delivery costs less), ``effect`` scales the model's
    effect; the multiple moves by ``result.scale_multiple``. With ``place_risk`` and a place whose ``measures`` hold
    the model's risk measure (``PLACE_RISK``), the model's risk input is scaled to the place. A place the model has
    no published figures for is priced at the model's own inputs and flagged ``modelled: False``; it counts in the
    market but never in the part that clears the bar: nothing says it does. With a ``target`` the best places are
    filled first (``plan``).
    """
    unit = UNITS[model]
    dial = {**KNOB_DEFAULT, **(dials or {})}
    reach, effect, extra = dial["reach"] / 100, dial["effect"] / 100, dial["extra"]
    risk = PLACE_RISK.get(model) if place_risk else None
    rows = []
    for key, p in sorted(places.items(), key=lambda kv: (kv[1]["name"], str(kv[0]))):
        base, modelled = _country_inputs(model, p["iso3"])
        inputs = {**base, **(overrides or {})}
        scale = None
        if risk is not None:
            local = (p.get("measures") or {}).get(risk.measure)
            if local:
                scale = local / risk.reference
                inputs[risk.input] = inputs.get(risk.input, engine.defaults(model)[risk.input]) * scale
        # the risk the model ran at, whether or not it was scaled: what a reader needs to see beside the multiple
        shown = PLACE_RISK.get(model)
        used = inputs.get(shown.input, engine.defaults(model)[shown.input]) if shown else None
        o = engine.run(model, inputs)
        model_x = result.OUTPUTS[model](o)[0]
        price = unit.annual_cost(o)
        per_person = price + extra
        if per_person <= 0:
            raise explore.ExploreError(
                f"The extra cost takes the price per person to {per_person:.2f}: it has to stay above zero."
            )
        x = result.scale_multiple(model_x, price, per_person, effect)
        deaths_pp = unit.deaths_per_100k(o) / 100_000 * effect
        units = p["units"] * reach if p["units"] is not None else None
        market_usd = units * per_person if units is not None else None
        deaths = units * deaths_pp if units is not None else None
        rows.append(
            {
                "key": str(key),
                "iso3": p["iso3"],
                "country": p.get("country") or p["name"],
                "name": p["name"],
                "units": units,
                "units_all": p["units"],
                "cost_per_person": per_person,
                "market_usd": market_usd,
                "multiple": x if modelled else None,
                "modelled": modelled,
                "clears_bar": bool(modelled and x >= bar),
                "partial": bool(p.get("partial")),
                "deaths_per_person": deaths_pp,
                "deaths_averted": deaths,
                "cost_per_death": (market_usd / deaths) if deaths else None,
                "risk": used,
                "risk_scale": scale,
                "risk_basis": "place" if scale is not None else "model",
            }
        )
    counted = [r for r in rows if r["market_usd"] is not None]
    clearing = [r for r in counted if r["clears_bar"]]
    out = {
        "model": model,
        "noun": unit.noun,
        "measure": unit.measure,
        "rows": rows,
        "totals": {
            "countries": len(rows),
            "places": len(rows),
            "units": sum(r["units"] for r in counted),
            "market_usd": sum(r["market_usd"] for r in counted),
            "deaths_averted": sum(r["deaths_averted"] for r in counted),
            "multiple": blended_multiple(counted),
            "clearing_countries": len(clearing),
            "clearing_units": sum(r["units"] for r in clearing),
            "clearing_usd": sum(r["market_usd"] for r in clearing),
            "unmodelled_countries": sum(1 for r in rows if not r["modelled"]),
            "is_floor": any(r["partial"] or r["units"] is None for r in rows),
        },
        "flat": is_flat(rows),
        "place_risk": {
            "applies": model in PLACE_RISK,
            "on": risk is not None,
            "input": PLACE_RISK[model].input if model in PLACE_RISK else None,
            "measure": PLACE_RISK[model].measure if model in PLACE_RISK else None,
            "reference": PLACE_RISK[model].reference if model in PLACE_RISK else None,
            "scaled": sum(1 for r in rows if r["risk_basis"] == "place"),
        },
    }
    if target:
        out["plan"] = plan(rows, target)
    return out


def blended_multiple(rows: list[dict]) -> float | None:
    """The multiple of the whole: each place's multiple weighted by what is spent there (benefit is proportional to
    spend times multiple), over the places the model has figures for."""
    spent = [r for r in rows if r["multiple"] is not None and r["market_usd"]]
    total = sum(r["market_usd"] for r in spent)
    return sum(r["market_usd"] * r["multiple"] for r in spent) / total if total else None


def is_flat(rows: list[dict]) -> bool:
    """Every place with figures has the same multiple: the model cannot tell them apart."""
    ms = [r["multiple"] for r in rows if r["multiple"] is not None]
    return len(ms) > 1 and max(ms) - min(ms) < 1e-9


def _totals(parts: list[dict]) -> dict:
    cost = sum(p["cost"] for p in parts)
    deaths = sum(p["deaths_averted"] for p in parts)
    spent = [p for p in parts if p["multiple"] is not None and p["cost"]]
    paid = sum(p["cost"] for p in spent)
    return {
        "places": len(parts),
        "people": sum(p["reached"] for p in parts),
        "cost": cost,
        "deaths_averted": deaths,
        "cost_per_death": (cost / deaths) if deaths else None,
        "multiple": (sum(p["cost"] * p["multiple"] for p in spent) / paid) if paid else None,
    }


def _part(r: dict, reached: float) -> dict:
    return {
        "key": r["key"],
        "name": r["name"],
        "country": r["country"],
        "reached": reached,
        "share_of_cases": (reached / r["units_all"]) if r["units_all"] else None,
        "cost": reached * r["cost_per_person"],
        "multiple": r["multiple"],
        "deaths_averted": reached * r["deaths_per_person"],
        "risk": r["risk"],
    }


def plan(rows: list[dict], target: float) -> dict:
    """The best way to reach ``target`` people: the places with the highest multiple first, each up to the most that
    can be reached there (a row's ``units``, already capped by the reach dial), until the target is met.

    Compared with the other obvious plan, spreading the target over every place in proportion to what each can give.
    Places the model has no figures for are never chosen: nothing says what reaching them buys.
    """
    able = [r for r in rows if r["units"] and r["multiple"] is not None]
    capacity = sum(r["units"] for r in able)
    remaining, picked = float(target), []
    for r in sorted(able, key=lambda r: (-r["multiple"], -r["units"], r["name"])):
        if remaining <= 0:
            break
        take = min(r["units"], remaining)
        picked.append(_part(r, take))
        remaining -= take
    if remaining < 1e-6 * target:  # a target met is met exactly, not a rounding error short of it
        remaining = 0.0
    best = _totals(picked)
    spread = [_part(r, min(r["units"], target * r["units"] / capacity)) for r in able] if capacity else []
    even = _totals(spread) if spread else None
    gain = None
    if even and even["deaths_averted"] and best["deaths_averted"]:
        gain = {
            "deaths_averted_pct": (best["deaths_averted"] / even["deaths_averted"] - 1) * 100,
            "cost_per_death_pct": (best["cost_per_death"] / even["cost_per_death"] - 1) * 100,
            "places_fewer": even["places"] - best["places"],
        }
    planned = {p["key"]: p["reached"] for p in picked}
    for r in rows:
        r["planned"] = planned.get(r["key"], 0.0)
    return {
        "target": target,
        "capacity": capacity,
        "shortfall": max(0.0, remaining),
        "feasible": remaining <= 0.5,
        "places": picked,
        "totals": best,
        "even": even,
        "gain": gain,
    }
