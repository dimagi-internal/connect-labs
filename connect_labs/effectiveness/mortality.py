"""A place's mortality with an honest range: how far to believe each territory's figure, and how sure a ranking is.

A survey or model gives a territory a number and, if we kept it, a published interval. One thing goes wrong when the number
alone is used to rank places:

* **Noise.** A small territory's figure can be far from the truth (Zimbabwe's modelled Bulawayo is 17.1 with a 90%
  interval of 4.1 to 48.1), and ranking on it is ranking on luck. ``shrink`` pulls each territory toward its
  country's typical value by as much as its own interval says it deserves (empirical Bayes, on the log scale, the
  DerSimonian-Laird estimate of how much territories really differ).
Not done here: subnational estimates are benchmarked to the national estimate of their own release, which is later
revised (Zimbabwe's current national series says 32.2 for 2021, its provinces' 2021 values average 22.6). Putting the
territories back on the current national would need every territory of the country, so it is left until Labs holds the
whole country's set; the plan says which figures carry which interval instead.

``Estimate`` carries a territory's figure with its log-scale standard error. Everything here works on the log scale
because a mortality rate is a positive quantity whose uncertainty is multiplicative, and a bound at or near zero breaks a
log: ``FLOOR`` keeps it positive.

Pure: no Django, no database.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

#: Standard normal quantiles for the two-sided intervals we meet: IGME publishes 90%, DHS 95%.
Z = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600}
#: A published bound at or below this (per 1,000) is raised to it, so a log is defined.
FLOOR = 0.5
#: Too few territories to tell real differences from noise: leave their figures as they are.
MIN_AREAS = 8
#: The least real spread between territories (on the log scale) we will believe, so shrinkage never flattens a country
#: completely just because its published intervals happen to be wide.
MIN_TAU = 0.05


def z_for(level: float) -> float:
    """The normal quantile for an interval of ``level`` coverage (0.90 -> 1.645)."""
    return Z.get(round(level, 2), Z[0.95])


def se_ln(low: float | None, high: float | None, level: float = 0.95) -> float | None:
    """The standard error of ``ln(value)`` implied by a published interval, or None when there is no usable one."""
    if low is None or high is None or high <= 0:
        return None
    low = max(low, FLOOR)
    high = max(high, low * 1.0001)
    return (math.log(high) - math.log(low)) / (2 * z_for(level))


def interval(value: float, se: float, level: float = 0.90) -> tuple[float, float]:
    """``value`` with a multiplicative interval of the given coverage."""
    z = z_for(level)
    return value * math.exp(-z * se), value * math.exp(z * se)


def combine(*ses: float | None) -> float | None:
    """Independent log-scale standard errors added in quadrature; None if none is known."""
    known = [s for s in ses if s is not None]
    return math.sqrt(sum(s * s for s in known)) if known else None


@dataclass(frozen=True)
class Area:
    """One territory's figure going in: its key, value, log-scale standard error (None if unknown) and weight (births)."""

    key: Hashable
    value: float
    se: float | None = None
    weight: float = 1.0


@dataclass(frozen=True)
class Estimate:
    """One territory's figure coming out."""

    value: float
    se: float | None
    raw: float
    #: How much of the figure is the country's typical value: 0 = all its own, 1 = all the country's.
    pull: float = 0.0
    shrunk: bool = False


def shrink(areas: Sequence[Area], *, min_areas: int = MIN_AREAS, min_tau: float = MIN_TAU) -> dict[Hashable, Estimate]:
    """Each territory's figure pulled toward its country's by as much as its own uncertainty warrants.

    On the log scale, with ``y`` the figure and ``s`` its standard error: the spread of real differences between
    territories ``tau`` is estimated from how much more the figures differ than their intervals allow (DerSimonian and
    Laird); the country's typical value is the precision-weighted mean; and each territory is ``B*mean + (1-B)*y`` with
    ``B = s^2 / (s^2 + tau^2)``. A territory with a tight interval keeps its figure; one with a wide interval moves toward
    the country's. Its posterior standard error is ``sqrt(s^2 * tau^2 / (s^2 + tau^2))``. Territories with no interval,
    or a country with fewer than ``min_areas`` that have one, are returned as they came.
    """
    out = {a.key: Estimate(a.value, a.se, a.value) for a in areas}
    usable = [a for a in areas if a.se and a.se > 0 and a.value > 0]
    if len(usable) < min_areas:
        return out
    y = {a.key: math.log(a.value) for a in usable}
    w = {a.key: 1 / (a.se * a.se) for a in usable}
    sw = sum(w.values())
    fixed = sum(w[k] * y[k] for k in y) / sw
    q = sum(w[k] * (y[k] - fixed) ** 2 for k in y)
    c = sw - sum(v * v for v in w.values()) / sw
    tau2 = max((q - (len(usable) - 1)) / c if c > 0 else 0.0, min_tau * min_tau)
    wr = {a.key: 1 / (a.se * a.se + tau2) for a in usable}
    mean = sum(wr[k] * y[k] for k in y) / sum(wr.values())
    for a in usable:
        s2 = a.se * a.se
        b = s2 / (s2 + tau2)
        out[a.key] = Estimate(
            value=math.exp(b * mean + (1 - b) * y[a.key]),
            se=math.sqrt(s2 * tau2 / (s2 + tau2)),
            raw=a.value,
            pull=b,
            shrunk=True,
        )
    return out
