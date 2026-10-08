"""MUAC-for-age plausibility: classify each MUAC reading and roll the results up.

Used by the ``muac_plausibility`` template (Program 217, CHC Nigeria RCT). Every
threshold lives in the constants below, not inline, because they will be refined:
the 6-11 month WHO rows are still outstanding, and the baseline Tier A rate is
expected to move once real data has been looked at.

Each reading falls into exactly one bucket, in this order:

1. **Excluded** -- no usable age, or no usable MUAC value for a child aged 6-59
   months. Counted, never silently dropped: a high exclusion rate is itself a
   data-quality signal.
2. **Out of age range** -- age known but outside 6-59 months. MUAC screening does
   not apply, so these are neither valid nor excluded.
3. **Valid** -- the denominator. Within valid, at most one of:

   * **Unit error** -- 90-200, almost certainly millimetres typed into the
     centimetre field. A different root cause (field design) from an implausible
     reading, so it is kept OUT of Tier A and shown on its own.
   * **Tier A (hard implausible)** -- below the 9 cm floor or above the age-banded
     ceiling: a near-certain entry error, not a clinical signal. This, and only
     this, is the headline "% implausible".
   * **Tier B (below WHO -2SD)** -- low for age and sex but above the floor. This
     is where real MAM/SAM lives, so it is reported separately and must never be
     folded into the implausible rate.

Tier A and Tier B are separate functions on purpose: merging them would make real
malnutrition look like a data problem.

Reference: de Onis, Yip & Mei, "The development of MUAC-for-age reference data
recommended by a WHO Expert Committee", Bull WHO 1997;75(1):11-18.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------------- ages

MIN_AGE_MONTHS = 6
MAX_AGE_MONTHS = 59

# (key, label, lowest month, highest month), inclusive.
AGE_BANDS = (
    ("6-11", "6-11 months", 6, 11),
    ("12-23", "12-23 months", 12, 23),
    ("24-35", "24-35 months", 24, 35),
    ("36-47", "36-47 months", 36, 47),
    ("48-59", "48-59 months", 48, 59),
)
BAND_UNDER_6 = "<6"
BAND_OVER_59 = ">59"
BAND_UNKNOWN = "unknown"

# ------------------------------------------------------------------------ Tier A

# Far below -3SD at every age 6-59 months (roughly -4 to -5SD).
TIER_A_FLOOR_CM = 9.0

# Age-banded ceilings, roughly WHO median + 3SD at the start of each band. Sex is
# recorded on every Health Service Delivery visit, so the 48-59 band, where girls
# run higher, is split by sex; elsewhere the two sexes are within 0.25 cm and share
# a ceiling. None means "not yet set": the 6-11 month WHO rows are still to be
# pulled from the primary source and are deliberately NOT extrapolated, so that
# band falls back to the flat ceiling and the page says so.
TIER_A_CEILING_CM = {
    "6-11": None,
    "12-23": {"male": 17.5, "female": 17.5},
    "24-35": {"male": 18.5, "female": 18.5},
    "36-47": {"male": 19.0, "female": 19.0},
    "48-59": {"male": 20.0, "female": 21.0},
}
# The flat comparison band (Ibadan reliability study's observed 9-20 cm range).
# Also the fallback ceiling for a band whose own ceiling is not yet set.
FLAT_CEILING_CM = 20.0

# ----------------------------------------------------------------- unit errors

# Millimetres typed into the centimetre field: 145 for 14.5.
UNIT_ERROR_MIN = 90.0
UNIT_ERROR_MAX = 200.0

# ------------------------------------------------------------------------ Tier B

# WHO MUAC-for-age -2SD (cm) at each reference age in months, by sex. Linear
# interpolation between reference ages; no value outside 12-60 months, so the
# 6-11 band is not assessed for Tier B until the 6-11 month rows are in.
WHO_MINUS_2SD_CM = {
    "male": ((12, 12.5), (18, 12.8), (24, 13.0), (36, 13.5), (48, 13.8), (60, 14.0)),
    "female": ((12, 12.1), (18, 12.4), (24, 12.7), (36, 13.3), (48, 13.7), (60, 14.0)),
}

# Cell counters, in the order they are stored in the snapshot.
COUNTERS = (
    "records",  # every approved Health Service Delivery visit in the cell
    "missing_age",
    "missing_muac",
    "out_of_age_range",
    "valid",  # the Tier A denominator
    "unit_error",
    "tier_a_low",
    "tier_a_high",  # age-banded ceiling (the default)
    "tier_a_high_flat",  # flat 20 cm ceiling (comparison)
    "tier_b_assessed",  # valid readings with a WHO -2SD value (12-59 months, known sex)
    "tier_b",
)


# ------------------------------------------------------------------- parsing


def parse_number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN


def normalise_sex(value) -> str | None:
    text = str(value or "").strip().lower()
    if text in ("male", "m", "boy"):
        return "male"
    if text in ("female", "f", "girl"):
        return "female"
    return None


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def age_in_months(age_months, dob, visit_at: datetime | None) -> float | None:
    """The child's age at the visit.

    The form's own ``childs_age_in_months`` is computed on the device at the visit,
    so it is preferred. Failing that, the date of birth against the visit date --
    never against today, which would age every child by the time since the visit.
    """
    age = parse_number(age_months)
    if age is not None:
        return age if age >= 0 else None
    if not dob or visit_at is None:
        return None
    try:
        born = date.fromisoformat(str(dob)[:10])
    except ValueError:
        return None
    visit_day = visit_at.date()
    if born > visit_day:
        return None
    months = (visit_day.year - born.year) * 12 + (visit_day.month - born.month)
    if visit_day.day < born.day:
        months -= 1
    return float(months)


def age_band(age: float | None) -> str:
    if age is None:
        return BAND_UNKNOWN
    whole = int(age)
    if whole < MIN_AGE_MONTHS:
        return BAND_UNDER_6
    if whole > MAX_AGE_MONTHS:
        return BAND_OVER_59
    for key, _label, low, high in AGE_BANDS:
        if low <= whole <= high:
            return key
    return BAND_UNKNOWN


# ------------------------------------------------------------------ thresholds


def tier_a_ceiling(band: str, sex: str | None) -> float:
    """The age-banded ceiling. Without a sex, the lower (more conservative) of the
    two sexes' ceilings; without a ceiling for the band, the flat one."""
    by_sex = TIER_A_CEILING_CM.get(band)
    if not by_sex:
        return FLAT_CEILING_CM
    if sex in by_sex:
        return by_sex[sex]
    return min(by_sex.values())


