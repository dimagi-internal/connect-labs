"""Per-state model inputs for EMOD: prevalence, incidence, under-5 population and the rain season.

The PMC explorer reads one state table from the indicator registry (``pmc.state_rows``). This
module reads the same values through the same ``BulkResolver`` and adds what a state-specific EMOD
run needs and the explorer never showed: the twelve monthly rainfall means (carried in the
``extra['monthly_mm']`` of the state's ``rain_peak_month`` row), turned into a habitat curve for the
worker and an onset month for the delivery rounds.
"""

from __future__ import annotations

from datetime import date

MONTHS = 12
LAG_MONTHS = 1  # mosquito habitat follows rain by about a month (larval development)
HABITAT_FLOOR = 0.1  # dry months keep low perennial transmission
ONSET_WINDOW_MONTHS = 4  # the "rounds" window: four consecutive months from the first of the wettest run

#: Mid-month day of year (non-leap), the habitat knots: 15, 46, 74, ...
_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
MID_MONTH_DOY = [sum(_MONTH_DAYS[:i]) + 15 for i in range(MONTHS)]


def _check_rain(rain_monthly) -> list[float]:
    if rain_monthly is None or len(rain_monthly) != MONTHS:
        raise ValueError("rain_monthly must hold exactly 12 monthly means, Jan..Dec")
    rain = [float(v) for v in rain_monthly]
    if any(v != v or v < 0 for v in rain):
        raise ValueError("rain_monthly must be non-negative numbers")
    return rain


def habitat_curve(rain_monthly) -> tuple[list[int], list[float]]:
    """(times, values) for ``setting['habitat_times'/'habitat_values']``.

    Knots sit at mid-month day of year. Each month's value is the PREVIOUS month's rain (the larval
    lag), scaled so the maximum is 1.0, then floored at 0.1. All-zero rain gives a flat floor curve.
    """
    rain = _check_rain(rain_monthly)
    lagged = [rain[(m - LAG_MONTHS) % MONTHS] for m in range(MONTHS)]
    peak = max(lagged)
    scaled = [v / peak if peak > 0 else 0.0 for v in lagged]
    return list(MID_MONTH_DOY), [round(max(v, HABITAT_FLOOR), 4) for v in scaled]


def onset_month(rain_monthly) -> int:
    """1-12: the first month of the wettest run of four consecutive months (wrapping the year)."""
    rain = _check_rain(rain_monthly)
    sums = [sum(rain[(s + k) % MONTHS] for k in range(ONSET_WINDOW_MONTHS)) for s in range(MONTHS)]
    return sums.index(max(sums)) + 1


def wettest_quarter_share(rain_monthly) -> float:
    """Percent of the year's rain in the wettest three consecutive months."""
    rain = _check_rain(rain_monthly)
    total = sum(rain)
    if total <= 0:
        return 0.0
    return round(100 * max(sum(rain[(s + k) % MONTHS] for k in range(3)) for s in range(MONTHS)) / total, 1)


def state_inputs(names: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Per-state inputs for every Nigerian state (or just ``names``), plus the states skipped.

    Returns ``(states, skipped)``. A state missing any input is omitted from ``states`` and listed
    in ``skipped`` as ``{"name", "reason"}``. Each state is
    ``{name, pfpr_target, incidence_per_1000, pop_u5, rain_monthly, rain_wettest_quarter, onset_month}``.
    """
    from connect_labs.labs.indicators import boundaries as boundary_set
    from connect_labs.labs.indicators.pmc import ISO
    from connect_labs.labs.indicators.resolve import BulkResolver

    units = list(boundary_set.owned().filter(iso_code=ISO, admin_level=1).order_by("name"))
    skipped: list[dict] = []
    if names is not None:
        wanted = {n.strip().lower() for n in names}
        have = {u.name.lower() for u in units}
        skipped += [{"name": n, "reason": "no such state boundary"} for n in names if n.strip().lower() not in have]
        units = [u for u in units if u.name.lower() in wanted]
    bulk = BulkResolver(units)

    states = []
    for b in units:
        got = {c: bulk.get(c, b) for c in ("malaria_prevalence", "malaria_incidence", "pop_u5", "rain_peak_month")}
        missing = [c for c, r in got.items() if r is None]
        if missing:
            skipped.append({"name": b.name, "reason": "no value for " + ", ".join(missing)})
            continue
        rain_row = got["rain_peak_month"]
        if rain_row.measured_at is not None and rain_row.measured_at.pk != b.pk:
            skipped.append({"name": b.name, "reason": "rainfall inherited from a coarser unit, not measured here"})
            continue
        monthly = (rain_row.extra or {}).get("monthly_mm")
        try:
            rain = _check_rain(monthly)
        except ValueError:
            skipped.append({"name": b.name, "reason": "rain_peak_month carries no 12-month monthly_mm profile"})
            continue
        states.append(
            {
                "name": b.name,
                "pfpr_target": round(got["malaria_prevalence"].value / 100, 4),
                "incidence_per_1000": round(got["malaria_incidence"].value, 1),
                "pop_u5": round(got["pop_u5"].value),
                "rain_monthly": [round(v, 2) for v in rain],
                "rain_wettest_quarter": wettest_quarter_share(rain),
                "onset_month": onset_month(rain),
            }
        )
    return states, skipped


def build_document(states: list[dict], skipped: list[dict]) -> dict:
    return {
        "version": 1,
        "generated": date.today().isoformat(),
        "sources": "CHIRPS rainfall, MAP prevalence and incidence, DHS, WorldPop under-5 population",
        "states": states,
        "skipped": skipped,
    }
