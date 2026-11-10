"""GiveWell's valuation, read once from the vendored Connect models.

Three numbers turn deaths averted into "a multiple of GiveWell's benchmark", and every
cost-effectiveness view in Labs needs them: what a death averted is worth, what 1x is
worth per dollar, and the bar a grant has to clear. They used to be typed again in each
engine (``labs/indicators/cost_effectiveness.py`` for ORS and PMC, ``pulse/data`` for
the delivered ranking). Now they come from one place, so a refresh of the vendored models
(``python manage.py impact_vendor``) moves every view together and a test fails if the
copies ever drift.

What is *not* shared, on purpose: each model's own adjustments to that value. The CHC
model grosses a death up by its mortality share, adds excluded effects, and applies
leveraging and funging (0.93). The ORS chain reproduces GiveWell's published CHAI Bauchi
grant (funging 0.968). The PMC view values a death at the bare figure, deaths only.
Whether PMC and ORS should adopt the CHC model's adjustments is a modelling decision, not
a refactor, so it is kept visible instead of folded in here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from connect_labs.impact import engine

SUMMARY = Path(__file__).resolve().parents[1] / "pulse" / "data" / "cea_models.json"

VALUE_PER_DEATH_SOURCE = "Connect CHC model input `uv` (GiveWell moral weight, under-5 death averted), vendored spec"
BENCHMARK_SOURCE = "Connect models' input `benchmark`: units of value per $ that is GiveWell's 1x (all three specs)"
BAR_SOURCE = "GiveWell's bar for new grants, as vendored in pulse/data/cea_models.json (`bar_x`)"


@lru_cache(maxsize=1)
def value_per_death() -> float:
    """Units of value for one under-5 death averted, before any model's adjustments."""
    return float(engine.defaults("chc")["uv"])


@lru_cache(maxsize=1)
def benchmark() -> float:
    """Units of value per dollar that is GiveWell's 1x."""
    return float(engine.defaults("chc")["benchmark"])


@lru_cache(maxsize=1)
def bar() -> float:
    """The multiple of the benchmark a grant has to reach to clear GiveWell's bar."""
    with SUMMARY.open(encoding="utf-8") as f:
        return float(json.load(f)["bar_x"])
