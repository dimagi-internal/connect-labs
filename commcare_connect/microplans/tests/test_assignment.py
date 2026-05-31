"""Tests for the assignment strategies (Phase 2)."""

from __future__ import annotations

import pytest

from commcare_connect.microplans.core.assignment import AssignmentConfig, _diameter, _haversine, assign_groups_to_chws


def _cell(wa_id: str, group: str, lon: float, lat: float, buildings: int = 10, worker: str | None = None) -> dict:
    return {
        "id": wa_id,
        "centroid": [lon, lat],
        "work_area_group": group,
        "building_count": buildings,
        "status": "UNASSIGNED",
        "opportunity_access": worker,
    }


def _grid_groups(groups: int, cells_per_group: int = 5, building_count: int = 10, spread: float = 0.05) -> list[dict]:
    """G groups, each cells_per_group cells in a tight cluster; groups spaced
    `spread` degrees apart on the lon axis (~5 km at the equator for spread=0.05)."""
    out = []
    for g in range(groups):
        cx = g * spread
        for c in range(cells_per_group):
            out.append(
                _cell(
                    wa_id=f"g{g}-c{c}",
                    group=f"group-{g + 1}",
                    lon=cx + 0.0001 * c,  # tight within group
                    lat=0.0,
                    buildings=building_count,
                )
            )
    return out


class TestRoundRobin:
    def test_distributes_evenly(self):
        cells = _grid_groups(groups=6)  # 6 groups
        cfg = AssignmentConfig(strategy="round_robin", workers=["a", "b", "c"])
        assign_groups_to_chws(cells, cfg)
        from collections import Counter

        per_worker = Counter(c["opportunity_access"] for c in cells)
        # 6 groups × 5 cells = 30 cells; round-robin across 3 → 10 each
        assert per_worker == {"a": 10, "b": 10, "c": 10}


class TestMinimaxSpread:
    def test_three_clusters_three_chws_each_gets_one(self):
        # Three tight clusters separated by spread=0.5° (~55 km). Optimal: one
        # CHW per cluster, each diameter ≈ 0.
        cells = _grid_groups(groups=3, cells_per_group=4, spread=0.5)
        cfg = AssignmentConfig(strategy="minimax_spread", workers=["a", "b", "c"], restarts=10)
        assign_groups_to_chws(cells, cfg)
        from collections import defaultdict

        per_worker = defaultdict(list)
        for c in cells:
            per_worker[c["opportunity_access"]].append(c["centroid"])
        # Each worker covers exactly one group
        assert len(per_worker) == 3
        for centroids in per_worker.values():
            assert _diameter(centroids) < 1.0  # well under a km

    def test_more_groups_than_chws_doesnt_break(self):
        cells = _grid_groups(groups=8, cells_per_group=3, spread=0.05)  # ~5km spacing
        cfg = AssignmentConfig(strategy="minimax_spread", workers=["a", "b", "c"], restarts=5)
        assign_groups_to_chws(cells, cfg)
        assigned = [c["opportunity_access"] for c in cells]
        assert all(w in ("a", "b", "c") for w in assigned)
        # Every CHW gets at least one group (greedy seeds the first n_workers
        # groups across all workers)
        from collections import Counter

        ct = Counter(assigned)
        assert all(ct[w] > 0 for w in ["a", "b", "c"])

    def test_beats_round_robin_on_spread_metric(self):
        # Three clusters far apart. Round-robin will scatter groups; minimax
        # should keep each CHW's territory tight.
        cells_rr = _grid_groups(groups=6, cells_per_group=3, spread=0.5)
        cells_mm = [dict(c) for c in cells_rr]
        assign_groups_to_chws(cells_rr, AssignmentConfig(strategy="round_robin", workers=["a", "b", "c"]))
        assign_groups_to_chws(
            cells_mm, AssignmentConfig(strategy="minimax_spread", workers=["a", "b", "c"], restarts=10)
        )

        def max_diameter(cells):
            from collections import defaultdict

            per = defaultdict(list)
            for c in cells:
                per[c["opportunity_access"]].append(c["centroid"])
            return max(_diameter(v) for v in per.values())

        assert max_diameter(cells_mm) < max_diameter(cells_rr)


class TestManualPreservesAssignments:
    def test_manual_is_noop(self):
        cells = _grid_groups(groups=3)
        # Pre-assign manually
        for c in cells[:5]:
            c["opportunity_access"] = "pre-set"
        cfg = AssignmentConfig(strategy="manual", workers=[])
        assign_groups_to_chws(cells, cfg)
        assert cells[0]["opportunity_access"] == "pre-set"


class TestPayloadParsing:
    def test_workers_from_newline_string(self):
        cfg = AssignmentConfig.from_payload({"strategy": "round_robin", "workers": "a\nb\nc"})
        assert cfg.workers == ["a", "b", "c"]

    def test_workers_from_comma_string(self):
        cfg = AssignmentConfig.from_payload({"strategy": "round_robin", "workers": "a, b ,c"})
        assert cfg.workers == ["a", "b", "c"]

    def test_strategy_requires_workers(self):
        with pytest.raises(ValueError):
            AssignmentConfig.from_payload({"strategy": "minimax_spread", "workers": []})

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError):
            AssignmentConfig.from_payload({"strategy": "magic"})


class TestHaversine:
    def test_known_distance(self):
        # New York to London ≈ 5570 km
        ny = (-74.006, 40.713)
        lon = (-0.128, 51.508)
        d = _haversine(ny, lon)
        assert 5500 < d < 5650

    def test_identical_centroids_no_domain_error(self):
        # Fix B: two identical (or near-identical) centroids must not raise
        # "math domain error" from asin.  Before the clamp this triggered when
        # floating-point rounding pushed sin^2 + cos*cos*sin^2 slightly above 1.
        p = (3.123456789, 6.987654321)
        q = (3.123456789, 6.987654321)  # exact duplicate → a can exceed 1.0
        d = _haversine(p, q)
        assert d == pytest.approx(0.0, abs=1e-9)

    def test_near_identical_centroids_no_domain_error(self):
        # ~1e-9 degree apart — still triggers the FP edge case without the clamp.
        p = (3.0, 6.0)
        q = (3.0 + 1e-9, 6.0 + 1e-9)
        d = _haversine(p, q)
        assert d >= 0.0


class TestIdenticalCentroidAssignment:
    """Fix B integration: assign_groups_to_chws with identical centroids must not
    raise ValueError: math domain error."""

    def test_assign_groups_identical_centroids_no_crash(self):
        # Two work areas in different groups but at exactly the same lat/lon.
        # The minimax_spread path calls _diameter → _haversine on these, which
        # (before the clamp) could blow up with a math domain error.
        same_lon, same_lat = 3.123456789, 6.987654321
        cells = [
            _cell("g1-c0", "group-1", same_lon, same_lat, buildings=10),
            _cell("g2-c0", "group-2", same_lon, same_lat, buildings=10),
        ]
        cfg = AssignmentConfig(strategy="minimax_spread", workers=["alice", "bob"], restarts=5)
        # Must complete without raising
        result = assign_groups_to_chws(cells, cfg)
        assert all(c["opportunity_access"] in ("alice", "bob") for c in result)
