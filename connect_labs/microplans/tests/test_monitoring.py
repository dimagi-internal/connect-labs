"""Tests for Stage B monitoring (pure dataframe analytics; no network/DB).

A small synthetic visit fixture with known values pins the exact flags and
rates ported from R derive_status.R.
"""

from __future__ import annotations

import pandas as pd

from connect_labs.microplans.monitoring.derive import add_attempt_index, derive_attempt_flags
from connect_labs.microplans.monitoring.duration import time_to_completion
from connect_labs.microplans.monitoring.gps_issue import build_gps_issue_report
from connect_labs.microplans.monitoring.normalize import normalize_visits
from connect_labs.microplans.monitoring.pipeline import compute_monitoring
from connect_labs.microplans.monitoring.rollups import build_cluster_rollup, build_enum_daily

# canonical-named fixture columns → identity field map for the pipeline
_IDENTITY_MAP = {
    c: c
    for c in [
        "sample_id",
        "cluster",
        "enumerator",
        "arm",
        "submission_time",
        "distance_m",
        "believed_reached_reason",
        "survey_completed_flag",
        "revisit_required_flag",
        "inhabited_flag",
        "fallback_distance_m",
        "duration_min",
    ]
}


def _visits():
    # Cluster C1, FLW alice, one day. 5 attempts with known characteristics.
    rows = [
        # reached (5m), completed, inhabited
        dict(
            sample_id="S1",
            distance_m=5,
            believed_reached_reason="",
            survey_completed_flag="complete",
            inhabited_flag="yes",
            fallback_distance_m=0,
            duration_min=8,
        ),
        # believed but 30m → gps issue, 26-50 band, completed, inhabited
        dict(
            sample_id="S2",
            distance_m=30,
            believed_reached_reason="believe_i_am_at_pin",
            survey_completed_flag="complete",
            inhabited_flag="yes",
            fallback_distance_m=0,
            duration_min=12,
        ),
        # believed but 60m → gps issue, >50 band, revisit, nonresidential
        dict(
            sample_id="S3",
            distance_m=60,
            believed_reached_reason="believe_i_am_at_pin",
            survey_completed_flag="revisit_required",
            inhabited_flag="no_nonresidential",
            fallback_distance_m=0,
            duration_min=18,
        ),
        # cannot reach → barrier, no structure
        dict(
            sample_id="S4",
            distance_m=None,
            believed_reached_reason="cannot_reach_target_pin",
            survey_completed_flag="",
            inhabited_flag="no_no_structure",
            fallback_distance_m=0,
            duration_min=25,
        ),
        # reached (10m), completed, inhabited, used a 20m fallback
        dict(
            sample_id="S5",
            distance_m=10,
            believed_reached_reason="",
            survey_completed_flag="complete",
            inhabited_flag="yes",
            fallback_distance_m=20,
            duration_min=35,
        ),
    ]
    for i, r in enumerate(rows):
        r.update(
            cluster="C1",
            arm="intervention",
            enumerator="alice",
            submission_time=f"2026-05-20T09:0{i}:00",
            date_local="2026-05-20",
        )
    return pd.DataFrame(rows)


class TestDerive:
    def test_attempt_flags(self):
        df = derive_attempt_flags(_visits())
        assert df["reached_le15"].tolist() == [True, False, False, False, True]
        assert df["believed_reached"].tolist() == [False, True, True, False, False]
        assert df["cannot_reach"].tolist() == [False, False, False, True, False]
        assert df["proceed_when_believed"].tolist() == [False, True, True, False, False]
        assert df["completed"].tolist() == [True, True, False, False, True]
        assert df["revisit_required"].tolist() == [False, False, True, False, False]

    def test_attempt_index(self):
        v = _visits()
        v = pd.concat([v, v.iloc[[0]].assign(submission_time="2026-05-20T10:00:00")], ignore_index=True)
        out = add_attempt_index(v)
        s1 = out[out["sample_id"] == "S1"].sort_values("submission_time")
        assert s1["attempt_n"].tolist() == [1, 2]


