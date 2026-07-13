import math

from connect_labs.workflow.flw_audit_compute import (
    compute_flw_indicators,
    haversine_meters,
    muac_bucket_label,
    muac_cutoff_clustering_flags,
    whipple_index,
)


def test_haversine_known_distance():
    # ~1 degree of latitude ~= 111.19 km at the equator.
    d = haversine_meters(0.0, 0.0, 1.0, 0.0)
    assert math.isclose(d, 111195, rel_tol=0.01)


def test_haversine_zero_distance():
    assert haversine_meters(12.28, 9.88, 12.28, 9.88) == 0.0


def test_haversine_none_when_missing_coord():
    assert haversine_meters(None, 9.88, 12.28, 9.88) is None


def test_muac_bucket_label_within_range():
    assert muac_bucket_label(11.5) == "11.5-12.0"
    assert muac_bucket_label(11.7) == "11.5-12.0"
    assert muac_bucket_label(11.4) == "11.0-11.5"


def test_muac_bucket_label_out_of_range():
    assert muac_bucket_label(3.0) == "<6.0"
    assert muac_bucket_label(25.0) == ">=20.0"


def test_whipple_index_no_heaping():
    # One child at each of the 60 possible ages -> exactly the expected uniform rate.
    ages = list(range(60))
    assert math.isclose(whipple_index(ages), 100.0, rel_tol=1e-6)


def test_whipple_index_full_heaping():
    # Every child exactly at a heaping age -> far above 100.
    ages = [12, 24, 36, 48] * 5
    idx = whipple_index(ages)
    assert math.isclose(idx, 1500.0, rel_tol=1e-6)  # (1.0 / (4/60)) * 100


def test_whipple_index_empty():
    assert whipple_index([]) is None


def test_muac_cutoff_clustering_flags_detects_spike():
    # 115mm (11.5cm) spikes hard relative to its 4 neighbors; 125mm spikes just
    # over the 2x-neighbor-average threshold (3/1 = 3.0 > 2.0).
    values = [115] * 20 + [113, 114, 116, 117] + [125] * 3 + [123, 124, 126, 127]
    result = muac_cutoff_clustering_flags(values)
    assert result["115"]["cutoff_count"] == 20
    assert result["115"]["neighbor_avg"] == 1.0
    assert result["115"]["flagged"] is True
    assert result["125"]["cutoff_count"] == 3
    assert result["125"]["neighbor_avg"] == 1.0
    assert result["125"]["flagged"] is True  # 3/1 = 3.0 > 2.0


def test_muac_cutoff_clustering_flags_no_spike():
    values = [113, 114, 115, 116, 117]  # even spread, no clustering
    result = muac_cutoff_clustering_flags(values)
    assert result["115"]["flagged"] is False


def _visit(
    username="alice",
    child_case_id="child-1",
    hh_case_id="hh-1",
    time_start="2026-07-06T08:00:00Z",
    time_end="2026-07-06T08:10:00Z",
    lat=12.28,
    lon=9.88,
    accuracy=5.0,
    muac_cm=15.0,
    age_months=24,
    age_days=730,
    gender="male",
    dob="2024-07-06",
    form_display_name="Health Service Delivery",
):
    return {
        "username": username,
        "child_case_id": child_case_id,
        "hh_case_id": hh_case_id,
        "time_start": time_start,
        "time_end": time_end,
        "normalized_lat": lat,
        "normalized_lon": lon,
        "current_accuracy": accuracy,
        "muac_cm": muac_cm,
        "age_months": age_months,
        "age_days": age_days,
        "childs_gender": gender,
        "childs_dob": dob,
        "form_display_name": form_display_name,
    }


def test_compute_flw_indicators_basic_counts():
    visits = [
        _visit(
            child_case_id="c1", hh_case_id="hh1", time_start="2026-07-06T08:00:00Z", time_end="2026-07-06T08:10:00Z"
        ),
        _visit(
            child_case_id="c2", hh_case_id="hh1", time_start="2026-07-06T08:20:00Z", time_end="2026-07-06T08:30:00Z"
        ),
        _visit(
            child_case_id="c3", hh_case_id="hh2", time_start="2026-07-07T09:00:00Z", time_end="2026-07-07T09:10:00Z"
        ),
    ]
    result = compute_flw_indicators(visits)
    assert result["total_service_delivery_forms"] == 3
    assert result["days_worked"] == 2
    # 2 households: hh1 has 2 children, hh2 has 1 -> avg = 1.5
    assert result["avg_children_per_household"] == 1.5


def test_compute_flw_indicators_gap_percentage():
    # Two consecutive visits 2 minutes apart (gap < 3min threshold), one pair 10 min apart.
    visits = [
        _visit(child_case_id="c1", time_start="2026-07-06T08:00:00Z", time_end="2026-07-06T08:05:00Z"),
        _visit(child_case_id="c2", time_start="2026-07-06T08:07:00Z", time_end="2026-07-06T08:12:00Z"),
        _visit(child_case_id="c3", time_start="2026-07-06T08:22:00Z", time_end="2026-07-06T08:27:00Z"),
    ]
    result = compute_flw_indicators(visits)
    # gaps: (08:07 - 08:05)=2min, (08:22-08:12)=10min -> 1 of 2 gaps < 3min = 50%
    assert result["pct_gap_lt_3min"] == 50.0
    assert result["median_gap_minutes"] == 6.0


def test_compute_flw_indicators_gps_accuracy_flags():
    visits = [
        _visit(child_case_id="c1", accuracy=0.0),  # flagged: exactly 0
        _visit(child_case_id="c2", accuracy=150.0),  # flagged: > 100
        _visit(child_case_id="c3", accuracy=5.0),  # not flagged
    ]
    result = compute_flw_indicators(visits)
    assert result["fraud"]["gps_accuracy_flag_count"] == 2
    assert result["fraud"]["gps_accuracy_flag_pct"] == round(2 / 3 * 100, 2)


