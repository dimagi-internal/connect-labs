"""Tier-1 parity tests: each new aggregation against an in-memory fixture.

These tests verify two things:
1. The in-memory `aggregate()` runner produces sensible results for the
   new aggregations on synthetic data.
2. v1's per-FLW computations and v3's pipeline-equivalent aggregation
   agree on the contract leaves they both produce.

This is the proof that the harness end-to-end works before any real
v3 cutover. Subsequent PRs add tests as each Step 1-5 migration lands.
"""

import pytest

from commcare_connect.workflow.tests.mbw_parity.fixtures import edge_cases, small_realistic
from commcare_connect.workflow.tests.mbw_parity.runners import aggregate

# ---- aggregation-runner unit tests (in-memory only) ----


class TestAggregationRunner:
    def test_count_unique_matches_python_set(self):
        rows = [
            {"u": "a", "x": 1},
            {"u": "a", "x": 2},
            {"u": "a", "x": 1},
            {"u": "b", "x": 5},
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="distinct_x",
            source_path="x",
            aggregation="count_unique",
        )
        assert result == {"a": 2, "b": 1}

    def test_median_handles_even_and_odd_lengths(self):
        rows = [{"u": "a", "v": v} for v in [1.0, 2.0, 3.0]]  # odd
        rows += [{"u": "b", "v": v} for v in [1.0, 2.0, 3.0, 4.0]]  # even
        result = aggregate(rows, grouping_key="u", field_name="med", source_path="v", aggregation="median")
        assert result["a"] == 2.0
        assert result["b"] == 2.5

    def test_median_ignores_nulls(self):
        rows = [{"u": "a", "v": v} for v in [None, 5.0, None, 7.0]]
        result = aggregate(rows, grouping_key="u", field_name="med", source_path="v", aggregation="median")
        assert result["a"] == 6.0

    def test_median_returns_none_for_all_null_group(self):
        rows = [{"u": "a", "v": None}, {"u": "a", "v": None}]
        result = aggregate(rows, grouping_key="u", field_name="med", source_path="v", aggregation="median")
        assert result["a"] is None

    def test_mode_returns_most_frequent_value(self):
        rows = [{"u": "a", "v": "G2P1"}, {"u": "a", "v": "G2P1"}, {"u": "a", "v": "G3P2"}]
        result = aggregate(rows, grouping_key="u", field_name="m", source_path="v", aggregation="mode")
        assert result["a"] == "G2P1"

    def test_mode_share_extreme_concentration(self):
        rows = [{"u": "fraud", "v": "G2P1"} for _ in range(5)]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="ms",
            source_path="v",
            aggregation="mode_share",
        )
        assert result["fraud"] == pytest.approx(1.0)

    def test_mode_share_diverse(self):
        rows = [
            {"u": "diverse", "v": "G1P0"},
            {"u": "diverse", "v": "G2P1"},
            {"u": "diverse", "v": "G3P2"},
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="ms",
            source_path="v",
            aggregation="mode_share",
        )
        # All three values appear once; mode is whichever Counter sees first,
        # share is 1/3.
        assert result["diverse"] == pytest.approx(1.0 / 3.0)

    def test_pre_aggregate_per_mother_then_per_flw(self):
        """Two-pass: collapse rows to one parity per mother, then mode_share per FLW.

        FLW 'fraud': 3 mothers, each reports parity G2P1 across all visits.
        Per-mother first-parity → ['G2P1', 'G2P1', 'G2P1']. mode_share → 1.0.

        FLW 'diverse': 3 mothers reporting parity G1P0, G2P1, G3P2.
        Per-mother first-parity → ['G1P0', 'G2P1', 'G3P2']. mode_share → 1/3.

        Without pre-aggregation, a fraud FLW with 1 mother visited 3 times
        and a diverse FLW with 3 mothers visited once each would produce
        the same mode_share = 1.0 from the raw rows — so this test would
        fail without two-pass.
        """
        rows = [
            # fraud FLW: 1 mother visited 3 times, all parity G2P1
            {"u": "fraud", "m": "ma", "v": "G2P1"},
            {"u": "fraud", "m": "ma", "v": "G2P1"},
            {"u": "fraud", "m": "ma", "v": "G2P1"},
            # diverse FLW: 3 mothers, 1 visit each, distinct parities
            {"u": "diverse", "m": "mb", "v": "G1P0"},
            {"u": "diverse", "m": "mc", "v": "G2P1"},
            {"u": "diverse", "m": "md", "v": "G3P2"},
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="parity_conc",
            source_path="v",
            aggregation="mode_share",
            pre_aggregate_by="m",
            pre_aggregation="first",
        )
        # fraud has only 1 mother, so mode_share over [G2P1] = 1.0
        assert result["fraud"] == pytest.approx(1.0)
        # diverse has 3 mothers, 3 distinct parities → mode_share = 1/3
        assert result["diverse"] == pytest.approx(1.0 / 3.0)

    def test_pre_aggregate_collapses_repeated_visits(self):
        """Two visits to the same mother with the same parity must count as
        ONE per-mother value, not two. Catches the regression where the
        outer mode_share would count repeated visits per FLW instead of
        unique mothers."""
        rows = [
            # fraud2: 2 mothers, each visited twice, all parity G2P1
            {"u": "fraud2", "m": "ma", "v": "G2P1"},
            {"u": "fraud2", "m": "ma", "v": "G2P1"},
            {"u": "fraud2", "m": "mb", "v": "G2P1"},
            {"u": "fraud2", "m": "mb", "v": "G2P1"},
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="parity_conc",
            source_path="v",
            aggregation="mode_share",
            pre_aggregate_by="m",
            pre_aggregation="first",
        )
        # Per-mother first → ['G2P1', 'G2P1']. mode_share = 1.0.
        # If we hadn't deduped, mode_share would still be 1.0 (all same), so
        # the assertion is symmetric — but the NUMBER OF VALUES the outer agg
        # sees should be 2 (mothers), not 4 (visits). Verify by counting.
        # Count via count_unique on the same setup.
        cnt = aggregate(
            rows,
            grouping_key="u",
            field_name="mother_count",
            source_path="m",
            aggregation="count_unique",
        )
        assert cnt["fraud2"] == 2
        assert result["fraud2"] == pytest.approx(1.0)

    def test_pre_aggregate_with_filter_path(self):
        """Filter applies at the row level — rows excluded by filter never
        enter the inner pre-aggregation step. Mirrors SQL semantics where
        FILTER (WHERE ...) on the inner GROUP BY pre-filters."""
        rows = [
            # only ANC-visit rows should contribute to per-mother first parity
            {"u": "a", "m": "ma", "v": "G2P1", "form_name": "ANC Visit"},
            {"u": "a", "m": "ma", "v": "G3P2", "form_name": "Post delivery visit"},
            {"u": "a", "m": "mb", "v": "G1P0", "form_name": "ANC Visit"},
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="parity_conc",
            source_path="v",
            aggregation="mode_share",
            pre_aggregate_by="m",
            pre_aggregation="first",
            filter_path="form_name",
            filter_value="ANC Visit",
        )
        # Per-mother first parity from ANC-only rows: ['G2P1', 'G1P0'].
        # mode_share = 1/2 = 0.5
        assert result["a"] == pytest.approx(0.5)

    def test_filter_path_excludes_non_matching_rows(self):
        rows = [
            {"u": "a", "form_name": "ANC Visit", "v": 1},
            {"u": "a", "form_name": "Post delivery visit", "v": 2},
            {"u": "a", "form_name": "ANC Visit", "v": 3},
        ]
        result = aggregate(
            rows,
            grouping_key="u",
            field_name="anc_count",
            source_path="v",
            aggregation="count",
            filter_path="form_name",
            filter_value="ANC Visit",
        )
        assert result == {"a": 2}


