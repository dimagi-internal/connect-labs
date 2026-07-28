from connect_labs.workflow.flw_daily_indicator_compute import (
    MIN_FORMS_FOR_RATE_INDICATORS,
    compute_flw_daily_indicators,
)


def _visit(time_start, time_end=None, **overrides):
    row = {
        "time_start": time_start,
        "time_end": time_end or time_start,
        "wa_caseid": "wa-1",
        "hh_case_id": "hh-1",
        "child_case_id": f"child-{time_start}",
        "household_phone": None,
        "child_name": None,
        "normalized_lat": "12.0",
        "normalized_lon": "9.0",
        "muac_cm": None,
        "received_any_vaccine": None,
        "dw_child_unwell_today": None,
        "diarrhea_last_month": None,
    }
    row.update(overrides)
    return row


def _padded_rate_indicator_visits(n=MIN_FORMS_FOR_RATE_INDICATORS, **shared_overrides):
    """n visits, 1 minute apart, distinct child/household ids so they don't
    trip the other (unrelated) indicators while testing a rate indicator."""
    return [
        _visit(
            f"2026-07-20T08:{i:02d}:00Z",
            hh_case_id=f"hh-{i}",
            child_case_id=f"child-{i}",
            **shared_overrides,
        )
        for i in range(n)
    ]


def test_total_forms_counts_every_visit():
    result = compute_flw_daily_indicators([_visit("2026-07-20T08:00:00Z"), _visit("2026-07-20T09:00:00Z")])
    assert result["total_forms"] == 2


def test_avg_forms_per_building_ratio():
    visits = [
        _visit("2026-07-20T08:00:00Z", wa_caseid="wa-1", child_case_id="c1"),
        _visit("2026-07-20T08:10:00Z", wa_caseid="wa-1", child_case_id="c2"),
        _visit("2026-07-20T08:20:00Z", wa_caseid="wa-2", child_case_id="c3"),
    ]
    result = compute_flw_daily_indicators(visits, wa_building_counts={"wa-1": 1.0, "wa-2": 10.0})
    by_wa = {row["wa_caseid"]: row for row in result["avg_forms_per_building"]["by_wa"]}
    assert by_wa["wa-1"]["forms"] == 2
    assert by_wa["wa-1"]["ratio"] == 2.0  # 2 forms / 1 building
    assert by_wa["wa-2"]["ratio"] == 0.1  # 1 form / 10 buildings
    assert result["avg_forms_per_building"]["max_ratio"] == 2.0


def test_avg_forms_per_building_missing_building_count_is_none():
    visits = [_visit("2026-07-20T08:00:00Z", wa_caseid="wa-unknown")]
    result = compute_flw_daily_indicators(visits, wa_building_counts={})
    assert result["avg_forms_per_building"]["max_ratio"] is None
    assert result["avg_forms_per_building"]["by_wa"][0]["ratio"] is None