class TestClusterRollup:
    def test_counts_and_rates(self):
        df = add_attempt_index(derive_attempt_flags(_visits()))
        roll = build_cluster_rollup(df)
        r = roll[roll["cluster"] == "C1"].iloc[0]
        assert r["points_attempted"] == 5
        assert r["reached_within_15m"] == 2
        assert r["believed_at_pin_gps_issue"] == 2
        assert r["cannot_reach_barrier"] == 1
        assert r["believed_26_50m"] == 1 and r["believed_over_50m"] == 1
        assert r["target_inhabited"] == 3
        assert r["fallback_used"] == 1
        assert r["surveys_completed"] == 3
        assert r["gps_accuracy_rate"] == 40.0
        assert r["gps_issue_rate"] == 40.0
        assert r["barrier_rate"] == 20.0
        assert r["target_occupied_rate"] == 75.0  # 3 inhabited / (2 reached + 2 gps-issue)
        assert r["completion_rate"] == 60.0


class TestEnumDaily:
    def test_productivity(self):
        df = add_attempt_index(derive_attempt_flags(_visits()))
        daily = build_enum_daily(df)
        r = daily.iloc[0]
        assert r["enumerator"] == "alice"
        assert r["points_attempted"] == 5
        assert r["unique_targets_touched"] == 5
        assert r["targets_reached_le15"] == 2
        assert r["surveys_completed"] == 3


class TestDuration:
    def test_bins(self):
        out = time_to_completion(_visits())
        assert out["count"] == 5
        counts = {b["label"]: b["count"] for b in out["bins"]}
        assert counts == {"<10 min": 1, "10-15 min": 1, "15-20 min": 1, "20-30 min": 1, ">30 min": 1}


class TestGpsIssue:
    def test_report_filters_believed_over_25m(self):
        df = add_attempt_index(derive_attempt_flags(_visits()))
        rep = build_gps_issue_report(df)
        assert len(rep) == 2  # S2 (30m) + S3 (60m)
        assert set(rep["distance_category"]) == {"26-50m", ">50m"}


class TestNormalize:
    def test_maps_source_fields_and_tolerates_missing(self):
        raw = pd.DataFrame(
            {
                "work_area_id": ["S1", "S2"],
                "distance_target_pin_from_arrival_point": [5, 30],
                "visit_date": ["2026-05-20T09:00:00Z", "2026-05-20T09:05:00Z"],
                # no inhabited / fallback source columns present
            }
        )
        out = normalize_visits(raw)  # default field map
        assert out["sample_id"].tolist() == ["S1", "S2"]
        assert out["distance_m"].tolist() == [5, 30]
        assert out["inhabited_flag"].isna().all()  # missing source → NaN, no crash
        assert out["date_local"].iloc[0].isoformat() == "2026-05-20"


class TestIngest:
    def test_flatten_visits_merges_form_json(self):
        from connect_labs.microplans.monitoring.ingest import flatten_visits

        rows = [
            {
                "username": "alice",
                "visit_date": "2026-05-20T09:00:00Z",
                "form_json": {"distance_target_pin_from_arrival_point": 5, "pin_inhabited_residential": "yes"},
            },
            {
                "username": "bob",
                "visit_date": "2026-05-20T09:05:00Z",
                "form_json": {"distance_target_pin_from_arrival_point": 30},
            },
        ]
        df = flatten_visits(rows)
        assert "form_json" not in df.columns
        assert df["username"].tolist() == ["alice", "bob"]
        assert df["form.distance_target_pin_from_arrival_point"].tolist() == [5, 30]

    def test_load_canonical_composes_fetch_flatten_normalize(self, monkeypatch):
        from connect_labs.microplans.monitoring import ingest

        rows = [
            {
                "username": "alice",
                "visit_date": "2026-05-20T09:00:00Z",
                "form_json": {"dist": 5, "outcome": "complete"},
            },
        ]
        monkeypatch.setattr(ingest, "fetch_user_visits", lambda *a, **k: rows)
        field_map = {
            "enumerator": "username",
            "submission_time": "visit_date",
            "distance_m": "form.dist",
            "survey_completed_flag": "form.outcome",
        }
        canonical = ingest.load_canonical(123, "tok", field_map=field_map)
        assert canonical["enumerator"].iloc[0] == "alice"
        assert canonical["distance_m"].iloc[0] == 5
        assert canonical["survey_completed_flag"].iloc[0] == "complete"


class TestPipeline:
    def test_compute_monitoring_payload(self):
        payload = compute_monitoring(_visits(), field_map=_IDENTITY_MAP)
        assert payload["totals"]["attempts"] == 5
        assert payload["totals"]["completed"] == 3
        assert payload["totals"]["gps_accuracy_rate"] == 40.0
        assert payload["by_arm"]["intervention"]["attempts"] == 5
        assert payload["cluster_rollup"][0]["completion_rate"] == 60.0
        assert len(payload["gps_issues"]) == 2
        assert payload["time_to_completion"]["count"] == 5
