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
  there, within any limits on how many places and countries.

A place's figures are only as good as the survey or estimate behind them, so ``trust`` pulls every local figure toward
what is known one level up (a territory toward its country, a country toward the model's own), and ``plan_check`` says
how a plan holds up if the local figures are only half to be believed.

Pure: the caller gives the people per place (from the baseline), this module prices and tests them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from connect_labs.effectiveness import engine, explore, groups, mortality, reliability, result
from connect_labs.effectiveness.scenario import KNOB_DEFAULT, PlanLimits

#: A place whose figure is more than this many times its country's, either way, is flagged: such a gap is as often a
#: survey artefact (under-reporting in a remote area, a small sample) as a real difference.
FAR_FROM_COUNTRY = 2.0
#: How far ``plan_check`` pulls each place toward the level above: half way.
CHECK_TRUST = 0.5


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


def _pull(local: float, anchor: float, trust: float) -> float:
    """``local`` believed to the extent of ``trust`` (1 = fully, 0 = not at all, so ``anchor``)."""
    return anchor + trust * (local - anchor)


def size(
    model: str,
    places: dict[str, dict],
    overrides: dict | None,
    bar: float,
    dials: dict | None = None,
    *,
    place_risk: bool = True,
    target: float | None = None,
    trust: float = 1.0,
    limits: PlanLimits | None = None,
) -> dict:
    """The model's market over ``places`` = ``{key: {"name", "iso3", "units", "measures", "inputs"}}``, at each
    place's figures.

    ``overrides`` are the caller's input changes, applied on top of every place. ``dials`` are the scenario's three
    (``scenario.DIALS``): ``reach`` is the most of a place's cases that can be reached, ``extra`` adds dollars a
    person a year the model does not count (below zero when delivery costs less), ``effect`` scales the model's
    effect; the multiple moves by ``result.scale_multiple``. With ``place_risk``, a place's own figures are used: the
    model's risk input scaled to the place's ``measures`` (``PLACE_RISK``) and any of the model's place inputs the
    baseline holds for it (``inputs``). ``trust`` is how far to believe them (1 = fully; 0 = a territory takes its
    country's, a country the model's). A place the model has no published figures for is priced at the model's own
    inputs and flagged ``modelled: False``; it counts in the market but never in the part that clears the bar:
    nothing says it does. With a ``target`` the best places are filled first (``plan``), within ``limits``.
    """
    unit = UNITS[model]
    dial = {**KNOB_DEFAULT, **(dials or {})}
    reach, effect, extra = dial["reach"] / 100, dial["effect"] / 100, dial["extra"]
    risk = PLACE_RISK.get(model) if place_risk else None
    shown = PLACE_RISK.get(model)
    rows = []
    for key, p in sorted(places.items(), key=lambda kv: (kv[1]["name"], str(kv[0]))):
        base, modelled = _country_inputs(model, p["iso3"])
        if place_risk:
            # the place's own figures for the model's place inputs, believed to the extent of `trust`
            for k, v in (p.get("inputs") or {}).items():
                base = {**base, k: _pull(v, base.get(k, v), trust)}
        inputs = {**base, **(overrides or {})}
        scale = local = country = se = shared = None
        if risk is not None:
            local = (p.get("measures") or {}).get(risk.measure)
            country = (p.get("measures") or {}).get(f"{risk.measure}_country")
            if local:
                believed = _pull(local, country or risk.reference, trust)
                scale = believed / risk.reference
                inputs[risk.input] = inputs.get(risk.input, engine.defaults(model)[risk.input]) * scale
                se = (p.get("measures_se") or {}).get(risk.measure)
                shared = (p.get("measures_shared_se") or {}).get(risk.measure)
        # the risk the model ran at, whether or not it was scaled: what a reader needs to see beside the multiple
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
        spread = _spread(model, unit, inputs, risk, se, (x, unit.deaths_per_100k(o)), effect, extra) if se else None
        units = p["units"] * reach if p["units"] is not None else None
        market_usd = units * per_person if units is not None else None
        deaths = units * deaths_pp if units is not None else None
        far = bool(local and country and not (1 / FAR_FROM_COUNTRY <= local / country <= FAR_FROM_COUNTRY))
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
                # what the place's own figure was, the country's beside it, where it came from, and whether it is far
                # off
                "measure": local,
                "measure_country": country,
                "measure_how": (p.get("measures_how") or {}).get(shown.measure) if shown else None,
                "far_from_country": far,
                "local_inputs": sorted((p.get("inputs") or {}).keys()) if place_risk else [],
                "conflict_rate": (p.get("measures") or {}).get("conflict_rate"),
                "risk_se": se,
                "risk_shared_se": shared,
                "risk_raw": (p.get("measures_raw") or {}).get(risk.measure) if risk is not None else None,
                **(spread or {}),
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
            "on": bool(place_risk),  # the switch: a model with no risk input still uses its territory's own figures
            "input": PLACE_RISK[model].input if model in PLACE_RISK else None,
            "measure": PLACE_RISK[model].measure if model in PLACE_RISK else None,
            "reference": PLACE_RISK[model].reference if model in PLACE_RISK else None,
            "scaled": sum(1 for r in rows if r["risk_basis"] == "place"),
            "far_from_country": sum(1 for r in rows if r["far_from_country"]),
            "local_inputs": sum(1 for r in rows if r["local_inputs"]),
        },
    }
    if target:
        out["plan"] = plan(rows, target, limits)
        out["plan"]["reliability"] = reliability.assess(rows, target)
    return out


