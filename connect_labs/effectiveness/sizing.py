"""A scenario's market: the one place a ``Scenario`` becomes the answer Market size and the workspace show.

``people`` says who lives in the scenario's places and how risky each is, ``market`` prices them at each model's cost,
tests them against the bar and, with a target, plans the reach, ``scenario`` carries every choice. This composes them
so the Market size page, the workspace page, an agent's tool and anything else that answers the same question get the
same payload, and no page owns arithmetic.
"""

from __future__ import annotations

from connect_labs.effectiveness import explore, market, people, valuation
from connect_labs.effectiveness.scenario import Scenario


def _size(model: str, trust: float, run: dict) -> dict:
    return market.size(
        model,
        run["places"],
        run["overrides"],
        run["bar"],
        run["dials"],
        place_risk=run["place_risk"],
        target=run["target"],
        trust=trust,
        limits=run["limits"],
    )


def overall(sized: list[dict]) -> dict:
    """The programmes chosen, taken together: what they cost, the deaths they avert and the multiple of the whole.

    A programme with a plan counts at its plan (what is actually to be reached), one without at everything it can
    reach. The page shows these as its headline so it adds nothing up itself."""
    parts = []
    for m in sized:
        t = m["plan"]["totals"] if m.get("plan") else None
        parts.append(
            {
                "cost": t["cost"] if t else m["totals"]["market_usd"],
                "deaths": t["deaths_averted"] if t else m["totals"]["deaths_averted"],
                "multiple": t["multiple"] if t else m["totals"]["multiple"],
                "people": t["people"] if t else m["totals"]["units"],
            }
        )
    cost = sum(p["cost"] for p in parts)
    deaths = sum(p["deaths"] for p in parts)
    weighted = [p for p in parts if p["multiple"] is not None and p["cost"]]
    paid = sum(p["cost"] for p in weighted)
    return {
        "planned": any(m.get("plan") for m in sized),
        "feasible": all(m["plan"]["feasible"] for m in sized if m.get("plan")),
        "cost": cost,
        "deaths_averted": deaths,
        "cost_per_death": (cost / deaths) if deaths else None,
        "multiple": (sum(p["cost"] * p["multiple"] for p in weighted) / paid) if paid else None,
        "people": sum(p["people"] for p in parts),
        "places": sum(m["totals"]["places"] for m in sized),
        "clearing_places": sum(m["totals"]["clearing_countries"] for m in sized),
        "is_floor": any(m["totals"]["is_floor"] for m in sized),
    }


def market_of(scenario: Scenario) -> dict:
    """Each chosen model's market in the scenario's places, with how the places were chosen.

    Raises ``explore.ExploreError`` when the inputs divide by zero in a model or take a price to zero, so a caller has
    one error to say.
    """
    question, everywhere = people.question_for(scenario)
    bar = scenario.resolved_bar(valuation.bar())
    found = people.people_by_place(question, scenario.grain)
    sized = []
    for k in scenario.models:
        try:
            run = dict(
                places=people.for_model(found, k),
                overrides=scenario.overrides_for(k),
                bar=bar,
                dials=scenario.dials_for(k),
                place_risk=scenario.place_risk,
                target=scenario.target,
                limits=scenario.limits(),
            )
            out = _size(k, scenario.trust / 100, run)
            # A plan made at the figures as they are, and how it holds up if they are only half to be believed.
            if (
                out.get("plan")
                and scenario.trust >= 100
                and (out["place_risk"]["scaled"] or out["place_risk"]["local_inputs"])
            ):
                less = _size(k, market.CHECK_TRUST, run)
                out["plan"]["check"] = market.plan_check(out["plan"], less["rows"], less.get("plan"))
        except ZeroDivisionError:
            raise explore.ExploreError(f"Those inputs divide by zero in the {k} model.") from None
        sized.append({**out, "overrides": scenario.overrides_for(k), "dials": scenario.dials_for(k)})
    return {
        "models": sized,
        "overall": overall(sized),
        "bar": bar,
        "target": scenario.target,
        "trust": scenario.trust,
        "grain": scenario.grain,
        "selection": people.describe(question, found, everywhere),
        "changes": scenario.changes(),
        "link": scenario.to_params(),
    }
