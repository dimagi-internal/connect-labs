from datetime import datetime, timedelta, timezone

import pytest

from connect_labs.workflow.muac_plausibility_compute import (
    COUNTERS,
    age_band,
    age_in_months,
    aggregate,
    classify,
    tier_a_ceiling,
    who_cutoff,
    who_minus_2sd,
)
from connect_labs.workflow.muac_who_reference import WHO_ACFA

WAT = timedelta(hours=1)


def _flags(result):
    return {k for k, v in result.items() if v and k != "records" and not k.startswith("muac_bin_")}


@pytest.mark.parametrize(
    "age,band",
    [(None, "unknown"), (5.9, "<6"), (6, "6-11"), (11, "6-11"), (12, "12-23"), (59, "48-59"), (60, ">59")],
)
def test_age_band(age, band):
    assert age_band(age) == band


def test_lms_reproduces_every_published_who_value():
    """WHO publishes -2, +2 and +3 SD per month; the LMS formula must give them all."""
    for sex, table in WHO_ACFA.items():
        for month, (_l, _m, _s, minus2, plus2, plus3) in table.items():
            for z, published in ((-2, minus2), (2, plus2), (3, plus3)):
                assert round(who_cutoff(month, sex, z), 1) == pytest.approx(published), (sex, month, z)


def test_age_prefers_form_age_then_dob_at_visit_date():
    visit = datetime(2026, 8, 23, 10, tzinfo=timezone.utc)
    assert age_in_months("21", "2020-01-01", visit) == 21
    # DOB is aged to the VISIT, not to today.
    assert age_in_months(None, "2024-10-26", visit) == 21
    assert age_in_months("", "2024-08-24", visit) == 23
    assert age_in_months(None, "2027-01-01", visit) is None
    assert age_in_months(None, None, visit) is None


def test_plausible_reading_is_valid_and_nothing_else():
    assert _flags(classify("15.1", 21, "female")) == {"valid", "tier_b_assessed"}


def test_floor_is_tier_a_low_in_both_ceiling_modes():
    assert _flags(classify("8.9", 30, "male")) == {"valid", "tier_a_low"}


def test_ceiling_is_who_plus_3sd_for_the_month_and_sex():
    # Boys: +3 SD is 18.4 cm at 12 months and 19.1 cm at 23 months.
    assert tier_a_ceiling(12, "male") == pytest.approx(18.38, abs=0.01)
    assert tier_a_ceiling(23, "male") == pytest.approx(19.07, abs=0.01)
    assert "tier_a_high" in _flags(classify("18.5", 12, "male"))
    # The same reading is plausible eleven months later.
    assert "tier_a_high" not in _flags(classify("18.5", 23, "male"))
    # A completed month: 23.9 months is month 23.
    assert tier_a_ceiling(23.9, "male") == tier_a_ceiling(23, "male")


def test_who_ceiling_catches_what_the_flat_one_passes_and_vice_versa():
    # 19.5 at 14 months: over WHO +3 SD (18.5), under the flat 20.
    flags = _flags(classify("19.5", 14, "male"))
    assert "tier_a_high" in flags and "tier_a_high_flat" not in flags
    # A Tier A reading is never also assessed for Tier B.
    assert "tier_b_assessed" not in flags
    # 20.5 for a girl of 50 months: within WHO +3 SD (21.5), over the flat 20.
    assert _flags(classify("20.5", 50, "female")) == {"valid", "tier_b_assessed", "tier_a_high_flat"}


def test_sex_matters_and_unknown_sex_takes_the_lower_ceiling():
    assert tier_a_ceiling(50, "female") > tier_a_ceiling(50, "male")
    assert tier_a_ceiling(50, None) == tier_a_ceiling(50, "male")
    # Unknown sex: still checked for Tier A, never for Tier B.
    assert "tier_b_assessed" not in _flags(classify("15.0", 50, None))


