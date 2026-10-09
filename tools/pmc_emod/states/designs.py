"""The per-state intervention design grid (PMC and SMC schedules) the EMOD batch runs and the ranking prices.

Pure Python with no Django import, so the batch driver and Django can both load it by path.

``designs_for(state_inputs)`` takes one entry of ``inputs.json`` ``states`` and returns the designs to
run in that state. Each design's ``rounds`` use the worker's format
``[offset_days, interval_days, reps, age_min_y, age_max_y, coverage]``, with offsets relative to the start
of the intervention period (day 0 = 1 January, the calendar of ``live.month_offset``).

The schedule is PERIODIC: a season is ``reps`` rounds at ``first_offset + k * interval``. Rounds that
fall at or after day 365 belong to the season's tail running into the next calendar year, so they wrap
to ``t - 365``. Every intervention year then gets the identical set of rounds: a tail group (last
season's rounds, at the start of the year) and a head group, so the two years are comparable and the
sim never cuts a season short. A group is a run of rounds ``interval`` days apart.

Labels use nominal month names. The 30-day interval drifts against the calendar by ~0.4 days a round,
so a late round can land a few days into the next month; that is deliberate, not a bug.

Each design carries ``drug``: "SP" for PMC, "SPAQ" for SMC, to pass through as the schedule's ``drug``.
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
    """Per intervention year the same rounds: a tail group wrapped from last season, then a head group."""
    times = [first_offset + k * interval for k in range(reps)]
    head = [t for t in times if t < DAYS_PER_YEAR]
    tail = [t - DAYS_PER_YEAR for t in times if t >= DAYS_PER_YEAR]
    if tail and head:
        assert tail[-1] + interval / 2 < head[0], "wrapped rounds collide with the head of the season"
    assert not tail or tail[-1] < DAYS_PER_YEAR
    out = []
    for y in range(INTERVENTION_YEARS):
        for group in (tail, head):
            if group:
                out.append([y * DAYS_PER_YEAR + group[0], interval, len(group), ages[0], ages[1], coverage])
    return out


def _span(first_month: int, n: int) -> str:
    last = (first_month - 1 + n - 1) % 12
    return f"{MONTH_ABBR[first_month - 1]}–{MONTH_ABBR[last]}"


def _design(code, label, kind, drug, first_offset, interval, reps, ages, fraction) -> dict:
    return {
        "code": code,
        "label": label,
        "kind": kind,
        "drug": drug,
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
                "SP",
                start,
                30,
                n,
                PMC_AGES_Y,
                PMC_TARGET_FRACTION,
            )
        )
    out.append(
        _design("pmc_m12", f"Year-round monthly, {age_txt}", "pmc", "SP", 0, 30, 12, PMC_AGES_Y, PMC_TARGET_FRACTION)
    )
    out.append(
        _design(
            "pmc_q4", f"Quarterly from {mon}, {age_txt}", "pmc", "SP", start, 91, 4, PMC_AGES_Y, PMC_TARGET_FRACTION
        )
    )
    out.append(
        _design(
            "pmc_b6",
            f"Every two months from {mon}, {age_txt}",
            "pmc",
            "SP",
            start,
            61,
            6,
            PMC_AGES_Y,
            PMC_TARGET_FRACTION,
        )
    )
    if state_inputs["rain_wettest_quarter"] >= SMC_MIN_WETTEST_QUARTER:
        out.append(
            _design(
                "smc_m4_onset",
                f"SMC (SPAQ): 4 monthly rounds, {_span(onset, 4)}, 3–59 months",
                "smc",
                "SPAQ",
                start,
                30,
                4,
                SMC_AGES_Y,
                SMC_TARGET_FRACTION,
            )
        )
    return out
