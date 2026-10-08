"""The per-state intervention design grid (PMC and SMC schedules) the EMOD batch runs and the ranking prices.

Pure Python with no Django import, so the batch driver and Django can both load it by path.

``designs_for(state_inputs)`` takes one entry of ``inputs.json`` ``states`` and returns the designs to
run in that state. Each design's ``rounds`` use the worker's format
``[offset_days, interval_days, reps, age_min_y, age_max_y, coverage]``, with offsets relative to the start
of the intervention period (day 0 = 1 January, the calendar of ``live.month_offset``). Like the live
runs, a schedule is one round-group per intervention year (offset + 365 * year), ``reps`` counting the
rounds of that group only. A group may run past the end of its calendar year (a Jul start with 8 monthly
rounds ends in Feb); the sim stops at the end of the last intervention year, so a late group is cut to
the rounds that start before then.
"""

from __future__ import annotations

DAYS_PER_YEAR = 365
INTERVENTION_YEARS = 2
COVERAGE = 0.85
PMC_AGES_Y = (3 / 12, 24 / 12)
SMC_AGES_Y = (3 / 12, 59 / 12)
#: Shares of the under-5 population (in 60 months) each target age band makes up.
PMC_TARGET_FRACTION = 21 / 60
SMC_TARGET_FRACTION = 56 / 60
#: SMC only where the rain is seasonal enough: share of the year's rain in the wettest quarter, percent.
SMC_MIN_WETTEST_QUARTER = 60

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_offset(month: int) -> int:
    """Day of the year a calendar month starts (same rule as connect_labs ... emod/live.py month_offset)."""
    return round((month - 1) * DAYS_PER_YEAR / 12)


def _groups(first_offset: int, interval: int, reps: int, ages: tuple, coverage: float) -> list[list[float]]:
    """One group per intervention year, each cut to the rounds that start before the sim ends."""
    end = INTERVENTION_YEARS * DAYS_PER_YEAR
    out = []
    for y in range(INTERVENTION_YEARS):
        offset = y * DAYS_PER_YEAR + first_offset
        fit = min(reps, (end - 1 - offset) // interval + 1)
        if fit >= 1:
            out.append([offset, interval, fit, ages[0], ages[1], coverage])
    return out


def _span(first_month: int, n: int) -> str:
    last = (first_month - 1 + n - 1) % 12
    return f"{MONTH_ABBR[first_month - 1]}–{MONTH_ABBR[last]}"


def _design(code, label, kind, first_offset, interval, reps, ages, fraction) -> dict:
    return {
        "code": code,
        "label": label,
        "kind": kind,
        "rounds": _groups(first_offset, interval, reps, ages, COVERAGE),
        "target_pop_fraction": fraction,
    }


def designs_for(state_inputs: dict) -> list[dict]:
    """The designs to run for one state: PMC schedules, plus SMC when the state's rain is seasonal."""
    onset = int(state_inputs["onset_month"])
    if not 1 <= onset <= 12:
        raise ValueError(f"onset_month must be 1-12, got {onset}")
    start = month_offset(onset)
    mon = MONTH_ABBR[onset - 1]
    age_txt = "3–24 months"
    out = []
    for n in (4, 6, 8):
        out.append(
            _design(
                f"pmc_m{n}_onset",
                f"{n} monthly rounds, {_span(onset, n)}, {age_txt}",
                "pmc",
                start,
                30,
                n,
                PMC_AGES_Y,
                PMC_TARGET_FRACTION,
            )
        )
    out.append(_design("pmc_m12", f"Year-round monthly, {age_txt}", "pmc", 0, 30, 12, PMC_AGES_Y, PMC_TARGET_FRACTION))
    out.append(
        _design("pmc_q4", f"Quarterly from {mon}, {age_txt}", "pmc", start, 91, 4, PMC_AGES_Y, PMC_TARGET_FRACTION)
    )
    out.append(
        _design(
            "pmc_b6", f"Every two months from {mon}, {age_txt}", "pmc", start, 61, 6, PMC_AGES_Y, PMC_TARGET_FRACTION
        )
    )
    if state_inputs.get("rain_wettest_quarter", 0) >= SMC_MIN_WETTEST_QUARTER:
        out.append(
            _design(
                "smc_m4_onset",
                f"SMC: 4 monthly rounds, {_span(onset, 4)}, 3–59 months",
                "smc",
                start,
                30,
                4,
                SMC_AGES_Y,
                SMC_TARGET_FRACTION,
            )
        )
    return out