def test_households_with_4plus_children_counted():
    visits = [
        # household hh-a: 4 distinct children -> counts
        _visit("2026-07-20T08:00:00Z", hh_case_id="hh-a", child_case_id="a1"),
        _visit("2026-07-20T08:10:00Z", hh_case_id="hh-a", child_case_id="a2"),
        _visit("2026-07-20T08:20:00Z", hh_case_id="hh-a", child_case_id="a3"),
        _visit("2026-07-20T08:30:00Z", hh_case_id="hh-a", child_case_id="a4"),
        # household hh-b: only 3 distinct children -> does not count
        _visit("2026-07-20T08:40:00Z", hh_case_id="hh-b", child_case_id="b1"),
        _visit("2026-07-20T08:50:00Z", hh_case_id="hh-b", child_case_id="b2"),
        _visit("2026-07-20T09:00:00Z", hh_case_id="hh-b", child_case_id="b3"),
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["households_4plus_children_count"] == 1


def test_gap_lt_2min_count_only_counts_rushed_pairs():
    visits = [
        _visit("2026-07-20T08:00:00Z", time_end="2026-07-20T08:00:00Z", child_case_id="c1"),
        _visit("2026-07-20T08:01:30Z", child_case_id="c2"),  # 90s gap -> rushed
        _visit("2026-07-20T08:10:00Z", child_case_id="c3"),  # ~8.5min gap -> not rushed
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["gap_lt_2min_count"] == 1


def test_vaccine_yes_pct_below_min_forms_is_none():
    visits = _padded_rate_indicator_visits(n=3, received_any_vaccine="yes")
    result = compute_flw_daily_indicators(visits)
    assert result["vaccine_yes_pct"] is None
    assert result["vaccine_forms_count"] == 3


def test_vaccine_yes_pct_computed_at_min_forms():
    visits = [
        _visit(f"2026-07-20T08:{i:02d}:00Z", hh_case_id=f"hh-{i}", child_case_id=f"c{i}", received_any_vaccine=answer)
        for i, answer in enumerate(["yes", "yes", "yes", "no", "no", "no", "no", "no"])
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["vaccine_forms_count"] == 8
    assert result["vaccine_yes_pct"] == 37.5


def test_camping_all_visits_in_one_cluster_is_100pct():
    visits = _padded_rate_indicator_visits(normalized_lat="12.0", normalized_lon="9.0")
    result = compute_flw_daily_indicators(visits)
    assert result["camping_forms_count"] == MIN_FORMS_FOR_RATE_INDICATORS
    assert result["camping_pct_largest_cluster"] == 100.0


def test_camping_one_outlier_reduces_largest_cluster_share():
    visits = _padded_rate_indicator_visits(n=MIN_FORMS_FOR_RATE_INDICATORS)
    # send the last visit ~111km away (1 degree latitude) -- clearly outside the 30m cluster radius
    visits[-1]["normalized_lat"] = "13.0"
    result = compute_flw_daily_indicators(visits)
    n = MIN_FORMS_FOR_RATE_INDICATORS
    assert result["camping_pct_largest_cluster"] == round((n - 1) / n * 100.0, 2)


def test_camping_below_min_forms_is_none():
    visits = _padded_rate_indicator_visits(n=3)
    result = compute_flw_daily_indicators(visits)
    assert result["camping_pct_largest_cluster"] is None


def test_travel_speed_violation_flags_implausible_speed():
    visits = [
        # ~111km apart (1 degree latitude) in 6 minutes -> ~1100 km/h, way over 15km/h
        _visit("2026-07-20T08:00:00Z", child_case_id="c1", normalized_lat="12.0"),
        _visit("2026-07-20T08:06:00Z", child_case_id="c2", normalized_lat="13.0"),
        # same spot, 10 minutes later -> 0 km/h, no violation
        _visit("2026-07-20T08:16:00Z", child_case_id="c3", normalized_lat="13.0"),
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["travel_speed_violation_count"] == 1


def test_duplicate_count_flags_shared_phone_and_shared_child_name_across_households():
    visits = [
        _visit(
            "2026-07-20T08:00:00Z", hh_case_id="hh-a", child_case_id="a1", household_phone="0800", child_name="Amina"
        ),
        _visit(
            "2026-07-20T08:10:00Z", hh_case_id="hh-b", child_case_id="b1", household_phone="0800", child_name="Bello"
        ),
        _visit(
            "2026-07-20T08:20:00Z", hh_case_id="hh-c", child_case_id="c1", household_phone="0900", child_name="AMINA"
        ),
    ]
    result = compute_flw_daily_indicators(visits)
    # 1 duplicate phone (0800 across hh-a/hh-b) + 1 duplicate child name (amina across hh-a/hh-c, case-insensitive)
    assert result["duplicate_count"] == 2


def test_duplicate_count_ignores_repeats_within_the_same_household():
    visits = [
        _visit("2026-07-20T08:00:00Z", hh_case_id="hh-a", child_case_id="a1", household_phone="0800"),
        _visit("2026-07-20T08:10:00Z", hh_case_id="hh-a", child_case_id="a2", household_phone="0800"),
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["duplicate_count"] == 0


def test_straight_lining_pct_and_min_forms_gate():
    answers = ["no", "no", "no", "no", "no", "no", "no", "yes"]  # 7/8 = 87.5%
    visits = [
        _visit(f"2026-07-20T08:{i:02d}:00Z", hh_case_id=f"hh-{i}", child_case_id=f"c{i}", dw_child_unwell_today=a)
        for i, a in enumerate(answers)
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["straight_line_pct"]["dw_child_unwell_today"] == 87.5
    assert result["straight_line_forms_count"]["dw_child_unwell_today"] == 8
    # diarrhea_last_month was never answered in this fixture -> n=0 -> gated to None
    assert result["straight_line_pct"]["diarrhea_last_month"] is None
    assert result["straight_line_forms_count"]["diarrhea_last_month"] == 0


def test_muac_repetition_pct_and_min_forms_gate():
    values = ["12.0", "12.0", "12.0", "12.0", "12.0", "11.5", "13.0", "9.9"]  # 5/8 = 62.5%
    visits = [
        _visit(f"2026-07-20T08:{i:02d}:00Z", hh_case_id=f"hh-{i}", child_case_id=f"c{i}", muac_cm=v)
        for i, v in enumerate(values)
    ]
    result = compute_flw_daily_indicators(visits)
    assert result["muac_forms_count"] == 8
    assert result["muac_repetition_pct"] == 62.5


def test_muac_repetition_below_min_forms_is_none():
    visits = _padded_rate_indicator_visits(n=3, muac_cm="12.0")
    result = compute_flw_daily_indicators(visits)
    assert result["muac_repetition_pct"] is None
    assert result["muac_forms_count"] == 3
