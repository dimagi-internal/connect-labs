"""How far a plan can be trusted: re-run the ranking with each place's mortality redrawn within its interval.

A plan fills the places with the highest multiple first, and a multiple rests on a place's mortality, which is known
only to within an interval. So the question is not "which places are best" but "how often would these places still be
chosen if the figures were a little different, and what would the plan buy then". ``assess`` draws each place's
mortality error (a part every territory of a country shares, because they all lean on the same national estimate, and a
part that is the territory's own), moves its multiple and deaths per person by the elasticity the model gave
(``market.size`` measures it), fills the same target, and reports:

* ``p_in_plan`` per place: the share of draws in which it is part of the plan;
* ``overlap``: the share of the shown plan's people who are also in a draw's own best plan, on average;
* ``deaths``: the shown plan's deaths averted at the 5th, 50th and 95th percentile of the draws.

Pure and deterministic: the same rows always give the same answer (a fixed seed).
"""

from __future__ import annotations

import math
import random

DRAWS = 400
SEED = 20261117


def _quantile(sorted_values: list[float], q: float) -> float:
    i = q * (len(sorted_values) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (i - lo)


def _fill(order: list[tuple[str, float, float]], target: float) -> dict[str, float]:
    """``order`` is (key, multiple, capacity), best first; the people taken at each place to reach ``target``."""
    taken, remaining = {}, float(target)
    for key, _, capacity in order:
        if remaining <= 0:
            break
        take = min(capacity, remaining)
        taken[key] = take
        remaining -= take
    return taken


def assess(rows: list[dict], target: float, draws: int = DRAWS, seed: int = SEED) -> dict | None:
    """The plan's reliability, or None when no place has an uncertainty to redraw.

    ``rows`` are ``market.size`` rows: ``key``, ``iso3``, ``units`` (reachable), ``multiple``, ``deaths_per_person``
    and
    the uncertainty fields ``risk_se``, ``risk_shared_se`` and the elasticities ``multiple_elasticity`` and
    ``deaths_elasticity`` (how many per cent the multiple and the deaths per person move for one per cent of risk).
    """
    able = [r for r in rows if r["units"] and r["multiple"] is not None]
    if not any(r.get("risk_se") for r in able):
        return None
    shown = _fill(
        [
            (r["key"], r["multiple"], r["units"])
            for r in sorted(able, key=lambda r: (-r["multiple"], -r["units"], r["name"]))
        ],
        target,
    )
    rng = random.Random(seed)
    in_plan = {r["key"]: 0 for r in able}
    overlap, deaths = [], []
    for _ in range(draws):
        shared = {}
        eps = {}
        for r in able:
            se, sh = r.get("risk_se") or 0.0, r.get("risk_shared_se") or 0.0
            if sh:
                shared.setdefault(r["iso3"], rng.gauss(0, 1))
            own = math.sqrt(max(se * se - sh * sh, 0.0))
            eps[r["key"]] = sh * shared.get(r["iso3"], 0.0) + own * rng.gauss(0, 1)
        moved = {
            r["key"]: (
                r["multiple"] * math.exp((r.get("multiple_elasticity") or 0.0) * eps[r["key"]]),
                r["deaths_per_person"] * math.exp((r.get("deaths_elasticity") or 0.0) * eps[r["key"]]),
            )
            for r in able
        }
        best = _fill(
            sorted(
                ((r["key"], moved[r["key"]][0], r["units"]) for r in able),
                key=lambda t: (-t[1], -t[2], t[0]),
            ),
            target,
        )
        for k in best:
            in_plan[k] += 1
        people = sum(shown.values())
        overlap.append(sum(min(v, best.get(k, 0.0)) for k, v in shown.items()) / people if people else 0.0)
        deaths.append(sum(v * moved[k][1] for k, v in shown.items()))
    deaths.sort()
    return {
        "draws": draws,
        "p_in_plan": {k: n / draws for k, n in in_plan.items()},
        "overlap": sum(overlap) / draws,
        "deaths": {"p05": _quantile(deaths, 0.05), "p50": _quantile(deaths, 0.5), "p95": _quantile(deaths, 0.95)},
    }
