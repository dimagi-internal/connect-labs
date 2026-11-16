"""What every scenario page hands its script: the choices as a link wrote them, and what the controls offer.

Market size and the workspace are two layouts of one scenario, so they read the link and describe the controls
here, once.
"""

from __future__ import annotations

from django.middleware.csrf import get_token
from django.urls import reverse

from connect_labs.effectiveness import engine, explore, groups, market, people, saved, valuation
from connect_labs.effectiveness.scenario import DIALS, Scenario
from connect_labs.labs.indicators.access import OpenLocallyMixin

#: Who may open a scenario page: the baseline's own gate (open on a laptop, signed in elsewhere), read in one place.
ScenarioPageGate = OpenLocallyMixin


def scenario_or_default(request) -> tuple[Scenario, dict | None, str | None]:
    """The scenario in the link, the saved scenario it opened (if any), and why it could not be run (if so).

    ``?scenario=<id>`` opens a saved scenario; any other key in the link changes it, so a saved scenario with one dial
    moved is a link. A link that cannot be run still opens the page, at the default, and says why."""
    params = dict(request.GET.items())
    sid = params.pop("scenario", None)
    record, error = None, None
    if sid:
        try:
            record = saved.get(request.user, int(sid))
            params = {**record.params, **params}
        except (ValueError, saved.SavedError):
            error = "That saved scenario is not available to you."
    try:
        return (
            Scenario.from_params(params),
            (saved.as_dict(record, request.user) if record and not error else None),
            error,
        )
    except explore.ExploreError as exc:
        return Scenario(), None, exc.message


def page_data(request, scenario: Scenario, loaded: dict | None = None, error: str | None = None) -> dict:
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
        "loaded": loaded,
        "signed_in": saved.signed_in(request.user),
        "csrf": get_token(request),
        "scenarios_url": reverse("cea:scenarios"),
        "api_url": reverse("cea:market_data"),
        "models_url": reverse("cea:models"),
    }