def test_6_to_11_months_is_fully_assessed():
    assert tier_a_ceiling(8, "male") == pytest.approx(18.1, abs=0.05)
    assert "tier_a_high" in _flags(classify("18.4", 8, "male"))
    assert _flags(classify("12.0", 8, "male")) == {"valid", "tier_b_assessed", "tier_b"}


def test_unit_error_is_kept_out_of_tier_a():
    assert _flags(classify("145", 30, "male")) == {"valid", "unit_error"}
    # Outside the mm range it is an implausible reading.
    assert "tier_a_high" in _flags(classify("45", 30, "male"))


def test_tier_b_below_who_minus_2sd_but_above_floor():
    assert who_minus_2sd(12, "male") == pytest.approx(12.5, abs=0.05)
    assert who_minus_2sd(30, "female") == pytest.approx(13.0, abs=0.05)
    assert _flags(classify("12.0", 12, "male")) == {"valid", "tier_b_assessed", "tier_b"}
    assert _flags(classify("12.6", 12, "male")) == {"valid", "tier_b_assessed"}


def test_muac_index_bins_count_every_in_range_value_whatever_the_age():
    def bins(result):
        return {k for k, v in result.items() if v and k.startswith("muac_bin_")}

    assert bins(classify("9.5", 30, "male")) == {"muac_bin_0"}
    assert bins(classify("10.4", 30, "male")) == {"muac_bin_0"}
    assert bins(classify("10.5", 30, "male")) == {"muac_bin_1"}
    assert bins(classify("21.4", 30, "male")) == {"muac_bin_11"}
    # Out of the index range, or no value: no bin.
    assert bins(classify("21.5", 30, "male")) == set()
    assert bins(classify("9.4", 30, "male")) == set()
    assert bins(classify(None, 30, "male")) == set()
    # Counted even where the plausibility check does not apply.
    assert bins(classify("14.0", None, "male")) == {"muac_bin_4"}
    assert bins(classify("14.0", 3, "male")) == {"muac_bin_4"}


def test_exclusions_and_out_of_range():
    assert _flags(classify("14", None, "male")) == {"missing_age"}
    assert _flags(classify(None, 30, "male")) == {"missing_muac"}
    assert _flags(classify("abc", 30, "male")) == {"missing_muac"}
    assert _flags(classify(None, 3, "male")) == {"out_of_age_range"}


def _row(**kw):
    row = {
        "opportunity_id": 2154,
        "username": "flw1",
        "ward": "Medu",
        "form_name": "Health Service Delivery",
        "time_start": "2026-08-23T11:00:00Z",  # a Sunday
        "muac_cm": "15.0",
        "age_months": "30",
        "childs_dob": None,
        "sex": "male",
    }
    row.update(kw)
    return row


def test_aggregate_cells_by_week_and_band():
    rows = [
        _row(),
        _row(muac_cm="8.0"),
        _row(time_start="2026-08-24T08:00:00Z"),  # Monday: next week
        _row(form_name="No Children Found", muac_cm=None, age_months=None),  # ignored
        _row(sex=None),
    ]
    out = aggregate(rows, form_name="Health Service Delivery", utc_offset=WAT)
    assert out["columns"][:5] == ["opportunity_id", "ward", "username", "week", "age_band"]
    assert out["columns"][5:] == list(COUNTERS)
    cells = {(r[3], r[4]): dict(zip(out["columns"], r)) for r in out["rows"]}
    assert set(cells) == {("2026-08-17", "24-35"), ("2026-08-24", "24-35")}
    first = cells[("2026-08-17", "24-35")]
    assert first["records"] == 3 and first["valid"] == 3 and first["tier_a_low"] == 1
    assert out["sex_missing"] == 1


def test_week_is_local_wat_monday():
    # Sunday 23:30 UTC is Monday 00:30 in Lagos.
    out = aggregate([_row(time_start="2026-08-23T23:30:00Z")], form_name="Health Service Delivery", utc_offset=WAT)
    assert out["rows"][0][3] == "2026-08-24"
