"""What a round of spend buys in one area, on Connect's CHC model.

This is the targeting-side question ("if we spend $X here at $Y a household visit, what does it
buy, and does it clear the bar?") answered with the same model, the same place baselines and the
same valuation as the delivered-opportunity ranking (``impact.result``), so the two are one
modelling and differ only in whether the people and cost are observed or planned.

The model is the Connect Child Health Campaign (ORS and zinc, vitamin A, deworming), not GiveWell's
door-to-door ORS grant it replaced here: that chain was a separate calculation pinned to GiveWell's
published CHAI Bauchi column, with its own funging. Pure functions over resolved inputs: nothing
here touches the database, so the tool and the tests share one implementation.
"""

from __future__ import annotations

from connect_labs.impact import engine
from connect_labs.impact import result as impact_result
from connect_labs.impact import valuation
from connect_labs.impact.baseline import Value

MODEL = "chc"
#: What a visit costs, and the ORS coverage a place might already have, bracketing a door-to-door visit
#: and what household surveys report across northern Nigeria. The grid always contains the quoted answer.
SENSITIVITY_UNIT_COSTS = (2.00, 2.50, 3.00, 3.50, 4.34, 5.00)
SENSITIVITY_COVERAGES = (0.40, 0.50, 0.60, 0.70, 0.80)

NOT_COUNTED = (
    "water treatment (chlorine)",
    "hygiene promotion",
    "transmission effects (cholera): they need a dynamic model to value",
)


def override(key: str, value: float, area: str) -> Value:
    """A location input the caller supplied in place of the registry's figure."""
    return Value(key, value, "caller override", area, "caller override", "supplied by the caller", None)


def _model_at(inputs: dict[str, Value], overrides: dict[str, float] | None = None) -> tuple[float, float, float]:
    """(multiple at the model's price, the model's price per child per campaign, deaths per 100,000)."""
    values = {k: v.value for k, v in inputs.items()}
    values.update(overrides or {})
    return impact_result.OUTPUTS[MODEL](engine.run(MODEL, values))


def evaluate_round(
    iso3: str,
    area: str,
    level: str,
    inputs: dict[str, Value],
    *,
    spend: float,
    unit_cost: float,
    children_per_household: float,
) -> dict:
    """One round: ``spend`` dollars at ``unit_cost`` a household visit, ``children_per_household`` children each."""
    if spend <= 0 or unit_cost <= 0 or children_per_household <= 0:
        raise ValueError("spend, unit_cost and children_per_household must be positive")
    households = spend / unit_cost
    children = households * children_per_household
    cost_per_child = unit_cost / children_per_household
    res = impact_result.evaluate(
        MODEL,
        iso3,
        [{"name": area, "level": level, "share": 1.0, "inputs": inputs}],
        children,
        spend,
        valuation.bar(),
        detail=True,
    )
    x = res["x"]
    return {
        "households": households,
        "children_reached": children,
        "cost_per_child": cost_per_child,
        "deaths_averted": res["deaths_averted"],
        "cost_per_death_averted": res["cost_per_death"],
        "units_of_value_per_dollar": x * valuation.benchmark(),
        "multiple_of_benchmark": x,
        "clears_bar": res["clears_bar"],
        # The multiple is inverse in price, so the price that lands exactly on the bar.
        "breakeven_unit_cost": unit_cost * x / valuation.bar(),
        "model_multiple_at_model_price": res["model_x"],
        "model_cost_per_child": res["model_cost_per_person"],
        "deaths_per_100k_children": res["model_deaths_per_100k"],
        "_detail": res,
    }


def sensitivity(inputs: dict[str, Value], *, children_per_household: float) -> dict:
    """Multiple of the benchmark across visit price (rows) x baseline ORS coverage (columns).

    The area's own coverage is added as a column if it is not already one.
    """
    own = inputs["ors"].value
    covs = sorted({*SENSITIVITY_COVERAGES, round(own, 3)})
    cells = {c: _model_at(inputs, {"ors": c}) for c in covs}
    grid = [
        [round(cells[c][0] * cells[c][1] / (price / children_per_household), 1) for c in covs]
        for price in SENSITIVITY_UNIT_COSTS
    ]
    return {"unit_costs": list(SENSITIVITY_UNIT_COSTS), "baseline_coverages": covs, "multiples": grid}
