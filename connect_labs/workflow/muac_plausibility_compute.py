"""MUAC-for-age plausibility: classify each MUAC reading and roll the results up.

Used by the ``muac_plausibility`` template (Program 217, CHC Nigeria RCT). Every
threshold lives in the constants below or in ``muac_who_reference`` (the WHO 2007
MUAC-for-age standard, month by month), not inline.

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
   * **Tier A (hard implausible)** -- below the 9 cm floor, or above WHO +3 SD for
     the child's completed month of age and sex. About 1 healthy child in 750 is
     above +3 SD, and 9 cm is 4-5 SD below the median at every age, so these are
     near-certain entry errors, not a clinical signal. This, and only this, is the
     headline "% implausible".
   * **Tier B (below WHO -2SD)** -- below WHO -2 SD for the child's month and sex
     but above the floor. This is where real MAM/SAM lives, so it is reported
     separately and must never be folded into the implausible rate.

Tier A and Tier B are separate functions on purpose: merging them would make real
malnutrition look like a data problem.

Separately, every MUAC value between 9.5 and 21.5 cm is counted into twelve 1 cm
bins (``MUAC_BIN_EDGES``), whatever the child's age. The page runs the six MUAC
distribution checks of Neal's MLFeatureAggregationReport (the "MUAC index") on
those bins, so they are only counted here, never judged.

Reference: WHO Child Growth Standards, arm circumference-for-age (2007); see
``muac_who_reference``. The first version used de Onis, Yip & Mei (1997) at six
reference ages with fixed band ceilings, which held an older child in a band to
as little as +2 SD.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from connect_labs.workflow.muac_who_reference import WHO_ACFA

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

# "Too high" is above WHO +3 SD for the child's completed month of age and sex.
TIER_A_HIGH_Z = 3.0

# The flat comparison band (Ibadan reliability study's observed 9-20 cm range),
# kept as a toggle on the page to compare against the WHO-by-month ceiling.
FLAT_CEILING_CM = 20.0

# ----------------------------------------------------------------- unit errors

# Millimetres typed into the centimetre field: 145 for 14.5.
UNIT_ERROR_MIN = 90.0
UNIT_ERROR_MAX = 200.0

# ------------------------------------------------------------------------ Tier B

# "Below WHO -2SD" is below -2 SD for the child's completed month of age and sex.
TIER_B_Z = -2.0

# ------------------------------------------------------------ MUAC index bins

# Neal's MLFeatureAggregationReport bins: twelve 1 cm bins, [9.5, 10.5) ... [20.5, 21.5).
MUAC_BIN_EDGES = (9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5, 20.5, 21.5)
MUAC_BINS = tuple(f"muac_bin_{i}" for i in range(len(MUAC_BIN_EDGES) - 1))

# Cell counters, in the order they are stored in the snapshot.
COUNTERS = (
    "records",  # every approved Health Service Delivery visit in the cell
    "missing_age",
    "missing_muac",
    "out_of_age_range",
    "valid",  # the Tier A denominator
    "unit_error",
    "tier_a_low",
    "tier_a_high",  # above WHO +3 SD for month and sex (the default)
    "tier_a_high_flat",  # above the flat 20 cm ceiling (comparison)
    "tier_b_assessed",  # valid, not Tier A, sex known (so a WHO -2 SD value applies)
    "tier_b",
    *MUAC_BINS,  # MUAC index histogram: every 9.5-21.5 cm value, any age
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


def who_cutoff(age: float, sex: str, z: float) -> float | None:
    """The MUAC (cm) at z standard deviations for this completed month of age and
    sex, from WHO's L, M and S: M * (1 + L*S*z) ** (1/L). None outside 3-60 months."""
    lms = WHO_ACFA.get(sex, {}).get(int(age))
    if lms is None:
        return None
    L, M, S = lms[:3]
    return M * (1 + L * S * z) ** (1 / L)


def tier_a_ceiling(age: float, sex: str | None) -> float | None:
    """WHO +3 SD for the child's month and sex. Without a sex, the lower (more
    conservative) of the two sexes' values."""
    if sex in WHO_ACFA:
        return who_cutoff(age, sex, TIER_A_HIGH_Z)
    values = [v for v in (who_cutoff(age, s, TIER_A_HIGH_Z) for s in WHO_ACFA) if v is not None]
    return min(values) if values else None


def who_minus_2sd(age: float, sex: str | None) -> float | None:
    if sex not in WHO_ACFA:
        return None
    return who_cutoff(age, sex, TIER_B_Z)


def is_unit_error(muac: float) -> bool:
    return UNIT_ERROR_MIN <= muac <= UNIT_ERROR_MAX


def tier_a(muac: float, age: float, sex: str | None, *, flat: bool = False) -> str | None:
    """'low', 'high' or None. Hard-implausible only; never call this for a unit error."""
    if muac < TIER_A_FLOOR_CM:
        return "low"
    ceiling = FLAT_CEILING_CM if flat else tier_a_ceiling(age, sex)
    if ceiling is not None and muac > ceiling:
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


def muac_bin(muac: float | None) -> str | None:
    """The MUAC index bin a value falls in, or None outside [9.5, 21.5)."""
    if muac is None:
        return None
    for i in range(len(MUAC_BIN_EDGES) - 1):
        if MUAC_BIN_EDGES[i] <= muac < MUAC_BIN_EDGES[i + 1]:
            return MUAC_BINS[i]
    return None


def classify(muac_raw, age: float | None, sex: str | None) -> dict:
    """Every counter one reading contributes to (each 0 or 1)."""
    out = dict.fromkeys(COUNTERS, 0)
    out["records"] = 1
    # The MUAC index histogram counts every in-range value, whatever the age or
    # tier, the way MLFeatureAggregationReport does.
    bin_name = muac_bin(parse_number(muac_raw))
    if bin_name:
        out[bin_name] = 1
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

    banded = tier_a(muac, age, sex)
    if banded == "low":
        out["tier_a_low"] = 1
    elif banded == "high":
        out["tier_a_high"] = 1
    if tier_a(muac, age, sex, flat=True) == "high":
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
    traced back to the rule that produced it, and the page can explain it."""

    def band_range(z, low, high):
        out = {}
        for sex in WHO_ACFA:
            values = [who_cutoff(m, sex, z) for m in range(low, high + 1)]
            out[sex] = [round(min(values), 1), round(max(values), 1)]
        return out

    return {
        "method": "who2007_monthly",
        "floor_cm": TIER_A_FLOOR_CM,
        "high_z": TIER_A_HIGH_Z,
        "tier_b_z": TIER_B_Z,
        "flat_ceiling_cm": FLAT_CEILING_CM,
        "unit_error_range": [UNIT_ERROR_MIN, UNIT_ERROR_MAX],
        "age_bands": [{"key": k, "label": label, "low": lo, "high": hi} for k, label, lo, hi in AGE_BANDS],
        # Per band: the lowest and highest +3 SD / -2 SD over its months, by sex.
        "ceiling_range": {k: band_range(TIER_A_HIGH_Z, lo, hi) for k, _l, lo, hi in AGE_BANDS},
        "minus_2sd_range": {k: band_range(TIER_B_Z, lo, hi) for k, _l, lo, hi in AGE_BANDS},
        "muac_bin_edges": list(MUAC_BIN_EDGES),
        "reference": "WHO Child Growth Standards, arm circumference-for-age (2007)",
    }
