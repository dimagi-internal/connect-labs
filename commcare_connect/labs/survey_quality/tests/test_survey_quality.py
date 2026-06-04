"""Unit tests for the survey-quality algorithm library.

Pure-Python — no Django, DB, or GDAL — so it runs under pytest or standalone
(``python commcare_connect/labs/survey_quality/tests/test_survey_quality.py``).
"""

from __future__ import annotations

import os
import sys

# Allow standalone execution from a repo checkout.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from commcare_connect.labs.survey_quality import REGISTRY, results_to_map, run_metrics  # noqa: E402


def _rec(**kw):
    base = dict(
        form_type="primary",
        ward="Kaura",
        arm="treatment",
        enumerator_id="E1",
        lat=9.66,
        lon=8.48,
        assigned_lat=9.66,
        assigned_lon=8.48,
        gps_offset_m=5.0,
        in_ward=True,
        start_ts=1000,
        end_ts=2080,
        duration_min=18.0,
        evidence_photo=True,
        child_present=True,
        child_sex="F",
        child_age_months=24,
        eligible=True,
        vitamin_a_received=True,
        dose_source="campaign",
        original_record_id=None,
        original_enumerator_id=None,
    )
    base.update(kw)
    return base


def _fixture():
    recs = []
    # 10 primary records, household ids p0..p9, each at a distinct place + time.
    for i in range(10):
        recs.append(
            _rec(
                record_id=f"p{i}",
                household_id=f"H{i}",
                lat=9.66 + i * 0.001,
                lon=8.48 + i * 0.001,
                start_ts=1000 + i * 100,
            )
        )
    # 4 received=False (so 6 positives).
    for i in (6, 7, 8, 9):
        recs[i]["vitamin_a_received"] = False
    # 1 positive missing its evidence photo (-> evidence_capture = 5/6).
    recs[5]["evidence_photo"] = False
    # 1 record GPS far (-> gps_within_15m = 9/10).
    recs[3]["gps_offset_m"] = 30.0
    # 1 record too-fast (-> duration flagged).
    recs[4]["duration_min"] = 1.5
    # back-checks on p0, p1, p2 by a different enumerator.
    bc0 = _rec(record_id="b0", household_id="H0", form_type="back_check", enumerator_id="BC", original_record_id="p0")
    bc1 = _rec(record_id="b1", household_id="H1", form_type="back_check", enumerator_id="BC", original_record_id="p1")
    bc2 = _rec(record_id="b2", household_id="H2", form_type="back_check", enumerator_id="BC", original_record_id="p2")
    # p2 disagrees on outcome AND child_sex (a Type-1 mismatch + outcome mismatch).
    bc2["vitamin_a_received"] = False
    bc2["child_sex"] = "M"
    return recs + [bc0, bc1, bc2]


def test_registry_populated():
    assert "evidence_capture" in REGISTRY
    assert "backcheck_type1_error" in REGISTRY
    assert "enum_scorecard" in REGISTRY


def test_layer1_metrics():
    m = results_to_map(run_metrics(_fixture(), layers=["survey_quality"]))
    assert m["evidence_capture"]["value"] == 83.3  # 5 of 6 positives have a photo
    assert m["evidence_capture"]["passed"] is False  # below the 95% threshold
    assert m["gps_within_15m"]["value"] == 90.0  # 9 of 10
    assert m["field_completeness"]["value"] == 100.0
    assert m["duplicate_integrity"]["value"] == 0
    assert m["duration_plausibility"]["value"] == 90.0  # 1 of 10 too fast


def test_backcheck_metrics():
    cfg = {}
    m = results_to_map(run_metrics(_fixture(), layers=["backcheck"], config=cfg))
    assert m["backcheck_coverage"]["n"] == 3
    assert m["backcheck_outcome_agreement"]["value"] == 66.7  # p2 disagrees -> 2 of 3
    assert m["backcheck_type1_error"]["value"] == 33.3  # p2 child_sex differs -> 1 of 3
    # comparison rows: 3 rows, exactly 1 flagged (p2), and it sorts first.
    comp = m["backcheck_comparison"]
    assert comp["n"] == 3
    assert comp["value"] == 1
    assert comp["detail"]["rows"][0]["household_id"] == "H2"
    assert comp["detail"]["rows"][0]["flagged"] is True


def test_prtest_runs():
    m = results_to_map(run_metrics(_fixture(), layers=["backcheck"]))
    pr = m["backcheck_outcome_prtest"]
    assert pr["value"] is not None
    assert "orig_pct" in pr["detail"] and "backcheck_pct" in pr["detail"]


def test_outlier_scorecard_smoke():
    m = results_to_map(run_metrics(_fixture(), layers=["outlier"]))
    sc = m["enum_scorecard"]
    assert "per_enumerator" in sc["detail"]
    # Single enumerator pool -> no relative outliers, everyone green.
    assert sc["value"] == 0


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    _main()
