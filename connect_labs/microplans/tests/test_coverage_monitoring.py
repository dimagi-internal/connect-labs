"""Tests for the coverage monitoring lens (pure dataframe analytics)."""

from __future__ import annotations

import pandas as pd

from connect_labs.microplans.monitoring.coverage import compute_coverage_monitoring, expected_from_areas

_MAP = {c: c for c in ["sample_id", "cluster", "submission_time"]}


def _visits(rows):
    return pd.DataFrame(rows)


class TestExpectedFromAreas:
    def test_reads_expected_visit_count(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"properties": {"cluster": "C0", "expected_visit_count": 50, "building_count": 50}},
                {"properties": {"cluster": "C1", "building_count": 30}},  # falls back to building_count
                {"properties": {}},  # no cluster → skipped
            ],
        }
        assert expected_from_areas(fc) == {"C0": 50, "C1": 30}


class TestCoverageMonitoring:
    def test_per_cluster_completion_and_status(self):
        # C0 expects 4, 4 distinct households visited → complete
        # C1 expects 4, 2 distinct (one revisited) → in_progress
        # C2 expects 3, none visited → not_started
        rows = [
            dict(cluster="C0", sample_id="a", submission_time="2026-05-01T09:00:00Z"),
            dict(cluster="C0", sample_id="b", submission_time="2026-05-01T09:00:00Z"),
            dict(cluster="C0", sample_id="c", submission_time="2026-05-02T09:00:00Z"),
            dict(cluster="C0", sample_id="d", submission_time="2026-05-02T09:00:00Z"),
            dict(cluster="C1", sample_id="e", submission_time="2026-05-01T09:00:00Z"),
            dict(cluster="C1", sample_id="e", submission_time="2026-05-02T09:00:00Z"),  # revisit, same household
            dict(cluster="C1", sample_id="f", submission_time="2026-05-02T09:00:00Z"),
        ]
        out = compute_coverage_monitoring(_visits(rows), {"C0": 4, "C1": 4, "C2": 3}, field_map=_MAP)
        by = {r["cluster"]: r for r in out["per_cluster"]}
        assert by["C0"]["visited"] == 4 and by["C0"]["status"] == "complete" and by["C0"]["coverage_pct"] == 100.0
        assert by["C1"]["visited"] == 2 and by["C1"]["status"] == "in_progress" and by["C1"]["remaining"] == 2
        assert by["C2"]["visited"] == 0 and by["C2"]["status"] == "not_started"

        s = out["summary"]
        assert s["work_areas"] == 3
        assert s["complete"] == 1 and s["in_progress"] == 1 and s["not_started"] == 1
        assert s["total_expected"] == 11 and s["total_visited"] == 6
        assert s["coverage_pct"] == round(100 * 6 / 11, 1)

    def test_daily_progress(self):
        rows = [
            dict(cluster="C0", sample_id="a", submission_time="2026-05-01T09:00:00Z"),
            dict(cluster="C0", sample_id="b", submission_time="2026-05-01T10:00:00Z"),
            dict(cluster="C0", sample_id="c", submission_time="2026-05-02T09:00:00Z"),
        ]
        out = compute_coverage_monitoring(_visits(rows), {"C0": 10}, field_map=_MAP)
        assert out["daily"] == [
            {"date": "2026-05-01", "households_visited": 2},
            {"date": "2026-05-02", "households_visited": 1},
        ]

    def test_empty_visits_reports_all_not_started(self):
        out = compute_coverage_monitoring(pd.DataFrame(), {"C0": 5, "C1": 3}, field_map=_MAP)
        assert out["summary"]["not_started"] == 2 and out["summary"]["total_visited"] == 0
        assert all(r["status"] == "not_started" for r in out["per_cluster"])

    def test_observed_cluster_without_expected(self):
        # FLW visited a cluster we have no expected count for → expected 0, in_progress
        rows = [dict(cluster="CX", sample_id="z", submission_time="2026-05-01T09:00:00Z")]
        out = compute_coverage_monitoring(_visits(rows), {}, field_map=_MAP)
        row = out["per_cluster"][0]
        assert row["cluster"] == "CX" and row["expected"] == 0
        assert row["coverage_pct"] is None and row["status"] == "in_progress"
