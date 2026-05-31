"""Tests for the grouping strategies (Phase 1)."""

from __future__ import annotations

from commcare_connect.microplans.core.grouping import GroupingConfig, group_work_areas


def _cell(wa_id: str, lon: float, lat: float, building_count: int = 10) -> dict:
    """One synthetic cell. Geometry is a 0.001° square around the centroid so the
    BFS adjacency check can find shared edges between neighbours that are exactly
    0.001° apart on lon or lat."""
    d = 0.0005
    return {
        "id": wa_id,
        "centroid": [lon, lat],
        "building_count": building_count,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - d, lat - d],
                    [lon + d, lat - d],
                    [lon + d, lat + d],
                    [lon - d, lat + d],
                    [lon - d, lat - d],
                ]
            ],
        },
        "work_area_group": "intervention",
        "status": "UNASSIGNED",
    }


def _grid(rows: int, cols: int, building_count: int = 10) -> list[dict]:
    """A rows×cols grid of cells centered at (0, 0). Step = 0.001° = ~110 m."""
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append(_cell(f"C{r}-{c}", 0.001 * c, 0.001 * r, building_count))
    return out


class TestBboxBucket:
    def test_splits_cells_into_super_grid(self):
        cells = _grid(6, 6, building_count=10)  # 36 cells
        group_work_areas(cells, GroupingConfig(strategy="bbox", target_size=9))
        # 36 / 9 = 4 → sqrt(4) = 2 → 2x2 super-grid → 4 groups
        groups = {c["work_area_group"] for c in cells}
        assert len(groups) == 4
        # Names are dense from 1
        assert groups == {"group-1", "group-2", "group-3", "group-4"}

    def test_degenerate_single_cell(self):
        cells = _grid(1, 1)
        group_work_areas(cells, GroupingConfig(strategy="bbox", target_size=30))
        assert cells[0]["work_area_group"] == "group-1"


class TestBfsAdjacency:
    def test_builds_contiguous_groups_capped_by_buildings(self):
        cells = _grid(6, 6, building_count=10)  # 36 cells × 10 buildings = 360 total
        # With max_buildings=100, each group holds 10 cells max
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", max_buildings=100))
        groups: dict[str, list[str]] = {}
        for c in cells:
            groups.setdefault(c["work_area_group"], []).append(c["id"])
        # 36 cells / 10-cell cap → ~4 groups
        assert 3 <= len(groups) <= 5
        # Every group respects the building cap (with one allowance for a possibly
        # oversized seed cell — Connect's algorithm also tolerates this).
        for cluster in groups.values():
            total = sum(10 for _ in cluster)  # each cell is 10 buildings
            assert total <= 100 or len(cluster) == 1

    def test_oversized_single_cell_lands_in_its_own_group(self):
        # One huge cell + a normal one separated > buffer (should NOT merge).
        cells = [
            _cell("big", 0.0, 0.0, building_count=500),
            _cell("small", 0.5, 0.0, building_count=10),
        ]
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", max_buildings=200, buffer_distance_m=50))
        big = next(c for c in cells if c["id"] == "big")
        small = next(c for c in cells if c["id"] == "small")
        assert big["work_area_group"] != small["work_area_group"]

    def test_distant_clusters_dont_merge(self):
        # Two 3x3 grids far apart — should produce >= 2 groups.
        left = _grid(3, 3, building_count=5)
        for c in left:
            c["id"] = f"L-{c['id']}"
        right = _grid(3, 3, building_count=5)
        for c in right:
            c["id"] = f"R-{c['id']}"
            c["centroid"][0] += 1.0  # 1° east
            c["geometry"]["coordinates"][0] = [[p[0] + 1.0, p[1]] for p in c["geometry"]["coordinates"][0]]
        cells = left + right
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", max_buildings=1000, buffer_distance_m=100))
        l_groups = {c["work_area_group"] for c in cells if c["id"].startswith("L-")}
        r_groups = {c["work_area_group"] for c in cells if c["id"].startswith("R-")}
        assert l_groups.isdisjoint(r_groups)


class TestBfsAdjacencyBadGeometry:
    """Fix A: malformed/unparseable geometry must not crash the regroup."""

    def test_empty_coordinates_polygon_is_skipped_not_fatal(self):
        # One area with a well-formed geometry, one with an empty-coordinates polygon
        # (shapely raises on this), one with geometry=None.
        good = _cell("good", 0.0, 0.0, building_count=20)
        bad_empty_coords = {
            "id": "bad-empty",
            "centroid": [0.001, 0.0],
            "building_count": 5,
            "geometry": {"type": "Polygon", "coordinates": []},
            "work_area_group": "intervention",
            "status": "UNASSIGNED",
        }
        no_geom = {
            "id": "no-geom",
            "centroid": [0.002, 0.0],
            "building_count": 5,
            "geometry": None,
            "work_area_group": "intervention",
            "status": "UNASSIGNED",
        }
        cells = [good, bad_empty_coords, no_geom]
        # Must complete without raising
        result = group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency"))
        # Every cell has a group label (no crash)
        for c in result:
            assert c.get("work_area_group"), f"cell {c['id']} has no group"
        # The two bad cells land in the sentinel group, not a real BFS group
        assert bad_empty_coords["work_area_group"] == "group-no-geometry"
        assert no_geom["work_area_group"] == "group-no-geometry"
        # The good cell lands in a real BFS group
        assert good["work_area_group"] != "group-no-geometry"

    def test_invalid_geometry_type_is_skipped_not_fatal(self):
        # geometry dict present but unrecognised type — shapely should raise TypeError/ValueError
        good = _cell("good", 0.0, 0.0, building_count=20)
        bad_type = {
            "id": "bad-type",
            "centroid": [0.001, 0.0],
            "building_count": 5,
            "geometry": {"type": "NotARealType", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            "work_area_group": "intervention",
            "status": "UNASSIGNED",
        }
        cells = [good, bad_type]
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency"))
        assert bad_type["work_area_group"] == "group-no-geometry"
        assert good["work_area_group"] != "group-no-geometry"


class TestGroupingConfigPayload:
    def test_defaults_to_bfs(self):
        cfg = GroupingConfig.from_payload({})
        assert cfg.strategy == "bfs_adjacency"
        assert cfg.max_buildings == 200
        assert cfg.buffer_distance_m == 100

    def test_unknown_strategy_raises(self):
        import pytest

        with pytest.raises(ValueError):
            GroupingConfig.from_payload({"strategy": "magic"})

    def test_overrides(self):
        cfg = GroupingConfig.from_payload({"strategy": "bbox", "target_size": 50})
        assert cfg.strategy == "bbox"
        assert cfg.target_size == 50
