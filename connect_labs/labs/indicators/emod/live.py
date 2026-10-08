"""Turn an agent-style PMC schedule into worker rounds, and a finished run into a costed row.

The agent describes a schedule the way a person would ("four monthly rounds from May, ages 3-24
months"). ``to_rounds`` converts that to the worker's round format
``[offset_days, interval_days, reps, age_min_y, age_max_y, coverage]`` on the same calendar as the
precomputed grid's ``connect_monthly_in_season_3_24`` (day 0 = 1 January, high season from ~1 April),
so a live run and a grid row are directly comparable. ``summarise`` reduces a worker result to the
relative effect (cases averted against the same seed's no-PMC run) that the costing applies to MAP.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics

from connect_labs.labs.indicators import pmc

INTERVENTION_YEARS = 2
DAYS_PER_YEAR = 365
#: Connect's coverage in every grid schedule.
DEFAULT_COVERAGE = 0.85
DEFAULT_AGE_MONTHS = (3, 24)
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
LABEL = "illustrative · live model run"
GRID_LABEL = "illustrative · precomputed model run"

#: Two-sided 95% t-values by degrees of freedom: three seeds is too few for 1.96.
_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26}


def month_offset(month: int) -> int:
    """Day of the year a calendar month starts, by the grid's own calendar (April -> 91, its SEASON_START)."""
    return round((month - 1) * DAYS_PER_YEAR / 12)