def _spread(model, unit, inputs, risk, se, at, effect, extra) -> dict:
    """How far a place's multiple and deaths per person move with its risk's uncertainty.

    The model is run once more with the risk 10% higher to measure how steeply the multiple and the deaths per person
    follow it (their elasticities, ``at`` being the multiple and deaths per 100,000 at the place's own risk); the range
    is the 90% interval of the risk carried through them. ``se`` is the standard error of the log of the place's
    mortality.
    """
    step = 1.1
    o = engine.run(model, {**inputs, risk.input: inputs[risk.input] * step})
    price = unit.annual_cost(o)
    x1 = result.scale_multiple(result.OUTPUTS[model](o)[0], price, price + extra, effect)
    d1 = unit.deaths_per_100k(o)
    x0, d0 = at
    ex = math.log(x1 / x0) / math.log(step) if x0 > 0 and x1 > 0 else 0.0
    ed = math.log(d1 / d0) / math.log(step) if d0 > 0 and d1 > 0 else 0.0
    z = mortality.z_for(0.90)
    return {
        "multiple_elasticity": ex,
        "deaths_elasticity": ed,
        "multiple_low": x0 * math.exp(-z * ex * se),
        "multiple_high": x0 * math.exp(z * ex * se),
    }


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
        "countries": len({p["country"] for p in parts}),
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
        "far_from_country": r["far_from_country"],
        "conflict_rate": r.get("conflict_rate"),
    }


def _fill(able: list[dict], target: float) -> tuple[list[dict], float]:
    """Best places first, each up to its capacity, until the target is met; and what is left of it."""
    remaining, picked = float(target), []
    for r in sorted(able, key=lambda r: (-r["multiple"], -r["units"], r["name"])):
        if remaining <= 0:
            break
        take = min(r["units"], remaining)
        picked.append(_part(r, take))
        remaining -= take
    if remaining < 1e-6 * target:  # a target met is met exactly, not a rounding error short of it
        remaining = 0.0
    return picked, remaining


def by_country(parts: list[dict]) -> list[dict]:
    """A plan's places added up by country, the biggest first: where the work would be."""
    out: dict[str, dict] = {}
    for p in parts:
        c = out.setdefault(
            p["country"], {"country": p["country"], "places": 0, "reached": 0.0, "cost": 0.0, "deaths_averted": 0.0}
        )
        c["places"] += 1
        c["reached"] += p["reached"]
        c["cost"] += p["cost"]
        c["deaths_averted"] += p["deaths_averted"]
    for c in out.values():
        c["multiple"] = _totals([p for p in parts if p["country"] == c["country"]])["multiple"]
    return sorted(out.values(), key=lambda c: -c["reached"])