def who_minus_2sd(age: float, sex: str | None) -> float | None:
    points = WHO_MINUS_2SD_CM.get(sex or "")
    if not points or age < points[0][0] or age > points[-1][0]:
        return None
    for (age_lo, v_lo), (age_hi, v_hi) in zip(points, points[1:]):
        if age_lo <= age <= age_hi:
            return v_lo + (v_hi - v_lo) * (age - age_lo) / (age_hi - age_lo)
    return None


def is_unit_error(muac: float) -> bool:
    return UNIT_ERROR_MIN <= muac <= UNIT_ERROR_MAX


def tier_a(muac: float, band: str, sex: str | None, *, flat: bool = False) -> str | None:
    """'low', 'high' or None. Hard-implausible only; never call this for a unit error."""
    if muac < TIER_A_FLOOR_CM:
        return "low"
    ceiling = FLAT_CEILING_CM if flat else tier_a_ceiling(band, sex)
    if muac > ceiling:
        return "high"
    return None


def tier_b(muac: float, age: float, sex: str | None) -> bool | None:
    """True when below WHO -2SD for age and sex (and not Tier A); None when there is
    no reference value to compare against."""
    threshold = who_minus_2sd(age, sex)
    if threshold is None:
        return None
    return TIER_A_FLOOR_CM <= muac < threshold


