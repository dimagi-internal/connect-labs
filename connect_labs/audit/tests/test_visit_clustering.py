from connect_labs.audit.visit_clustering import build_flw_visit_clusters, compute_visit_clusters


def _v(vid, date, loc):
    return {"id": vid, "visit_date": date, "location": loc}


def test_disabled_returns_empty_list_regardless_of_input():
    visits = [_v(1, "2026-06-22T10:00:00Z", "1.0 2.0 0 5"), _v(2, "2026-06-22T10:01:00Z", "1.0 2.0 0 5")]
    assert compute_visit_clusters(visits) == []
    assert compute_visit_clusters(visits, enable_time_gap=False, enable_distance=False) == []


def test_time_only_clusters_visits_within_threshold():
    visits = [
        _v(1, "2026-06-22T10:00:00Z", None),
        _v(2, "2026-06-22T10:05:00Z", None),  # 5 min after 1 -> within 10 min
        _v(3, "2026-06-22T12:00:00Z", None),  # far from 2 -> isolated (dropped)
    ]
    clusters = compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=10)
    assert len(clusters) == 1
    assert clusters[0]["visit_ids"] == [1, 2]
    assert clusters[0]["group_id"] == "g1"


def test_time_only_excludes_pairs_outside_threshold():
    visits = [_v(1, "2026-06-22T10:00:00Z", None), _v(2, "2026-06-22T10:15:00Z", None)]
    assert compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=10) == []


def test_distance_only_clusters_visits_within_threshold():
    # ~5.5m apart at this latitude (0.00005 deg longitude delta at the equator)
    visits = [
        _v(1, None, "0.0 0.0 0 5"),
        _v(2, None, "0.0 0.00005 0 5"),
        _v(3, None, "10.0 10.0 0 5"),  # far away -> isolated
    ]
    clusters = compute_visit_clusters(visits, enable_distance=True, distance_meters=10)
    assert len(clusters) == 1
    assert clusters[0]["visit_ids"] == [1, 2]


def test_distance_only_excludes_pairs_outside_threshold():
    visits = [_v(1, None, "0.0 0.0 0 5"), _v(2, None, "0.0 1.0 0 5")]
    assert compute_visit_clusters(visits, enable_distance=True, distance_meters=10) == []


def test_and_semantics_both_must_hold_when_both_enabled():
    # Close in time, far in distance -> must NOT cluster (AND semantics)
    visits = [_v(1, "2026-06-22T10:00:00Z", "0.0 0.0 0 5"), _v(2, "2026-06-22T10:01:00Z", "10.0 10.0 0 5")]
    assert (
        compute_visit_clusters(
            visits, enable_time_gap=True, time_gap_minutes=10, enable_distance=True, distance_meters=10
        )
        == []
    )


def test_and_semantics_clusters_when_both_hold():
    visits = [_v(1, "2026-06-22T10:00:00Z", "0.0 0.0 0 5"), _v(2, "2026-06-22T10:01:00Z", "0.0 0.00005 0 5")]
    clusters = compute_visit_clusters(
        visits, enable_time_gap=True, time_gap_minutes=10, enable_distance=True, distance_meters=10
    )
    assert len(clusters) == 1
    assert clusters[0]["visit_ids"] == [1, 2]


def test_chains_transitively_across_three_visits():
    visits = [
        _v(1, "2026-06-22T10:00:00Z", None),
        _v(2, "2026-06-22T10:05:00Z", None),
        _v(3, "2026-06-22T10:10:00Z", None),
    ]
    clusters = compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=6)
    assert len(clusters) == 1
    assert clusters[0]["visit_ids"] == [1, 2, 3]


def test_drops_singletons_with_no_qualifying_neighbor():
    visits = [
        _v(1, "2026-06-22T10:00:00Z", None),
        _v(2, "2026-06-22T14:00:00Z", None),  # isolated
        _v(3, "2026-06-22T18:00:00Z", None),  # isolated
    ]
    assert compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=10) == []


def test_produces_multiple_independent_groups():
    visits = [
        _v(1, "2026-06-22T10:00:00Z", None),
        _v(2, "2026-06-22T10:05:00Z", None),
        _v(3, "2026-06-22T14:00:00Z", None),
        _v(4, "2026-06-22T14:05:00Z", None),
    ]
    clusters = compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=10)
    assert [c["visit_ids"] for c in clusters] == [[1, 2], [3, 4]]
    assert [c["group_id"] for c in clusters] == ["g1", "g2"]


def test_missing_visit_date_never_clusters_when_time_gap_enabled():
    visits = [_v(1, None, None), _v(2, "2026-06-22T10:00:00Z", None)]
    assert compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=1000000) == []


def test_missing_location_never_clusters_when_distance_enabled():
    visits = [_v(1, None, None), _v(2, None, "0.0 0.0 0 5")]
    assert compute_visit_clusters(visits, enable_distance=True, distance_meters=1000000) == []


def test_malformed_location_never_clusters_when_distance_enabled():
    visits = [_v(1, None, "not-a-location"), _v(2, None, "0.0 0.0 0 5")]
    assert compute_visit_clusters(visits, enable_distance=True, distance_meters=1000000) == []


def test_build_flw_visit_clusters_fills_in_image_count_from_flw_images():
    visit_meta_by_id = {
        "1": {"visit_date": "2026-06-22T10:00:00Z", "location": None},
        "2": {"visit_date": "2026-06-22T10:05:00Z", "location": None},
    }
    flw_images = {"1": [{"blob_id": "a"}, {"blob_id": "b"}], "2": [{"blob_id": "c"}]}
    clusters = build_flw_visit_clusters(
        [1, 2], visit_meta_by_id, flw_images, enable_time_gap=True, time_gap_minutes=10
    )
    assert clusters == [{"group_id": "g1", "visit_ids": [1, 2], "image_count": 3}]


def test_build_flw_visit_clusters_returns_empty_when_disabled():
    assert build_flw_visit_clusters([1, 2], {}, {}) == []


def test_build_flw_visit_clusters_handles_missing_visit_meta_gracefully():
    # A visit_id with no entry in visit_meta_by_id (e.g. the bulk fetch missed it)
    # must not crash -- it just never clusters (visit_date/location both None).
    clusters = build_flw_visit_clusters(
        [1, 2], {"1": {"visit_date": "2026-06-22T10:00:00Z", "location": None}}, {}, enable_time_gap=True
    )
    assert clusters == []


def test_naive_iso_datetime_pairs_with_aware_iso_datetime():
    # Regression: naive-ISO-datetime (no trailing Z) paired with Z-suffixed aware datetime
    # must not crash with "can't compare offset-naive and offset-aware datetimes"
    visits = [
        _v(1, "2026-06-22T10:00:00", None),  # no trailing Z -> naive, should be normalized
        _v(2, "2026-06-22T10:05:00Z", None),  # aware
    ]
    clusters = compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=10)
    assert len(clusters) == 1
    assert clusters[0]["visit_ids"] == [1, 2]


def test_naive_datetime_paired_with_missing_visit_date():
    # Regression: naive-datetime visit paired with a visit that has no visit_date (None)
    # must not crash and must not cluster (fail-safe, since counterpart datetime is unknown)
    visits = [
        _v(1, "2026-06-22T10:00:00", None),  # naive, should be normalized
        _v(2, None, None),  # missing visit_date
    ]
    clusters = compute_visit_clusters(visits, enable_time_gap=True, time_gap_minutes=1000000)
    assert clusters == []  # no clustering because visit 2 has no datetime
