"""Smoke tests proving the harness itself works.

If we can't show that the diff catches a real disagreement, parity tests
that say "all leaves match" are meaningless. These tests are the
canary — they are intentionally minimal and shouldn't depend on any v1/v3
implementation, only on the contract walker and tolerance machinery.
"""

from commcare_connect.workflow.tests.mbw_parity.diff import diff_payloads
from commcare_connect.workflow.tests.mbw_parity.payload_contract import DASHBOARD_CONTRACT, PCT_EPS, Leaf


def _make_payload(total_visits: int, mother_counts: dict[str, int]) -> dict:
    """Minimal payload that exercises a few contract leaves."""
    return {
        "gps_data": {
            "total_visits": total_visits,
            "total_flagged": 0,
            "date_range_start": None,
            "date_range_end": None,
            "flw_summaries": [],
            "median_meters_by_flw": {},
            "median_minutes_by_flw": {},
        },
        "followup_data": {
            "total_cases": 0,
            "flw_summaries": [],
            "visit_status_distribution": {
                "approved": 0,
                "pending": 0,
                "rejected": 0,
                "over_limit": 0,
            },
        },
        "quality_metrics": {},
        "performance_data": [],
        "overview_data": {
            "mother_counts": mother_counts,
            "ebf_pct_by_flw": {},
            "form_name_distribution": {},
            "total_visit_rows": 0,
            "total_registration_forms": 0,
            "total_gs_forms": 0,
        },
    }


def test_identical_payloads_match():
    """Trivial sanity: payload diffed against itself reports zero diffs."""
    p = _make_payload(10, {"flw_a": 3, "flw_b": 5})
    report = diff_payloads(p, p, DASHBOARD_CONTRACT)
    assert report.is_match, report.format()


def test_integer_mismatch_is_caught():
    """Counts must be exact: a 1-off should fail."""
    p1 = _make_payload(10, {"flw_a": 3})
    p2 = _make_payload(11, {"flw_a": 3})
    report = diff_payloads(p1, p2, DASHBOARD_CONTRACT)
    assert not report.is_match
    paths = [d.path for d in report.diffs]
    assert any("total_visits" in p for p in paths)


def test_dict_keyed_by_flw_walks_all_keys():
    """`overview_data.mother_counts{}` must check each FLW's value."""
    p1 = _make_payload(0, {"flw_a": 3, "flw_b": 5})
    p2 = _make_payload(0, {"flw_a": 3, "flw_b": 99})
    report = diff_payloads(p1, p2, DASHBOARD_CONTRACT)
    assert not report.is_match
    paths = [d.path for d in report.diffs]
    assert any("flw_b" in p for p in paths)


def test_missing_key_reported():
    """If v3 is missing a key v1 has, the report flags it."""
    p1 = _make_payload(0, {"flw_a": 3})
    p2 = _make_payload(0, {})  # flw_a absent
    report = diff_payloads(p1, p2, DASHBOARD_CONTRACT)
    # flw_a in v1.mother_counts but not v3 — caught by walker
    assert not report.is_match


def test_epsilon_tolerance_swallows_small_float_drift():
    """Floats within epsilon must NOT register as a diff."""
    contract = [Leaf("x.y", float, PCT_EPS)]
    a = {"x": {"y": 50.000}}
    b = {"x": {"y": 50.005}}  # well within 0.01 eps
    report = diff_payloads(a, b, contract)
    assert report.is_match, report.format()


def test_epsilon_tolerance_catches_real_drift():
    """Floats outside epsilon must register as a diff."""
    contract = [Leaf("x.y", float, PCT_EPS)]
    a = {"x": {"y": 50.0}}
    b = {"x": {"y": 50.5}}  # 0.5 > 0.01 eps
    report = diff_payloads(a, b, contract)
    assert not report.is_match
