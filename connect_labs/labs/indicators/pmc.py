"""Perennial malaria chemoprevention (PMC): which schedule, and where.

The question a PMC proposal has to answer is not only "how many children" but
"how often, and when". IDM's EMOD model can compare schedules; Connect can
deliver any of them and verify every dose. This module joins the two:

* ``data/pmc_emod_sweep.json`` holds a precomputed EMOD sweep -- six delivery
  schedules, six random seeds, in ONE uncalibrated southern-Nigeria-like
  setting. EMOD is not run here: a sweep takes about twenty minutes in Docker
  (``tools/pmc_emod/``), so the page and the agent read its results.
* The costs are recomputed per request from the caller's own visit price, so
  the cost per case averted moves with the proposal's numbers.
* The state table is live registry data (DHS malaria prevalence, rainfall
  seasonality, zero-dose and DPT3), the same values the targeting map shows.

What this is NOT, and every answer says so: a calibrated estimate for any real
state. The setting is fitted to nothing; ``fit`` only says whether a state's
measured prevalence is near the modelled one. The per-state projection is
"if this state behaved like the modelled setting" -- a way to size the
question, not an answer to it.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

SWEEP_PATH = Path(__file__).parent / "data" / "pmc_emod_sweep.json"

#: The country the sweep's setting stands in for.
ISO = "NGA"

#: Months of age the Connect schedules cover (3-24), as a share of the under-5
#: population. Assumes ages are spread evenly across 0-59 months, which slightly
#: understates infants in a young population -- the estimate is labelled as one.
SHARE_OF_U5_AGED_3_24M = 21 / 60

#: How far, in percentage points, a state's measured prevalence may sit from the
#: modelled setting's before its projection is flagged as out of range.
FIT_TOLERANCE_PTS = 10.0

#: The same for seasonality: the share of a year's rain in the wettest quarter
#: against the modelled habitat's share in its peak quarter. Prevalence alone
#: is not enough -- Kano and Sokoto carry southern-level prevalence but take
#: three-quarters of their rain in one season, which is SMC country, and a
#: perennial-transmission model says nothing about them.
SEASONALITY_TOLERANCE_PTS = 8.0

#: The indicators the state table shows, in column order.
STATE_INDICATORS = (
    "malaria_prevalence",
    "malaria_incidence",
    "rain_wettest_quarter",
    "zero_dose",
    "dpt3_vaccination",
    "pop_u5",
)

#: Fits whose states are ranked. A state more (or less) seasonal than the modelled
#: setting is the wrong intervention or the wrong model, so it is never ranked; a
#: state whose prevalence differs is ranked at lower confidence.
RANKED_FITS = ("near", "prevalence_differs")

CAVEATS = (
    "One simulated setting, fitted to no real state: treat every figure as illustrative.",
    "State prevalence is DHS 2021 (rapid test, children 6-59 months); the model's is PfPR in 2-5 year olds. "
    "They are close but not the same measure.",
    "States are projected only where their rainfall seasonality is within 8 points of the model's; where "
    "prevalence also differs by more than 10 points the projection is lower confidence. Only a "
    "state-calibrated EMOD run can say how a schedule performs there.",
    "The Sahel north is far more seasonal than the modelled setting; there seasonal malaria chemoprevention "
    "(SMC), not PMC, is the standard.",
    "Children 3-24 months are estimated as 21/60 of the under-5 population.",
    "States are ranked by applying each schedule's EMOD effect to the state's own malaria incidence (MAP 2024), "
    "relative to the states that match the model. An approximation until EMOD is fitted per state.",
)


@lru_cache(maxsize=1)
def load_sweep() -> dict:
    with open(SWEEP_PATH) as f:
        return json.load(f)


def cost_per_dose(cost_per_visit: float, platform_fee: float, dose_rate: float) -> float:
    """All-in cost of one dose given: the visit price plus the platform fee, spread over the visits that dose."""
    if not all(math.isfinite(v) for v in (cost_per_visit, platform_fee, dose_rate)):
        raise ValueError("costs must be finite numbers")
    if cost_per_visit < 0 or platform_fee < 0:
        raise ValueError("costs cannot be negative")
    if not 0 < dose_rate <= 1:
        raise ValueError("dose_rate is a share between 0 and 1")
    return cost_per_visit * (1 + platform_fee) / dose_rate


def costs_or_default(cost_per_visit=None, platform_fee=None, dose_rate=None) -> dict:
    d = load_sweep()["default_costs"]
    return {
        "cost_per_visit": d["cost_per_visit"] if cost_per_visit is None else float(cost_per_visit),
        "platform_fee": d["platform_fee"] if platform_fee is None else float(platform_fee),
        "dose_rate": d["dose_rate"] if dose_rate is None else float(dose_rate),
    }


def schedule_rows(costs: dict) -> list[dict]:
    """Every schedule with its outcome and the cost per case averted at these prices."""
    sweep = load_sweep()
    setting = sweep["setting"]
    baseline = setting["baseline_cases"]
    children = setting["children_3_24m"]
    years = setting["intervention_years"]
    per_dose = cost_per_dose(**costs)

    rows = []
    for s in sweep["schedules"]:
        averted = baseline * s["averted_pct"] / 100
        spend = s["doses"] * per_dose
        # An effect smaller than its own uncertainty is not a result to cost.
        # The comparison point itself has nothing to cost.
        noisy = s["code"] != "none" and s["averted_ci"] >= s["averted_pct"]
        rows.append(
            {
                "code": s["code"],
                "label": s["label"],
                "channel": s["channel"],
                "detail": s["detail"],
                "rounds_per_year": s["rounds_per_year"],
                "age_months": s["age_months"],
                "coverage": s["coverage"],
                "averted_pct": s["averted_pct"],
                "averted_ci": s["averted_ci"],
                "too_noisy": noisy,
                "doses": s["doses"],
                "cases_averted": round(averted, 1),
                "spend": round(spend, 2),
                "cost_per_case_averted": None if noisy or averted <= 0 else round(spend / averted, 2),
                # Rates per child per year, for scaling to a real population.
                "cases_averted_per_1000_children_per_year": round(averted / children / years * 1000, 1),
                "doses_per_child_per_year": round(s["doses"] / children / years, 2),
            }
        )
    return rows


def best_schedule(rows: list[dict]) -> dict | None:
    """The cheapest schedule per case averted among those with a real effect."""
    costed = [r for r in rows if r["cost_per_case_averted"] is not None]
    return min(costed, key=lambda r: r["cost_per_case_averted"]) if costed else None


def fit_for(prevalence: float | None, wettest_quarter: float | None = None) -> str:
    """Whether a state looks like the modelled setting -- in burden AND in seasonality.

    Returns ``near``, ``prevalence_differs``, ``more_seasonal``, ``less_seasonal``
    or ``unknown`` (no prevalence, or no rainfall figure to check seasonality).
    """
    setting = load_sweep()["setting"]
    if prevalence is None or wettest_quarter is None:
        return "unknown"
    # Seasonality first: a Sahel state is the wrong intervention (SMC, not PMC)
    # whatever its prevalence, and that is the reason worth showing.
    gap = wettest_quarter - setting["wettest_quarter_pct"]
    if gap > SEASONALITY_TOLERANCE_PTS:
        return "more_seasonal"
    if gap < -SEASONALITY_TOLERANCE_PTS:
        return "less_seasonal"
    if abs(prevalence - setting["pfpr_2_5y"] * 100) > FIT_TOLERANCE_PTS:
        return "prevalence_differs"
    return "near"


def approx(n: float, figures: int = 2) -> int:
    """Round to significant figures. A projection from an uncalibrated model quoted to
    the unit (332,465 cases) reads as a measurement; 330,000 reads as what it is."""
    if not n:
        return 0
    return int(float(f"{n:.{figures}g}"))


def project(children: float | None, schedule: dict, per_dose: float, burden: float = 1.0) -> dict | None:
    """One year of a schedule in a population of this size.

    ``burden`` scales the modelled setting's malaria to this state's own: the
    state's MAP incidence over the reference incidence (see ``reference_incidence``).
    The schedule's RELATIVE effect is EMOD's; the absolute number of cases it
    averts follows the state's burden, and so does the cost per case averted.
    Doses do not depend on malaria, so they do not scale.
    """
    if not children:
        return None
    doses = children * schedule["doses_per_child_per_year"]
    cases = children * schedule["cases_averted_per_1000_children_per_year"] / 1000 * burden
    spend = doses * per_dose
    return {
        "cases_averted_per_year": approx(cases),
        "doses_per_year": approx(doses),
        "spend_per_year": approx(spend),
        "cost_per_case_averted": round(spend / cases, 2) if cases else None,
    }


def reference_incidence(rows: list[dict]) -> float | None:
    """The malaria incidence the modelled setting stands for.

    EMOD's setting is fitted to no state, so its burden is pinned to the states
    that match it in both prevalence and seasonality: their mean MAP incidence.
    A state's burden factor is then its own incidence over this. Recomputed from
    live data, never hand-maintained.
    """
    near = [r["malaria_incidence"] for r in rows if r["fit"] == "near" and r["malaria_incidence"]]
    return sum(near) / len(near) if near else None


def state_rows(schedule_code: str, costs: dict) -> list[dict]:
    """Nigeria's states with the live registry values and a projection for one schedule."""
    from connect_labs.labs.indicators import boundaries as boundary_set
    from connect_labs.labs.indicators.resolve import BulkResolver

    schedules = {r["code"]: r for r in schedule_rows(costs)}
    if schedule_code not in schedules:
        raise ValueError(f"unknown schedule {schedule_code!r}; known: {sorted(schedules)}")
    schedule = schedules[schedule_code]
    per_dose = cost_per_dose(**costs)

    units = list(boundary_set.owned().filter(iso_code=ISO, admin_level=1).order_by("name"))
    bulk = BulkResolver(units)
    rows, children_of = [], {}
    for b in units:
        values, sources = {}, {}
        for code in STATE_INDICATORS:
            r = bulk.get(code, b)
            values[code] = round(r.value, 1) if r else None
            sources[code] = (r.source_ref or r.source) if r else None
        children = values["pop_u5"] * SHARE_OF_U5_AGED_3_24M if values["pop_u5"] else None
        fit = fit_for(values["malaria_prevalence"], values["rain_wettest_quarter"])
        rows.append(
            {
                "pk": b.pk,
                "name": b.name,
                **{k: v for k, v in values.items() if k != "pop_u5"},
                "pop_u5": round(values["pop_u5"]) if values["pop_u5"] else None,
                "children_3_24m": round(children) if children else None,
                "fit": fit,
                "sources": sources,
            }
        )
        children_of[b.pk] = children

    ref = reference_incidence(rows)
    for r in rows:
        r["burden_factor"] = None
        r["projection"] = None
        r["confidence"] = None
        if r["fit"] not in RANKED_FITS or not ref:
            continue
        # No incidence of its own: the model's burden as is, at lower confidence.
        factor = (r["malaria_incidence"] / ref) if r["malaria_incidence"] else 1.0
        r["burden_factor"] = round(factor, 2)
        r["projection"] = project(children_of[r["pk"]], schedule, per_dose, factor)
        r["confidence"] = "model fit" if r["fit"] == "near" and r["malaria_incidence"] else "lower"

    # Most cost-effective first; the states the model cannot rank follow, highest
    # prevalence first, so the shortlist and the exclusions read top to bottom.
    def order(r):
        cost = (r["projection"] or {}).get("cost_per_case_averted")
        return (0, cost) if cost is not None else (1, -(r["malaria_prevalence"] or -1))

    rows.sort(key=order)
    rank = 0
    for r in rows:
        if r["projection"] and r["projection"]["cost_per_case_averted"] is not None:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None
    return rows


def summary(costs: dict) -> dict:
    """The sweep's provenance, the schedules at these prices, and the caveats -- everything but the states."""
    sweep = load_sweep()
    rows = schedule_rows(costs)
    best = best_schedule(rows)
    return {
        "model": sweep["model"],
        "setting": sweep["setting"],
        "run_date": sweep["run_date"],
        "costs": {
            **costs,
            "cost_per_dose": round(cost_per_dose(**costs), 3),
            "source": sweep["default_costs"]["source"],
        },
        "schedules": rows,
        "best_schedule": best["code"] if best else None,
        "references": sweep["references"],
        "caveats": list(CAVEATS),
    }
