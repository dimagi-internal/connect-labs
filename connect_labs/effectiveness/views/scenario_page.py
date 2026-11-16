"""What every scenario page hands its script: the choices as a link wrote them, and what the controls offer.

Market size and the workspace are two layouts of one scenario, so they read the link and describe the controls
here, once.
"""

from __future__ import annotations

from django.urls import reverse

from connect_labs.effectiveness import engine, explore, groups, market, people, valuation
from connect_labs.effectiveness.scenario import DIALS, Scenario
from connect_labs.labs.indicators.access import OpenLocallyMixin

#: Who may open a scenario page: the baseline's own gate (open on a laptop, signed in elsewhere), read in one place.
ScenarioPageGate = OpenLocallyMixin


def scenario_or_default(request) -> tuple[Scenario, str | None]:
    """The scenario in the link, or the default and the reason when the link cannot be run (a page still opens)."""
    try:
        return Scenario.from_params(request.GET), None
    except explore.ExploreError as exc:
        return Scenario(), exc.message


def page_data(request, scenario: Scenario, error: str | None = None) -> dict:
    """The JSON a scenario page embeds: the starting state and everything its controls need to draw themselves."""
    meta = explore.models()
    bar = valuation.bar()
    return {
        "state": scenario.to_state(bar),
        "error": error,
        "models": {
            k: {
                **meta[k],
                "key": k,
                "noun": market.UNITS[k].noun,
                "inputs": market.key_inputs(k, explore.inputs(k)),
            }
            for k in engine.MODELS
        },
        "indicators": people.indicator_choices(),
        "blocks": [{"key": g, "title": groups.TITLES[g]} for g in groups.KEY_GROUPS],
        "dials": list(DIALS),
        "bar": bar,
        "api_url": reverse("cea:market_data"),
        "models_url": reverse("cea:models"),
    }
