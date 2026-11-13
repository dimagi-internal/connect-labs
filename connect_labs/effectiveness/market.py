"""Market size: what it would cost to deliver a Connect programme to everyone in the chosen places.

Pick the places (the baseline's selection: an indicator over a threshold, in some countries), pick one or more
cost-effectiveness models, and set the models' inputs. For each model and country the market is the people the
programme serves there times what the model says it costs per person per year, and the model is run at that
country's own figures to say whether it clears GiveWell's bar. Nothing here has arithmetic of its own: the cost per
person and the multiple are the models' (``effectiveness.engine``).

Pure: the caller gives the people per country (from the baseline), this module prices and tests them.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.effectiveness import engine, groups, result


@dataclass(frozen=True)
class Unit:
    """Who a model serves, as a count the baseline holds, and what one of them costs a year in the model."""

    measure: str
    noun: str
    annual_cost: callable


#: Per model: the baseline count of the people it serves, and the model's own cost per person per year.
#: CHC is priced per child per campaign and runs ``campaigns`` a year (``full_cost``); KC and MBW are priced per
#: baby, and per mother and baby pair, for the whole programme.
UNITS = {
    "chc": Unit("pop_u5", "children under 5", lambda o: o["full_cost"]),
    "kc": Unit("births_lbw", "low-birthweight newborns", lambda o: o["cost"]),
    "mbw": Unit("births", "births", lambda o: o["cost"]),
}


def count_measures() -> tuple[str, ...]:
    return tuple(sorted({u.measure for u in UNITS.values()}))


def key_inputs(model: str, inputs: list[dict]) -> list[dict]:
    """The inputs worth changing when sizing: the four key groups, less the place figures the country sets."""
    return [i for i in inputs if i["group"] in groups.KEY_GROUPS and i["block"] != "location"]


def _country_inputs(model: str, iso3: str) -> tuple[dict, bool]:
    place = engine.country(model, iso3)
    return ((place or {}).get("inputs") or {}), place is not None or model == "kc"


def size(model: str, people: dict[str, dict], overrides: dict | None, bar: float) -> dict:
    """The model's market over ``people`` = ``{iso3: {"name", "units"}}``, run at each country's figures.

    ``overrides`` are the caller's input changes, applied on top of every country. A country the model has no
    published figures for is priced at the model's own inputs and flagged ``modelled: False``; it counts in the
    market but never in the part that clears the bar, because nothing says it does.
    """
    unit = UNITS[model]
    rows = []
    for iso3, p in sorted(people.items(), key=lambda kv: kv[1]["name"]):
        base, modelled = _country_inputs(model, iso3)
        o = engine.run(model, {**base, **(overrides or {})})
        x = result.OUTPUTS[model](o)[0]
        per_person = unit.annual_cost(o)
        units = p["units"]
        rows.append(
            {
                "iso3": iso3,
                "country": p["name"],
                "units": units,
                "cost_per_person": per_person,
                "market_usd": units * per_person if units is not None else None,
                "multiple": x if modelled else None,
                "modelled": modelled,
                "clears_bar": bool(modelled and x >= bar),
                "partial": bool(p.get("partial")),
            }
        )
    counted = [r for r in rows if r["market_usd"] is not None]
    clearing = [r for r in counted if r["clears_bar"]]
    return {
        "model": model,
        "noun": unit.noun,
        "measure": unit.measure,
        "rows": rows,
        "totals": {
            "countries": len(rows),
            "units": sum(r["units"] for r in counted),
            "market_usd": sum(r["market_usd"] for r in counted),
            "clearing_countries": len(clearing),
            "clearing_units": sum(r["units"] for r in clearing),
            "clearing_usd": sum(r["market_usd"] for r in clearing),
            "unmodelled_countries": sum(1 for r in rows if not r["modelled"]),
            "is_floor": any(r["partial"] or r["units"] is None for r in rows),
        },
    }