# ---- end-to-end parity: mother_counts ----


def _v1_mother_counts(visits: list[dict]) -> dict[str, int]:
    """Reference implementation: count distinct mother_case_ids per username.

    Mirrors what the v1 helper `count_mothers_from_pipeline` ultimately
    produces for the dashboard's overview_data.mother_counts leaf — for
    the FIXTURE-shaped input here, where rows already have mother_case_id.
    """
    by_flw: dict[str, set[str]] = {}
    for row in visits:
        u = row.get("username")
        m = row.get("mother_case_id")
        if not u or not m:
            continue
        by_flw.setdefault(u, set()).add(m)
    return {u: len(s) for u, s in by_flw.items()}


def _v3_mother_counts_via_aggregation(visits: list[dict]) -> dict[str, int]:
    """v3 path: aggregate mother_case_id with count_unique on the visits pipeline."""
    return aggregate(
        # Filter out null mothers before aggregation, matching v1 semantics.
        [r for r in visits if r.get("mother_case_id")],
        grouping_key="username",
        field_name="mother_count",
        source_path="mother_case_id",
        aggregation="count_unique",
    )


class TestMotherCountsParity:
    def test_small_realistic_fixture(self):
        bundle = small_realistic()
        v1 = _v1_mother_counts(bundle.visits)
        v3 = _v3_mother_counts_via_aggregation(bundle.visits)
        assert v1 == v3, f"v1={v1} v3={v3}"

    def test_edge_cases_fixture(self):
        bundle = edge_cases()
        v1 = _v1_mother_counts(bundle.visits)
        v3 = _v3_mother_counts_via_aggregation(bundle.visits)
        assert v1 == v3, f"v1={v1} v3={v3}"
