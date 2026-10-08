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

#: The indicators the state table shows, in column order.
STATE_INDICATORS = ("malaria_prevalence", "rain_wettest_quarter", "zero_dose", "dpt3_vaccination", "pop_u5")

CAVEATS = (
    "One simulated setting, fitted to no real state: treat every figure as illustrative.",
    "State prevalence is DHS 2021 (rapid test, children 6-59 months); the model's is PfPR in 2-5 year olds. "
    "They are close but not the same measure.",
    "Per-state projections assume the state behaves like the modelled setting, and are shown only where its "
    "prevalence is within 10 points of the model's. Only a state-calibrated EMOD run can say how a schedule "
    "performs there.",
    "Children 3-24 months are estimated as 21/60 of the under-5 population.",
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


def fit_for(prevalence: float | None) -> str:
    """Whether a state's measured prevalence is near the modelled setting's."""
    if prevalence is None:
        return "unknown"
    modelled = load_sweep()["setting"]["pfpr_2_5y"] * 100
    return "near" if abs(prevalence - modelled) <= FIT_TOLERANCE_PTS else "outside"


def approx(n: float, figures: int = 2) -> int:
    """Round to significant figures. A projection from an uncalibrated model quoted to
    the unit (332,465 cases) reads as a measurement; 330,000 reads as what it is."""
    if not n:
        return 0
    return int(float(f"{n:.{figures}g}"))


def project(children: float | None, schedule: dict, per_dose: float) -> dict | None:
    """One year of a schedule in a population of this size, IF it behaved like the modelled setting."""
    if not children:
        return None
    doses = children * schedule["doses_per_child_per_year"]
    return {
        "cases_averted_per_year": approx(children * schedule["cases_averted_per_1000_children_per_year"] / 1000),
        "doses_per_year": approx(doses),
        "spend_per_year": approx(doses * per_dose),
    }


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
    rows = []
    for b in units:
        values, sources = {}, {}
        for code in STATE_INDICATORS:
            r = bulk.get(code, b)
            values[code] = round(r.value, 1) if r else None
            sources[code] = (r.source_ref or r.source) if r else None
        children = values["pop_u5"] * SHARE_OF_U5_AGED_3_24M if values["pop_u5"] else None
        fit = fit_for(values["malaria_prevalence"])
        rows.append(
            {
                "pk": b.pk,
                "name": b.name,
                **{k: v for k, v in values.items() if k != "pop_u5"},
                "pop_u5": round(values["pop_u5"]) if values["pop_u5"] else None,
                "children_3_24m": round(children) if children else None,
                "fit": fit,
                # Only where the state looks like the modelled setting. Projecting a 44%
                # setting's incidence onto a 4% state would invent most of the cases.
                "projection": project(children, schedule, per_dose) if fit == "near" else None,
                "sources": sources,
            }
        )
    # Highest burden first: the shortlist a proposal starts from.
    rows.sort(key=lambda r: -(r["malaria_prevalence"] or -1))
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
