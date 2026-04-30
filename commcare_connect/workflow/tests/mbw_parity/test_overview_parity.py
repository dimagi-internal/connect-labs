"""End-to-end v1↔v3 parity tests for the dashboard's overview_data block.

This is the first non-trivial slice of the dashboard payload. v1's logic
lives in templates/mbw_monitoring/{data_transforms,followup_analysis}.py;
v3's equivalent is implemented declaratively as PIPELINE_SCHEMAS in the
forthcoming mbw_monitoring_v3 template plus a JSX-side histogram.

These tests compare the v1 reference implementation against the v3
pipeline-equivalent computation on synthetic fixtures and on assembled
contract leaves walked by the parity diff machinery.
"""

import pytest

from commcare_connect.workflow.tests.mbw_parity.diff import diff_payloads
from commcare_connect.workflow.tests.mbw_parity.fixtures import edge_cases, small_realistic
from commcare_connect.workflow.tests.mbw_parity.payload_contract import DASHBOARD_CONTRACT, Leaf
from commcare_connect.workflow.tests.mbw_parity.runners import (
    aggregate,
    compute_v1_overview_reference,
    compute_v3_overview,
)


def _overview_only_contract() -> list[Leaf]:
    """Subset of DASHBOARD_CONTRACT that touches only `overview_data.*` leaves.

    Used to gate v1↔v3 parity for this PR's slice without complaining about
    leaves under gps_data, followup_data, etc., which v3 doesn't compute yet.
    """
    return [leaf for leaf in DASHBOARD_CONTRACT if leaf.path.startswith("overview_data.")]


def _wrap_overview(overview_dict: dict) -> dict:
    """Wrap a bare overview_data dict in the full payload shape so the
    contract walker can navigate it."""
    return {"overview_data": overview_dict}


# ---- contains_word filter unit coverage on the in-memory mirror ----


class TestContainsWordFilter:
    def test_matches_ebf_as_token(self):
        rows = [
            {"u": "a", "bf": "ebf", "v": 1},
            {"u": "a", "bf": "ebf bottle", "v": 1},
            {"u": "a", "bf": "non-ebf", "v": 1},  # substring, not token → excluded
            {"u": "a", "bf": "", "v": 1},  # empty → excluded
            {"u": "a", "bf": None, "v": 1},  # null → excluded
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="ebf_count",
            source_path="v",
            aggregation="count",
            filter_path="bf",
            filter_value="ebf",
            filter_op="contains_word",
        )
        assert result == {"a": 2}

    def test_eq_filter_unchanged(self):
        rows = [
            {"u": "a", "bf": "ebf", "v": 1},
            {"u": "a", "bf": "ebf bottle", "v": 1},  # not exact-equal
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="ebf_count",
            source_path="v",
            aggregation="count",
            filter_path="bf",
            filter_value="ebf",
            filter_op="eq",
        )
        assert result == {"a": 1}


# ---- field-by-field parity coverage ----


class TestOverviewParity:
    def test_mother_counts_parity_small_realistic(self):
        bundle = small_realistic()
        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
        assert v1["mother_counts"] == v3["mother_counts"]

    def test_mother_counts_parity_edge_cases(self):
        bundle = edge_cases()
        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
        assert v1["mother_counts"] == v3["mother_counts"]

    def test_ebf_pct_parity_small_realistic(self):
        bundle = small_realistic()
        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
        assert v1["ebf_pct_by_flw"] == v3["ebf_pct_by_flw"]

    def test_ebf_pct_parity_edge_cases(self):
        """Exercises the contains_word semantics — `ebf bottle` counts but
        `non-ebf` doesn't, and empty bf_status is excluded from the denominator."""
        bundle = edge_cases()
        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
        assert v1["ebf_pct_by_flw"] == v3["ebf_pct_by_flw"]
        # Spot-check the v1 quirks declared in fixtures.edge_cases:
        # flw_ebf has 4 visits with bf_status: "ebf", "ebf bottle", "non-ebf", "".
        # Empty drops out of denominator. Numerator: "ebf", "ebf bottle" = 2 of 3.
        # 2/3 * 100 = 66.667 → round() → 67
        assert v1["ebf_pct_by_flw"]["flw_ebf"] == 67

    def test_form_name_distribution_parity_both_fixtures(self):
        for label, bundle in (("small_realistic", small_realistic()), ("edge_cases", edge_cases())):
            v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
            v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
            assert v1["form_name_distribution"] == v3["form_name_distribution"], f"diff on {label}"

    def test_row_counts_parity_both_fixtures(self):
        for bundle in (small_realistic(), edge_cases()):
            v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
            v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
            assert v1["total_visit_rows"] == v3["total_visit_rows"]
            assert v1["total_registration_forms"] == v3["total_registration_forms"]
            assert v1["total_gs_forms"] == v3["total_gs_forms"]


# ---- contract-walk parity (every overview_data leaf at once) ----


class TestOverviewContractWalk:
    """Run the full diff-payloads contract walker against both fixtures.

    This is the test that proves the harness works end-to-end on a real
    payload slice — every overview_data leaf in the contract must agree
    within tolerance, and the walker reports any disagreement structurally.
    """

    @pytest.mark.parametrize("bundle_factory", [small_realistic, edge_cases])
    def test_full_overview_parity(self, bundle_factory):
        bundle = bundle_factory()
        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_overview(bundle.visits, bundle.registrations, bundle.gs_forms)
        report = diff_payloads(_wrap_overview(v1), _wrap_overview(v3), _overview_only_contract())
        assert report.is_match, report.format()
