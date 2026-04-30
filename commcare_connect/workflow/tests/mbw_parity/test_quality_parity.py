"""End-to-end v1↔v3 parity tests for the dashboard's quality_metrics block.

PR #3 covers the parity_concentration slice only — the other quality
leaves (phone_dup_pct, age_concentration, anc_pnc_same_date_count,
age_equals_reg_pct) need cross-pipeline JOIN and per-mother extraction
across multiple form types, both planned for future PRs.

The parity here exercises the new `pre_aggregate_by` / `pre_aggregation`
two-pass primitive: per-FLW concentration of parity values requires
collapsing rows to one parity per mother first, then computing
mode_share/mode over those per-mother values.
"""

import pytest

from commcare_connect.workflow.tests.mbw_parity.diff import diff_payloads
from commcare_connect.workflow.tests.mbw_parity.fixtures import edge_cases, small_realistic
from commcare_connect.workflow.tests.mbw_parity.payload_contract import DASHBOARD_CONTRACT, Leaf
from commcare_connect.workflow.tests.mbw_parity.runners import compute_v1_quality_reference, compute_v3_quality


def _quality_only_contract() -> list[Leaf]:
    return [leaf for leaf in DASHBOARD_CONTRACT if leaf.path.startswith("quality_metrics")]


def _wrap_quality(quality_dict: dict) -> dict:
    return {"quality_metrics": quality_dict}


class TestQualityParity:
    def test_parity_concentration_small_realistic(self):
        bundle = small_realistic()
        v1 = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_quality(bundle.visits, bundle.registrations, bundle.gs_forms)
        # Same set of FLWs reporting parity from ANC visits.
        assert set(v1.keys()) == set(v3.keys())
        for flw in v1:
            v1_pc = v1[flw]["parity_concentration"]
            v3_pc = v3[flw]["parity_concentration"]
            assert v1_pc["mode_pct"] == v3_pc["mode_pct"], f"mode_pct mismatch for {flw}: v1={v1_pc} v3={v3_pc}"
            assert v1_pc["mode_value"] == v3_pc["mode_value"]
            assert (
                v1_pc["pct_duplicate"] == v3_pc["pct_duplicate"]
            ), f"pct_duplicate mismatch for {flw}: v1={v1_pc} v3={v3_pc}"

    def test_parity_concentration_edge_cases(self):
        bundle = edge_cases()
        v1 = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_quality(bundle.visits, bundle.registrations, bundle.gs_forms)
        assert set(v1.keys()) == set(v3.keys()), f"v1 keys={set(v1)} v3 keys={set(v3)}"

    def test_fraud_vs_diverse_distinguished(self):
        """Hand-crafted scenario: per-mother dedup is the only thing that
        distinguishes a fraud-pattern FLW from a diverse-mother FLW. v1 and
        v3 must agree on the discriminating numbers.
        """
        # Synthesize one fixture inline — fraud FLW has 1 mother visited
        # 3 times with same parity; diverse FLW has 3 mothers each visited
        # once with distinct parities. After per-mother dedup:
        #   fraud:  1 parity value  → mode_pct = 100, mode = "G2P1"
        #   diverse: 3 parity values, all distinct → mode_pct = 33, mode = first one seen
        visits = [
            {"username": "fraud", "mother_case_id": "ma", "parity": "G2P1", "form_name": "ANC Visit"},
            {"username": "fraud", "mother_case_id": "ma", "parity": "G2P1", "form_name": "ANC Visit"},
            {"username": "fraud", "mother_case_id": "ma", "parity": "G2P1", "form_name": "ANC Visit"},
            {"username": "diverse", "mother_case_id": "mb", "parity": "G1P0", "form_name": "ANC Visit"},
            {"username": "diverse", "mother_case_id": "mc", "parity": "G2P1", "form_name": "ANC Visit"},
            {"username": "diverse", "mother_case_id": "md", "parity": "G3P2", "form_name": "ANC Visit"},
        ]
        v1 = compute_v1_quality_reference(visits, [], [])
        v3 = compute_v3_quality(visits, [], [])

        assert v1["fraud"]["parity_concentration"]["mode_pct"] == 100
        assert v3["fraud"]["parity_concentration"]["mode_pct"] == 100
        # Diverse: 3 mothers, 3 distinct values → 33 (rounded from 33.33)
        assert v1["diverse"]["parity_concentration"]["mode_pct"] == 33
        assert v3["diverse"]["parity_concentration"]["mode_pct"] == 33

    def test_anc_filter_excludes_post_delivery_parity_writes(self):
        """V1 only writes to parity_by_mother from ANC visits. A Post
        delivery visit with a parity field must NOT contribute. v3's filter
        clause (form_name=="ANC Visit") must replicate this."""
        visits = [
            {"username": "a", "mother_case_id": "m1", "parity": "G2P1", "form_name": "ANC Visit"},
            # Same mother, same FLW, different parity, but Post delivery — must be ignored.
            {"username": "a", "mother_case_id": "m1", "parity": "G99P99", "form_name": "Post delivery visit"},
        ]
        v1 = compute_v1_quality_reference(visits, [], [])
        v3 = compute_v3_quality(visits, [], [])
        assert v1["a"]["parity_concentration"]["mode_value"] == "G2P1"
        assert v3["a"]["parity_concentration"]["mode_value"] == "G2P1"


class TestQualityContractWalk:
    @pytest.mark.parametrize("bundle_factory", [small_realistic, edge_cases])
    def test_full_quality_parity(self, bundle_factory):
        bundle = bundle_factory()
        v1 = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v3 = compute_v3_quality(bundle.visits, bundle.registrations, bundle.gs_forms)
        report = diff_payloads(_wrap_quality(v1), _wrap_quality(v3), _quality_only_contract())
        assert report.is_match, report.format()