def test_compute_flw_indicators_duration_outlier():
    visits = [
        _visit(
            child_case_id="c1", time_start="2026-07-06T08:00:00Z", time_end="2026-07-06T08:01:00Z"
        ),  # 1 min: outlier
        _visit(child_case_id="c2", time_start="2026-07-06T09:00:00Z", time_end="2026-07-06T09:05:00Z"),  # 5 min: fine
    ]
    result = compute_flw_indicators(visits)
    assert result["fraud"]["form_duration_outlier_count"] == 1


def test_compute_flw_indicators_near_duplicate_gps_different_households():
    visits = [
        _visit(child_case_id="c1", hh_case_id="hh1", lat=12.0000, lon=9.0000, time_start="2026-07-06T08:00:00Z"),
        _visit(
            child_case_id="c2", hh_case_id="hh2", lat=12.00005, lon=9.0000, time_start="2026-07-06T08:30:00Z"
        ),  # ~5.5m from c1 AND c3, different household from both
        _visit(
            child_case_id="c3", hh_case_id="hh1", lat=12.0000, lon=9.0000, time_start="2026-07-06T09:00:00Z"
        ),  # same household as c1 (c1-c3 pair not counted)
    ]
    result = compute_flw_indicators(visits)
    # c2 is within 10m of both c1 and c3, and c2's household differs from both -> 2 cross-household
    # near pairs (c1-c2, c2-c3). c1-c3 shares hh1, so that pair is excluded.
    assert result["fraud"]["gps_near_duplicate_count"] == 2


def test_compute_flw_indicators_implied_speed_flag():
    # ~11.1km apart (0.1 degree lat), 1 minute apart -> ~666 km/h, way over 60km/h threshold.
    visits = [
        _visit(
            child_case_id="c1", lat=12.0, lon=9.0, time_start="2026-07-06T08:00:00Z", time_end="2026-07-06T08:01:00Z"
        ),
        _visit(
            child_case_id="c2", lat=12.1, lon=9.0, time_start="2026-07-06T08:01:00Z", time_end="2026-07-06T08:02:00Z"
        ),
    ]
    result = compute_flw_indicators(visits)
    assert result["fraud"]["implied_speed_flag_count"] == 1


def test_compute_flw_indicators_duplicate_child_across_households():
    visits = [
        _visit(child_case_id="c1", hh_case_id="hh1", dob="2024-01-01", time_start="2026-07-06T08:00:00Z"),
        _visit(
            child_case_id="c2", hh_case_id="hh2", dob="2024-01-01", time_start="2026-07-06T09:00:00Z"
        ),  # same DOB, different household -> duplicate
        _visit(child_case_id="c3", hh_case_id="hh3", dob="2024-06-01", time_start="2026-07-06T10:00:00Z"),
    ]
    result = compute_flw_indicators(visits)
    assert result["fraud"]["duplicate_child_count"] == 1


def test_compute_flw_indicators_same_dob_within_household():
    visits = [
        _visit(child_case_id="c1", hh_case_id="hh1", dob="2024-01-01", time_start="2026-07-06T08:00:00Z"),
        _visit(
            child_case_id="c2", hh_case_id="hh1", dob="2024-01-01", time_start="2026-07-06T08:20:00Z"
        ),  # twin DOB collision within hh1
        _visit(child_case_id="c3", hh_case_id="hh2", dob="2024-06-01", time_start="2026-07-06T09:00:00Z"),
    ]
    result = compute_flw_indicators(visits)
    # 1 of 2 households (hh1) has a same-DOB collision -> 50%
    assert result["pct_same_dob_within_household"] == 50.0


def test_compute_flw_indicators_age_and_muac_buckets():
    visits = [
        _visit(child_case_id="c1", age_months=5, muac_cm=11.5),
        _visit(child_case_id="c2", age_months=5, muac_cm=11.5),  # different child, same age/muac -> both counted
        _visit(child_case_id="c3", age_months=30, muac_cm=17.2),
    ]
    result = compute_flw_indicators(visits)
    assert result["children_by_age_month"]["5"] == 2
    assert result["children_by_age_month"]["30"] == 1
    assert result["children_by_muac_bucket"]["11.5-12.0"] == 2
    assert result["children_by_muac_bucket"]["17.0-17.5"] == 1


def test_compute_flw_indicators_dedupes_repeat_visit_to_same_child():
    # Same child visited twice this week -> counted once for age/muac/household buckets,
    # but every visit still counts toward total_service_delivery_forms/cadence stats.
    visits = [
        _visit(child_case_id="c1", hh_case_id="hh1", time_start="2026-07-06T08:00:00Z", age_months=10, muac_cm=13.0),
        _visit(child_case_id="c1", hh_case_id="hh1", time_start="2026-07-09T08:00:00Z", age_months=10, muac_cm=13.0),
    ]
    result = compute_flw_indicators(visits)
    assert result["total_service_delivery_forms"] == 2
    assert result["avg_children_per_household"] == 1  # only 1 distinct child in hh1
    assert result["children_by_age_month"]["10"] == 1


def test_compute_flw_indicators_empty_visits():
    result = compute_flw_indicators([])
    assert result["total_service_delivery_forms"] == 0
    assert result["days_worked"] == 0
    assert result["pct_gap_lt_3min"] is None
    assert result["avg_children_per_household"] is None
    assert result["fraud"]["age_heaping_whipple_index"] is None
    assert result["fraud"]["age_heaping_flagged"] is False
