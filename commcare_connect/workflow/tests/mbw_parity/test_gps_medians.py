"""Algorithm spec tests for the GPS median functions.

These are not parity tests — they're hand-crafted assertions that pin the
algorithm to v1's exact behaviour. When v3 ships a SQL window-field
implementation of these metrics, a future Postgres-execution test will
verify the SQL produces the same output as these reference functions
(same bounding pattern as test_aggregation_execution.py uses for the
in-memory aggregation runner).

Algorithm:
1. Filter to visits with parsed GPS + mother_case_id + visit_date + visit_datetime.
2. (meters only) Filter to visits with app_build_version > 0.
3. Group by (username, visit_date), sort each group by visit_datetime.
4. Dedupe by mother_case_id within each (FLW, day).
5. Skip (FLW, day) groups with < 2 unique mothers.
6. For each consecutive pair, compute haversine (meters) or time-diff (minutes).
7. Per FLW, median across all pairs from all days, rounded to int.
"""

from datetime import datetime, timezone

import pytest

from commcare_connect.workflow.tests.mbw_parity.runners import (
    compute_gps_median_meters_by_flw,
    compute_gps_median_minutes_by_flw,
)


def _visit(
    visit_id: int,
    username: str,
    when: datetime,
    *,
    mother_case_id: str | None = "m1",
    gps: tuple[float, float] | None = (-1.0, 35.0),
    app_build_version: int | None = 250,
) -> dict:
    """Minimal visit row for GPS-median testing."""
    return {
        "visit_id": visit_id,
        "username": username,
        "visit_date": when.date().isoformat(),
        "visit_datetime": when.isoformat(),
        "mother_case_id": mother_case_id,
        "gps_location": f"{gps[0]} {gps[1]} 1000 10" if gps else None,
        "app_build_version": app_build_version,
    }


# ---- median meters ----


class TestMedianMeters:
    def test_two_mothers_same_day_known_distance(self):
        """Two visits to two mothers in one day, ~1.0001° apart at equator
        → ~111m haversine. Median over a single pair = that pair.
        """
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1", gps=(-1.0000, 35.0000)),
            _visit(
                2, "a", datetime(2024, 1, 10, 10, tzinfo=timezone.utc), mother_case_id="m2", gps=(-1.0010, 35.0000)
            ),
        ]
        result = compute_gps_median_meters_by_flw(visits)
        # 0.001° latitude ≈ 111m; rounded to int.
        assert result["a"] == pytest.approx(111, abs=2)

    def test_single_mother_per_day_excluded(self):
        """Day with only one mother visited yields no pair → no contribution.
        FLW with NO qualifying days produces None.
        """
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1"),
            _visit(2, "a", datetime(2024, 1, 11, 9, tzinfo=timezone.utc), mother_case_id="m1"),
        ]
        result = compute_gps_median_meters_by_flw(visits)
        assert result == {}

    def test_per_mother_dedup_keeps_first(self):
        """Two visits to the SAME mother in one day collapse to one — they
        do not create a (m1, m1) pair. With only one unique mother, the day
        is excluded.
        """
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1", gps=(-1.0, 35.0)),
            _visit(2, "a", datetime(2024, 1, 10, 11, tzinfo=timezone.utc), mother_case_id="m1", gps=(-2.0, 36.0)),
        ]
        result = compute_gps_median_meters_by_flw(visits)
        assert result == {}

    def test_app_version_zero_excluded_from_meters(self):
        """app_build_version=0 (or None) filters out of meters but not minutes.
        Two visits, one with app_build_version=0 → excluded → no qualifying pair.
        """
        visits = [
            _visit(
                1,
                "a",
                datetime(2024, 1, 10, 9, tzinfo=timezone.utc),
                mother_case_id="m1",
                gps=(-1.0, 35.0),
                app_build_version=0,  # excluded from meters
            ),
            _visit(
                2,
                "a",
                datetime(2024, 1, 10, 10, tzinfo=timezone.utc),
                mother_case_id="m2",
                gps=(-1.001, 35.0),
                app_build_version=250,
            ),
        ]
        result = compute_gps_median_meters_by_flw(visits)
        # First visit dropped → only one mother, no pair.
        assert result == {}

    def test_missing_gps_excluded(self):
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1", gps=None),
            _visit(2, "a", datetime(2024, 1, 10, 10, tzinfo=timezone.utc), mother_case_id="m2", gps=(-1.001, 35.0)),
        ]
        result = compute_gps_median_meters_by_flw(visits)
        assert result == {}

    def test_three_mothers_yields_two_pairs(self):
        """Three mothers in chrono order → two consecutive pairs.
        Distances ~111m and ~111m, median ~111m.
        """
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1", gps=(-1.0000, 35.0000)),
            _visit(
                2, "a", datetime(2024, 1, 10, 10, tzinfo=timezone.utc), mother_case_id="m2", gps=(-1.0010, 35.0000)
            ),
            _visit(
                3, "a", datetime(2024, 1, 10, 11, tzinfo=timezone.utc), mother_case_id="m3", gps=(-1.0020, 35.0000)
            ),
        ]
        result = compute_gps_median_meters_by_flw(visits)
        assert result["a"] == pytest.approx(111, abs=2)