# ------------------------------------------------------------------ classifier


def classify(muac_raw, age: float | None, sex: str | None) -> dict:
    """Every counter one reading contributes to (each 0 or 1)."""
    out = dict.fromkeys(COUNTERS, 0)
    out["records"] = 1
    band = age_band(age)
    if band == BAND_UNKNOWN:
        out["missing_age"] = 1
        return out
    if band in (BAND_UNDER_6, BAND_OVER_59):
        out["out_of_age_range"] = 1
        return out
    muac = parse_number(muac_raw)
    if muac is None:
        out["missing_muac"] = 1
        return out

    out["valid"] = 1
    if is_unit_error(muac):
        out["unit_error"] = 1
        return out

    banded = tier_a(muac, band, sex)
    if banded == "low":
        out["tier_a_low"] = 1
    elif banded == "high":
        out["tier_a_high"] = 1
    if tier_a(muac, band, sex, flat=True) == "high":
        out["tier_a_high_flat"] = 1

    # Tier B is only for readings that are not hard-implausible.
    if banded is None:
        below = tier_b(muac, age, sex)
        if below is not None:
            out["tier_b_assessed"] = 1
            out["tier_b"] = int(below)
    return out


# ----------------------------------------------------------------- aggregation


def week_start(visit_at: datetime, utc_offset: timedelta) -> str:
    """The Monday (local calendar) of the visit's week, ISO."""
    local_day = (visit_at + utc_offset).date()
    return (local_day - timedelta(days=local_day.weekday())).isoformat()


def aggregate(rows, *, form_name: str, utc_offset: timedelta) -> dict:
    """Roll visit rows up into cells keyed (opportunity, ward, FLW, week, age band).

    Returns ``{"columns": [...], "rows": [[...], ...], "sex_missing": int}`` -- a
    compact table, because the snapshot holds one row per cell rather than one per
    visit (Program 217 has a quarter of a million visits).
    """
    cells: dict[tuple, list[int]] = defaultdict(lambda: [0] * len(COUNTERS))
    sex_missing = 0
    for row in rows:
        if row.get("form_name") != form_name:
            continue
        visit_at = _parse_dt(row.get("time_start"))
        if visit_at is None:
            continue
        sex = normalise_sex(row.get("sex"))
        age = age_in_months(row.get("age_months"), row.get("childs_dob"), visit_at)
        result = classify(row.get("muac_cm"), age, sex)
        if result["valid"] and sex is None:
            sex_missing += 1
        key = (
            row.get("opportunity_id"),
            row.get("ward") or "",
            row.get("username") or "",
            week_start(visit_at, utc_offset),
            age_band(age),
        )
        counts = cells[key]
        for i, name in enumerate(COUNTERS):
            counts[i] += result[name]

    columns = ["opportunity_id", "ward", "username", "week", "age_band", *COUNTERS]
    table = [list(key) + counts for key, counts in sorted(cells.items(), key=lambda kv: tuple(map(str, kv[0])))]
    return {"columns": columns, "rows": table, "sex_missing": sex_missing}


def thresholds_summary() -> dict:
    """The thresholds in force, saved with every run so a figure can always be
    traced back to the rule that produced it."""
    return {
        "floor_cm": TIER_A_FLOOR_CM,
        "flat_ceiling_cm": FLAT_CEILING_CM,
        "unit_error_range": [UNIT_ERROR_MIN, UNIT_ERROR_MAX],
        "age_bands": [{"key": k, "label": label, "low": lo, "high": hi} for k, label, lo, hi in AGE_BANDS],
        "ceilings": {band: by_sex for band, by_sex in TIER_A_CEILING_CM.items()},
        "who_minus_2sd": {sex: [list(p) for p in pts] for sex, pts in WHO_MINUS_2SD_CM.items()},
    }