def plan(rows: list[dict], target: float, limits: PlanLimits | None = None) -> dict:
    """The best way to reach ``target`` people: the places with the highest multiple first, each up to the most that
    can be reached there (a row's ``units``, already capped by the reach dial), until the target is met.

    ``limits`` (``PlanLimits``): ``min_place`` leaves out a place too small to be worth a team, ``max_conflict`` one
    where reported conflict is above the ceiling (a place with no conflict figure is never left out on it: nothing
    says it is unsafe), and ``max_countries`` keeps to the countries that matter most to the best plan: the best plan
    is made, the countries ranked by the deaths it averts in each are taken in order, and it is made again within
    them. ``countries_needed`` says how many countries it takes, in that order, to reach the target at all.

    Compared with the other obvious plan, spreading the target over every allowed place in proportion to what each can
    give. Places the model has no figures for are never chosen: nothing says what reaching them buys.
    """
    able = [r for r in rows if r["units"] and r["multiple"] is not None]
    limits = limits or PlanLimits()
    too_small = [r for r in able if limits.min_place and r["units"] < limits.min_place]
    unsafe = [
        r
        for r in able
        if limits.max_conflict is not None and r.get("conflict_rate") is not None and r["conflict_rate"] > limits.max_conflict
    ]
    left_out = {id(r) for r in too_small + unsafe}
    able = [r for r in able if id(r) not in left_out]
    ranked: list[str] = []
    if able:
        unconstrained, _ = _fill(able, target)
        gave: dict[str, float] = {}
        for p in unconstrained:
            gave[p["country"]] = gave.get(p["country"], 0.0) + p["deaths_averted"]
        # countries the best plan uses first, then the rest by what they could give per person
        rest = {}
        for r in able:
            if r["country"] not in gave:
                rest[r["country"]] = max(rest.get(r["country"], 0.0), r["multiple"])
        ranked = sorted(gave, key=lambda c: -gave[c]) + sorted(rest, key=lambda c: -rest[c])
    needed, have = None, 0.0
    for i, c in enumerate(ranked, 1):
        have += sum(r["units"] for r in able if r["country"] == c)
        if have >= target:
            needed = i
            break
    if limits.max_countries and ranked:
        kept = set(ranked[: limits.max_countries])
        able = [r for r in able if r["country"] in kept]
    capacity = sum(r["units"] for r in able)
    picked, remaining = _fill(able, target)
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
        "countries": by_country(picked),
        "countries_needed": needed,
        "limits": {
            "min_place": limits.min_place,
            "max_countries": limits.max_countries,
            "max_conflict": limits.max_conflict,
            "left_out": len(left_out),
            "left_out_small": len(too_small),
            "left_out_conflict": len(unsafe),
        },
        "totals": best,
        "even": even,
        "gain": gain,
    }


def plan_check(chosen: dict, rows_if_less_trusted: list[dict], replanned: dict | None) -> dict | None:
    """How a plan holds up if the local figures are only partly to be believed.

    ``chosen`` is the plan made at the figures as they are; ``rows_if_less_trusted`` the same places sized with each
    local figure pulled part of the way to what is known a level up; ``replanned`` the best plan for those. The chosen
    places are valued at the less-trusted figures and set beside the best that could have been done with them: near
    100% the plan does not depend on the figures being right; low, it does.
    """
    if not chosen or not replanned:
        return None
    by_key = {r["key"]: r for r in rows_if_less_trusted}
    kept = sum(p["reached"] * by_key[p["key"]]["deaths_per_person"] for p in chosen["places"] if p["key"] in by_key)
    best = replanned["totals"]["deaths_averted"]
    same = {p["key"] for p in chosen["places"]} & {p["key"] for p in replanned["places"]}
    return {
        "trust": CHECK_TRUST * 100,
        "kept_deaths": kept,
        "best_deaths": best,
        "kept_share": (kept / best) if best else None,
        "places_in_common": len(same),
        "places": len(chosen["places"]),
    }