def _runs(months: list[int]) -> list[tuple[int, int]]:
    """(first month, length) for each run of consecutive calendar months."""
    out: list[list[int]] = []
    for m in months:
        if out and m == out[-1][0] + out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([m, 1])
    return [(a, n) for a, n in out]


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def to_rounds(spec: dict) -> tuple[list[list[float]], str]:
    """``(rounds, plain-English description)`` for a schedule spec; ValueError when it is malformed.

    Exactly one of ``rounds_per_year``, ``months`` or ``interval_days``:
      rounds_per_year N: a round every round(365/N) days for two years, N x 2 rounds.
      interval_days D:   a round every D days, 730 // D rounds (the grid's own rule).
      months [..]:       one round a month in those calendar months, both years; each run of
                         consecutive months is one block of 30-day rounds starting on the month's start.
    ``age_min_months``/``age_max_months`` default 3 and 24; ``coverage`` defaults to 0.85.
    """
    if not isinstance(spec, dict):
        raise ValueError("schedule must be an object")
    known = {"rounds_per_year", "months", "interval_days", "age_min_months", "age_max_months", "coverage"}
    unknown = sorted(set(spec) - known)
    if unknown:
        raise ValueError(f"unknown schedule field(s): {', '.join(unknown)}")
    given = [k for k in ("rounds_per_year", "months", "interval_days") if spec.get(k) is not None]
    if len(given) != 1:
        raise ValueError("give exactly one of rounds_per_year, months or interval_days")
    age_min = spec.get("age_min_months", DEFAULT_AGE_MONTHS[0])
    age_max = spec.get("age_max_months", DEFAULT_AGE_MONTHS[1])
    coverage = spec.get("coverage", DEFAULT_COVERAGE)
    if not (_is_num(age_min) and _is_num(age_max) and 0 <= age_min and age_max <= 60):
        raise ValueError("age_min_months and age_max_months must satisfy 0 <= min < max <= 60")
    if not (_is_num(coverage) and 0 < coverage <= 1):
        raise ValueError("coverage must be a share above 0 and up to 1")
    # Near-identical asks share a run: ages to whole months, coverage to 5-point steps.
    age_min, age_max = round(age_min), round(age_max)
    coverage = max(0.05, round(round(coverage * 20) / 20, 2))
    if not age_min < age_max:
        raise ValueError("age_min_months and age_max_months must satisfy 0 <= min < max <= 60")
    ages = (age_min / 12, age_max / 12)
    who = f"children {age_min:g}-{age_max:g} months, {coverage:.0%} coverage"

    if "rounds_per_year" in given:
        n = spec["rounds_per_year"]
        if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 24):
            raise ValueError("rounds_per_year must be a whole number from 1 to 24")
        interval = round(DAYS_PER_YEAR / n)
        rounds = [[0, interval, n * INTERVENTION_YEARS, *ages, coverage]]
        what = f"{n} rounds a year (every {interval} days)"
    elif "interval_days" in given:
        d = spec["interval_days"]
        if not (_is_num(d) and 7 <= d <= DAYS_PER_YEAR * INTERVENTION_YEARS):
            raise ValueError("interval_days must be between 7 and 730")
        rounds = [[0, d, max(1, int(DAYS_PER_YEAR * INTERVENTION_YEARS // d)), *ages, coverage]]
        what = f"a round every {d:g} days"
    else:
        months = spec["months"]
        if not (
            isinstance(months, list)
            and months
            and all(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in months)
        ):
            raise ValueError("months must be a list of calendar month numbers, 1-12 (e.g. [5, 6, 7, 8])")
        months = sorted(set(months))
        rounds = [
            [y * DAYS_PER_YEAR + month_offset(first), 30, length, *ages, coverage]
            for y in range(INTERVENTION_YEARS)
            for first, length in _runs(months)
        ]
        names = [MONTH_NAMES[m - 1] for m in months]
        what = f"monthly rounds in {', '.join(names)} each year"
    return rounds, f"{what}; {who}"


def describe_rounds(rounds: list) -> str:
    """Plain-English description of worker rounds (what to_rounds' text says), for a run read back from the DB."""
    if not rounds:
        return "a custom schedule"
    offset, interval, reps, a0, a1, cov = rounds[0]
    who = f"children {round(a0 * 12):g}-{round(a1 * 12):g} months, {cov:.0%} coverage"
    if interval == 30:
        months = []
        for off, _, n, *_rest in (r for r in rounds if r[0] < DAYS_PER_YEAR):
            first = min(range(1, 13), key=lambda m: abs(month_offset(m) - off))
            months += [MONTH_NAMES[(first - 1 + i) % 12] for i in range(int(n))]
        what = f"monthly rounds in {', '.join(months)} each year"
    else:
        what = f"a round every {interval:g} days (about {DAYS_PER_YEAR / interval:.1f} a year)"
    return f"{what}; {who}"


def schedule_code(rounds: list) -> str:
    """Deterministic code for a custom schedule: the same rounds always give the same code (and cache key)."""
    canonical = json.dumps(rounds, separators=(",", ":"))
    return "custom_" + hashlib.sha256(canonical.encode()).hexdigest()[:8]


def grid_match(rounds: list) -> str | None:
    """The precomputed grid schedule these rounds are exactly, if any (so no model run is needed)."""
    for code, grid_rounds in GRID_ROUNDS.items():
        if [list(r) for r in rounds] == [list(r) for r in grid_rounds]:
            return code
    return None


#: The grid's Connect schedules in worker round format. Copied from SCENARIOS in
#: tools/pmc_emod/pmc_sweep.py (tools/ is not importable from the app); test_emod_live.py loads that
#: file and fails if the two drift.
GRID_ROUNDS = {
    "connect_quarterly_3_24": [[0, 91, 8, 0.25, 2.0, 0.85]],
    "connect_bimonthly_3_24": [[0, 61, 11, 0.25, 2.0, 0.85]],
    "connect_monthly_in_season_3_24": [[y * 365 + 91, 30, 6, 0.25, 2.0, 0.85] for y in range(2)],
    "connect_quarterly_12_24": [[0, 91, 8, 1.0, 2.0, 0.85]],
}


def summarise(result: dict, code: str) -> dict:
    """Relative effect of schedule ``code`` from a worker result: percent of cases averted against
    the same seed's no-PMC run, mean and 95% half-range across seeds, and doses per child per year."""
    runs = result.get("runs") or []
    base = {r["seed"]: r for r in runs if r["code"] == "none"}
    mine = {r["seed"]: r for r in runs if r["code"] == code}
    seeds = sorted(set(base) & set(mine))
    if not seeds:
        raise ValueError(f"result has no paired runs for {code!r}")
    pcts = [(1 - mine[s]["cases_3_24m"] / base[s]["cases_3_24m"]) * 100 for s in seeds if base[s]["cases_3_24m"]]
    if not pcts:
        raise ValueError("the no-PMC baseline recorded no cases, so no relative effect can be computed")
    mean = statistics.fmean(pcts)
    half = 0.0
    if len(pcts) > 1:
        half = _T95.get(len(pcts) - 1, 1.96) * statistics.stdev(pcts) / math.sqrt(len(pcts))
    kids = statistics.fmean(mine[s]["kids_3_24m"] for s in seeds)
    doses = statistics.fmean(mine[s]["doses"] for s in seeds)
    return {
        "averted_pct": round(mean, 1),
        "averted_ci": round(half, 1),
        "seeds": len(pcts),
        "doses_per_child_per_year": round(doses / kids / pmc.load_sweep()["setting"]["intervention_years"], 2),
        # An effect no larger than its own uncertainty is not a result to cost (same rule as the grid).
        "too_noisy": len(pcts) < 2 or half >= mean,
    }
