"""GiveWell's valuation, read once from the vendored Connect models.

Every cost-effectiveness view in Labs turns deaths averted into "a multiple of GiveWell's
benchmark" with the same few numbers: what a death averted is worth, what 1x is worth per
dollar, and the bar a grant has to clear. They live here, read from the vendored models
(``python manage.py effectiveness_vendor`` refreshes them), so the delivered ranking and every
scenario cannot value a death differently, and tests fail if the specs
disagree or a value moves without a deliberate change.

A death averted is valued as the Connect CHC model values it (``units_per_death``): the
GiveWell moral weight (113 units) grossed up for the share of a programme's benefit that
comes from deaths and for effects the model does not count, then adjusted for leveraging and
funging, all taken from that model's own rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from connect_labs.effectiveness import engine

SUMMARY = Path(__file__).resolve().parents[1] / "pulse" / "data" / "cea_models.json"


@dataclass(frozen=True)
class Parameter:
    value: float
    source: str


@lru_cache(maxsize=1)
def _chc() -> dict:
    """The CHC model's rows at its own inputs: where the death valuation is worked out."""
    return engine.run("chc")


@lru_cache(maxsize=1)
def value_per_death() -> float:
    """Units of value for one under-5 death averted, before any adjustment (the moral weight)."""
    return float(engine.defaults("chc")["uv"])


@lru_cache(maxsize=1)
def units_per_death() -> float:
    """Units of value for one under-5 death averted, as the CHC model values it.

    ``vpd`` (the moral weight over mortality's share of benefits, times the excluded-effects
    adjustment) times leveraging and funging: the same three rows ``full_x`` multiplies.
    """
    rows = _chc()
    return float(rows["vpd"] * rows["lev"] * rows["fung"])


@lru_cache(maxsize=1)
def benchmark() -> float:
    """Units of value per dollar that is GiveWell's 1x."""
    return float(engine.defaults("chc")["benchmark"])


@lru_cache(maxsize=1)
def bar() -> float:
    """The multiple of the benchmark a grant has to reach to clear GiveWell's bar."""
    with SUMMARY.open(encoding="utf-8") as f:
        return float(json.load(f)["bar_x"])


MORAL_WEIGHT = Parameter(
    value_per_death(), "Connect CHC model input `uv`: GiveWell moral weight, under-5 death averted (vendored spec)"
)
UNITS_PER_DEATH = Parameter(
    units_per_death(),
    "Connect CHC model: moral weight / mortality share of benefits x excluded effects x leveraging x funging",
)
BENCHMARK = Parameter(benchmark(), "Connect models' input `benchmark`: units of value per $ that is GiveWell's 1x")
BAR = Parameter(bar(), "GiveWell's bar for new grants, as vendored in pulse/data/cea_models.json (`bar_x`)")


def multiple(deaths_averted: float, spend: float) -> float:
    """Multiple of GiveWell's benchmark for ``deaths_averted`` under-5 deaths bought with ``spend`` dollars."""
    return UNITS_PER_DEATH.value * deaths_averted / spend / BENCHMARK.value
