"""A scenario's market: the one place a ``Scenario`` becomes the answer Market size and the workspace show.

``people`` says who lives in the scenario's places, ``market`` prices them at each model's cost and tests them against
the bar, ``scenario`` carries every choice. This composes the three so the Market size page, the workspace page and
anything that answers the same question get the same payload, and neither page owns arithmetic.
"""

from __future__ import annotations

from connect_labs.effectiveness import explore, market, people, valuation
from connect_labs.effectiveness.scenario import Scenario


def market_of(scenario: Scenario) -> dict:
    """Each chosen model's market in the scenario's places, with how the places were chosen.

    Raises ``explore.ExploreError`` when the inputs divide by zero in a model, so a caller has one error to say.
    """
    question, everywhere = people.question_for(scenario)
    bar = scenario.resolved_bar(valuation.bar())
    found = people.people_by_country(question)
    sized = []
    for k in scenario.models:
        try:
            out = market.size(k, people.for_model(found, k), scenario.overrides_for(k), bar, scenario.dials_for(k))
        except ZeroDivisionError:
            raise explore.ExploreError(f"Those inputs divide by zero in the {k} model.") from None
        sized.append({**out, "overrides": scenario.overrides_for(k), "dials": scenario.dials_for(k)})
    return {
        "models": sized,
        "bar": bar,
        "selection": people.describe(question, found, everywhere),
        "changes": scenario.changes(),
        "link": scenario.to_params(),
    }