# ---- median minutes ----


class TestMedianMinutes:
    def test_one_hour_apart(self):
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1"),
            _visit(2, "a", datetime(2024, 1, 10, 10, tzinfo=timezone.utc), mother_case_id="m2"),
        ]
        result = compute_gps_median_minutes_by_flw(visits)
        assert result["a"] == 60

    def test_app_version_does_not_filter_minutes(self):
        """app_build_version=0 must NOT filter for minutes (mirrors v1's
        no-cutoff behavior)."""
        visits = [
            _visit(
                1,
                "a",
                datetime(2024, 1, 10, 9, tzinfo=timezone.utc),
                mother_case_id="m1",
                app_build_version=0,
            ),
            _visit(
                2,
                "a",
                datetime(2024, 1, 10, 9, 30, tzinfo=timezone.utc),
                mother_case_id="m2",
                app_build_version=250,
            ),
        ]
        result = compute_gps_median_minutes_by_flw(visits)
        # Both visits qualify for minutes → 30-minute gap.
        assert result["a"] == 30

    def test_per_mother_dedup_applies_to_minutes_too(self):
        """Repeated mother-visits within a day are deduped before pairing,
        same as for meters. One unique mother → no pair → no contribution.
        """
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, tzinfo=timezone.utc), mother_case_id="m1"),
            _visit(2, "a", datetime(2024, 1, 10, 10, tzinfo=timezone.utc), mother_case_id="m1"),
        ]
        result = compute_gps_median_minutes_by_flw(visits)
        assert result == {}

    def test_three_visits_two_pairs_median(self):
        """Three mothers at 9:00, 9:30, 11:00. Pairs: 30min, 90min. Median = 60."""
        visits = [
            _visit(1, "a", datetime(2024, 1, 10, 9, 0, tzinfo=timezone.utc), mother_case_id="m1"),
            _visit(2, "a", datetime(2024, 1, 10, 9, 30, tzinfo=timezone.utc), mother_case_id="m2"),
            _visit(3, "a", datetime(2024, 1, 10, 11, 0, tzinfo=timezone.utc), mother_case_id="m3"),
        ]
        result = compute_gps_median_minutes_by_flw(visits)
        assert result["a"] == 60

    def test_multiple_days_aggregate_to_overall_median(self):
        """Two days, two pairs each. All four time-diffs aggregate to one
        per-FLW median.
        """
        visits = [
            # Day 1 — 30min and 90min apart
            _visit(1, "a", datetime(2024, 1, 10, 9, 0, tzinfo=timezone.utc), mother_case_id="m1"),
            _visit(2, "a", datetime(2024, 1, 10, 9, 30, tzinfo=timezone.utc), mother_case_id="m2"),
            _visit(3, "a", datetime(2024, 1, 10, 11, 0, tzinfo=timezone.utc), mother_case_id="m3"),
            # Day 2 — 60min and 60min apart
            _visit(4, "a", datetime(2024, 1, 11, 9, 0, tzinfo=timezone.utc), mother_case_id="m4"),
            _visit(5, "a", datetime(2024, 1, 11, 10, 0, tzinfo=timezone.utc), mother_case_id="m5"),
            _visit(6, "a", datetime(2024, 1, 11, 11, 0, tzinfo=timezone.utc), mother_case_id="m6"),
        ]
        # All diffs: [30, 90, 60, 60]. Sorted: [30, 60, 60, 90]. Median = 60.
        result = compute_gps_median_minutes_by_flw(visits)
        assert result["a"] == 60
