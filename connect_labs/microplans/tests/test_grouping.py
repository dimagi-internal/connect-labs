"""Tests for the grouping strategies (Phase 1)."""

from __future__ import annotations

from connect_labs.microplans.core.grouping import (
    GroupingConfig,
    _absorb_enclosed_clusters,
    _reassign_dominated_cells,
    _reassign_isolated_pieces_touching_other_wags,
    group_work_areas,
)


def _cell(wa_id: str, lon: float, lat: float, building_count: int = 10, ward: str | None = None) -> dict:
    """One synthetic cell. Geometry is a 0.001° square around the centroid so the
    BFS adjacency check can find shared edges between neighbours that are exactly
    0.001° apart on lon or lat."""
    d = 0.0005
    cell = {
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
    if ward is not None:
        cell["properties"] = {"ward": ward}
    return cell


def _grid(rows: int, cols: int, building_count: int = 10, ward: str | None = None) -> list[dict]:
    """A rows×cols grid of cells centered at (0, 0). Step = 0.001° = ~110 m."""
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append(_cell(f"C{r}-{c}", 0.001 * c, 0.001 * r, building_count, ward=ward))
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

    def test_ward_prefix_added_when_ward_present(self):
        cells = [_cell("a", 0.0, 0.0, ward="Kano North")]
        group_work_areas(cells, GroupingConfig(strategy="bbox", target_size=30))
        assert cells[0]["work_area_group"] == "KAN-group-1"

    def test_no_prefix_when_ward_absent(self):
        cells = [_cell("a", 0.0, 0.0)]  # no ward -> unchanged legacy naming
        group_work_areas(cells, GroupingConfig(strategy="bbox", target_size=30))
        assert cells[0]["work_area_group"] == "group-1"

    def test_numbering_restarts_per_ward(self):
        # Two wards sharing one bbox tiling — each ward's OWN bucket numbers must
        # start at 1 and run consecutively, independent of the other ward's count.
        cells = _grid(6, 6, building_count=10, ward="Kano North")
        for c in cells:
            c["id"] = f"K-{c['id']}"
        madobi = _grid(6, 6, building_count=10, ward="Madobi")
        for c in madobi:
            c["id"] = f"M-{c['id']}"
            c["centroid"][1] += 10.0  # push far away so buckets don't overlap
        all_cells = cells + madobi
        group_work_areas(all_cells, GroupingConfig(strategy="bbox", target_size=9))
        k_groups = {c["work_area_group"] for c in all_cells if c["id"].startswith("K-")}
        m_groups = {c["work_area_group"] for c in all_cells if c["id"].startswith("M-")}
        assert all(g.startswith("KAN-group-") for g in k_groups)
        assert all(g.startswith("MAD-group-") for g in m_groups)
        k_nums = sorted(int(g.rsplit("-", 1)[1]) for g in k_groups)
        m_nums = sorted(int(g.rsplit("-", 1)[1]) for g in m_groups)
        assert k_nums == list(range(1, len(k_nums) + 1))
        # The crucial assertion: Madobi restarts at 1 rather than continuing
        # Kano North's running count.
        assert m_nums == list(range(1, len(m_nums) + 1))
        assert m_nums[0] == 1


class TestBfsAdjacency:
    def test_builds_contiguous_groups_capped_by_buildings(self):
        cells = _grid(6, 6, building_count=10)  # 36 cells × 10 buildings = 360 total
        # With max_buildings=100, each group holds 10 cells max
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=100))
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
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=200, buffer_distance_m=50))
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
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=1000, buffer_distance_m=100))
        l_groups = {c["work_area_group"] for c in cells if c["id"].startswith("L-")}
        r_groups = {c["work_area_group"] for c in cells if c["id"].startswith("R-")}
        assert l_groups.isdisjoint(r_groups)

    def test_ward_prefix_from_cluster_seed(self):
        # A whole contiguous cluster shares ONE label (from its seed cell's ward),
        # even though every cell in the grid was tagged with the same ward here.
        cells = _grid(3, 3, building_count=5)
        for c in cells:
            c["properties"] = {"ward": "Dabi"}
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=1000))
        groups = {c["work_area_group"] for c in cells}
        assert groups == {"DAB-group-1"}

    def test_distant_clusters_get_own_ward_prefix(self):
        left = _grid(3, 3, building_count=5)
        for c in left:
            c["id"] = f"L-{c['id']}"
            c["properties"] = {"ward": "Kano North"}
        right = _grid(3, 3, building_count=5)
        for c in right:
            c["id"] = f"R-{c['id']}"
            c["centroid"][0] += 1.0
            c["geometry"]["coordinates"][0] = [[p[0] + 1.0, p[1]] for p in c["geometry"]["coordinates"][0]]
            c["properties"] = {"ward": "Madobi"}
        cells = left + right
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=1000, buffer_distance_m=100))
        l_groups = {c["work_area_group"] for c in cells if c["id"].startswith("L-")}
        r_groups = {c["work_area_group"] for c in cells if c["id"].startswith("R-")}
        assert all(g.startswith("KAN-") for g in l_groups)
        assert all(g.startswith("MAD-") for g in r_groups)
        # Numbering restarts at 1 per ward, rather than continuing a shared count —
        # each ward here has exactly one cluster, so both are "-group-1".
        assert l_groups == {"KAN-group-1"}
        assert r_groups == {"MAD-group-1"}

    def test_numbering_restarts_per_ward_across_multiple_clusters(self):
        # Kano North gets TWO separate (far-apart) clusters; Madobi gets one.
        # Madobi must come out as "MAD-group-1", not "MAD-group-3".
        kan1 = _grid(3, 3, building_count=5)
        for c in kan1:
            c["id"] = f"KAN1-{c['id']}"
            c["properties"] = {"ward": "Kano North"}
        kan2 = _grid(3, 3, building_count=5)
        for c in kan2:
            c["id"] = f"KAN2-{c['id']}"
            c["centroid"][0] += 2.0
            c["geometry"]["coordinates"][0] = [[p[0] + 2.0, p[1]] for p in c["geometry"]["coordinates"][0]]
            c["properties"] = {"ward": "Kano North"}
        mad = _grid(3, 3, building_count=5)
        for c in mad:
            c["id"] = f"MAD-{c['id']}"
            c["centroid"][0] += 1.0
            c["geometry"]["coordinates"][0] = [[p[0] + 1.0, p[1]] for p in c["geometry"]["coordinates"][0]]
            c["properties"] = {"ward": "Madobi"}
        cells = kan1 + kan2 + mad
        group_work_areas(cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=1000, buffer_distance_m=100))
        kan_groups = {c["work_area_group"] for c in cells if c["id"].startswith("KAN")}
        mad_groups = {c["work_area_group"] for c in cells if c["id"].startswith("MAD-")}
        assert kan_groups == {"KAN-group-1", "KAN-group-2"}
        assert mad_groups == {"MAD-group-1"}


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
            "properties": {"ward": "Dabi"},
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
        # ...still ward-prefixed per-cell when a ward is available
        assert no_geom["work_area_group"] == "DAB-group-no-geometry"
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
        assert cfg.target_buildings == 200
        assert cfg.buffer_distance_m == 100
        # New fields default to "no headroom, top-up disabled" — exact pre-2026-07
        # behaviour for any caller that doesn't opt into them.
        assert cfg.effective_max_buildings == 200
        assert cfg.min_buildings == 0

    def test_legacy_max_buildings_only_maps_to_target(self):
        # Old-format payload (pre-2026-07 field name, no target_buildings key):
        # must behave exactly as it used to — max_buildings meant the cap.
        cfg = GroupingConfig.from_payload({"strategy": "bfs_adjacency", "max_buildings": 150})
        assert cfg.target_buildings == 150
        assert cfg.effective_max_buildings == 150  # no accidental new headroom

    def test_new_payload_splits_target_and_max(self):
        cfg = GroupingConfig.from_payload(
            {"strategy": "bfs_adjacency", "target_buildings": 250, "max_buildings": 300, "min_buildings": 100}
        )
        assert cfg.target_buildings == 250
        assert cfg.effective_max_buildings == 300
        assert cfg.min_buildings == 100

    def test_max_reach_defaults_to_buffer_distance(self):
        cfg = GroupingConfig.from_payload({"strategy": "bfs_adjacency", "buffer_distance_m": 150})
        assert cfg.effective_max_reach_m == 150

    def test_max_reach_explicit_zero_is_honored(self):
        cfg = GroupingConfig.from_payload({"strategy": "bfs_adjacency", "max_reach_m": 0})
        assert cfg.effective_max_reach_m == 0

    def test_unknown_strategy_raises(self):
        import pytest

        with pytest.raises(ValueError):
            GroupingConfig.from_payload({"strategy": "magic"})

    def test_overrides(self):
        cfg = GroupingConfig.from_payload({"strategy": "bbox", "target_size": 50})
        assert cfg.strategy == "bbox"
        assert cfg.target_size == 50


class TestTopUpMergeAndSteal:
    """Phase-2: undersized groups merge into (or steal from) a nearby neighbour."""

    def test_disabled_by_default_min_buildings_zero(self):
        # A 250-building village + an 80-building leftover — with no min_buildings
        # set, the leftover must stay exactly as it was pre-2026-07: standalone.
        cells = _grid(5, 5, building_count=10, ward="Dabi")  # 25 cells x 10 = 250
        leftover = [_cell(f"X{c}", 0.001 * c, -0.001, building_count=10, ward="Dabi") for c in range(8)]
        all_cells = cells + leftover
        group_work_areas(
            all_cells, GroupingConfig(strategy="bfs_adjacency", target_buildings=250, buffer_distance_m=100)
        )
        totals = {}
        for w in all_cells:
            totals[w["work_area_group"]] = totals.get(w["work_area_group"], 0) + w["building_count"]
        assert sorted(totals.values()) == [80, 250]

    def test_small_remainder_whole_merges_into_the_village(self):
        # Leftover (30) + village (250) fit comfortably under max=300 -> whole merge,
        # keeping the village's own (bigger) group identity.
        cells = _grid(5, 5, building_count=10, ward="Dabi")  # 250
        leftover = [_cell(f"X{c}", 0.001 * c, -0.001, building_count=10, ward="Dabi") for c in range(3)]  # 30
        all_cells = cells + leftover
        group_work_areas(
            all_cells,
            GroupingConfig(
                strategy="bfs_adjacency",
                target_buildings=250,
                buffer_distance_m=100,
                min_buildings=100,
                max_buildings=300,
                max_reach_m=150,
            ),
        )
        groups = {w["work_area_group"] for w in all_cells}
        assert groups == {"DAB-group-1"}  # ONE merged group, village's label survives
        assert sum(w["building_count"] for w in all_cells) == 280

    def test_large_remainder_steals_when_whole_merge_would_breach_max(self):
        # Leftover (80) + village (250) = 330 > max=300, so a whole merge is
        # rejected — the leftover must instead STEAL just enough (up to min=100)
        # from the village, which shrinks but stays well over its own floor.
        cells = _grid(5, 5, building_count=10, ward="Dabi")  # 250
        leftover = [_cell(f"X{c}", 0.001 * c, -0.001, building_count=10, ward="Dabi") for c in range(8)]  # 80
        all_cells = cells + leftover
        group_work_areas(
            all_cells,
            GroupingConfig(
                strategy="bfs_adjacency",
                target_buildings=250,
                buffer_distance_m=100,
                min_buildings=100,
                max_buildings=300,
                max_reach_m=150,
            ),
        )
        totals = {}
        for w in all_cells:
            totals[w["work_area_group"]] = totals.get(w["work_area_group"], 0) + w["building_count"]
        # Total buildings conserved; the small group reaches (at least) min_buildings,
        # the donor shrank but stays well over its own floor. The exact split can
        # shift by a cell or two beyond the minimum-satisfying steal itself — the
        # always-on dominated-cell pass may sweep one more boundary cell along for
        # local consistency, which is fine as long as both invariants hold.
        assert sum(totals.values()) == 330
        small, big = sorted(totals.values())
        assert small >= 100
        assert big >= 100
        # Still only 2 groups — nothing got fragmented.
        assert len(totals) == 2

    def test_no_eligible_donor_within_reach_stays_standalone(self):
        # A tiny, distant leftover with no neighbour anywhere near max_reach_m —
        # it should just stay small rather than being force-merged from afar.
        cells = _grid(5, 5, building_count=10, ward="Dabi")
        far = [_cell(f"Y{c}", 0.001 * c, 5.0, building_count=10, ward="FarWard") for c in range(3)]  # ~550km away
        all_cells = cells + far
        group_work_areas(
            all_cells,
            GroupingConfig(
                strategy="bfs_adjacency",
                target_buildings=250,
                buffer_distance_m=100,
                min_buildings=100,
                max_buildings=300,
                max_reach_m=150,
            ),
        )
        totals = {}
        for w in all_cells:
            totals[w["work_area_group"]] = totals.get(w["work_area_group"], 0) + w["building_count"]
        assert sorted(totals.values()) == [30, 250]  # far group untouched, still below min

    def test_donor_never_drops_below_its_own_min_buildings(self):
        # Donor line of 15 cells (150) can give at most down to min_buildings=100
        # (i.e. 5 cells' worth) before it must stop, even if the recipient still
        # needs more than that.
        donor = [_cell(f"D{i}", 0.001 * i, 0.0, building_count=10, ward="Dabi") for i in range(15)]
        recipient = [_cell(f"R{i}", 0.001 * (14 + i), -0.001, building_count=10, ward="Dabi") for i in range(3)]
        all_cells = donor + recipient
        group_work_areas(
            all_cells,
            GroupingConfig(
                strategy="bfs_adjacency",
                target_buildings=150,
                buffer_distance_m=100,
                min_buildings=100,
                max_buildings=170,
                max_reach_m=150,
            ),
        )
        totals = {}
        for w in all_cells:
            totals[w["work_area_group"]] = totals.get(w["work_area_group"], 0) + w["building_count"]
        # Donor floor-protected at exactly 100; recipient gets what it can (80),
        # still short of min_buildings but that's expected — no other donor exists.
        assert sorted(totals.values()) == [80, 100]


class TestTopUpContiguityGuard:
    """Direct unit tests of _top_up_undersized_clusters: a cell is only stolen if
    the donor's remaining cells stay one connected piece (never split a group in
    two to satisfy a neighbour's minimum)."""

    @staticmethod
    def _line_donor_plus_recipient(recipient_touches: str):
        from shapely.geometry import Point
        from shapely.strtree import STRtree

        donor_ids = [f"D{i}" for i in range(5)]
        by_id = {wid: {"building_count": 60} for wid in donor_ids}
        by_id["R0"] = {"building_count": 10}
        clusters = [donor_ids, ["R0"]]
        cents = {f"D{i}": (i * 10.0, 0.0) for i in range(5)}
        cents["R0"] = (cents[recipient_touches][0], -10.0)
        geoms = {wid: Point(xy).buffer(4.0) for wid, xy in cents.items()}
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        adjacency = {wid: set() for wid in wa_ids}
        for a, b in [("D0", "D1"), ("D1", "D2"), ("D2", "D3"), ("D3", "D4")]:
            adjacency[a].add(b)
            adjacency[b].add(a)
        adjacency["R0"].add(recipient_touches)
        adjacency[recipient_touches].add("R0")
        adjacency = {k: list(v) for k, v in adjacency.items()}
        return clusters, by_id, geoms, cents, adjacency, tree, wa_ids

    def test_middle_cell_would_split_donor_so_it_is_refused(self):
        from connect_labs.microplans.core.grouping import _top_up_undersized_clusters

        clusters, by_id, geoms, cents, adjacency, tree, wa_ids = self._line_donor_plus_recipient("D2")
        result = _top_up_undersized_clusters(
            clusters, by_id, geoms, cents, adjacency, tree, wa_ids, 100, 300, 50.0, None
        )
        r0_group = next(cells for _seed, cells in result if "R0" in cells)
        # Refused the only candidate (would split the donor line in two) — R0
        # stays alone rather than disconnecting the donor.
        assert r0_group == ["R0"]

    def test_end_cell_is_safe_to_steal(self):
        from connect_labs.microplans.core.grouping import _top_up_undersized_clusters

        clusters, by_id, geoms, cents, adjacency, tree, wa_ids = self._line_donor_plus_recipient("D0")
        result = _top_up_undersized_clusters(
            clusters, by_id, geoms, cents, adjacency, tree, wa_ids, 100, 300, 50.0, None
        )
        r0_group = next(cells for _seed, cells in result if "R0" in cells)
        # D0 (a line endpoint) is safe to remove — the donor stays one piece — so
        # R0 successfully grows by taking it (and D1, still reaching for min).
        assert "D0" in r0_group
        assert len(r0_group) > 1


class TestTopUpDoesNotBridgeAcrossAnotherGroup:
    """A merge/steal must never reach across a THIRD cluster's already-claimed
    cells — that would leave that group's cells sitting in the gap between two
    pieces of the merged group (the "another WAG runs in between" bug)."""

    @staticmethod
    def _in_a_row(a_buildings, b_buildings, c_buildings):
        from shapely.geometry import Point
        from shapely.strtree import STRtree

        clusters = [["A"], ["B"], ["C"]]
        by_id = {
            "A": {"building_count": a_buildings},
            "B": {"building_count": b_buildings},
            "C": {"building_count": c_buildings},
        }
        cents = {"A": (0.0, 0.0), "B": (10.0, 0.0), "C": (20.0, 0.0)}
        geoms = {wid: Point(xy).buffer(4.0) for wid, xy in cents.items()}
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        adjacency = {wid: [] for wid in wa_ids}  # none touching — three separate clusters
        return clusters, by_id, geoms, cents, adjacency, tree, wa_ids

    def test_blocked_when_third_cluster_sits_in_the_gap(self):
        from connect_labs.microplans.core.grouping import _top_up_undersized_clusters

        # A (10, undersized) is too small to merge with B (1000 — combined would
        # breach max_buildings) and the only other candidate, C (100, would fit),
        # sits on the far side of B — the straight line to it crosses B's cell.
        clusters, by_id, geoms, cents, adjacency, tree, wa_ids = self._in_a_row(10, 1000, 100)
        result = _top_up_undersized_clusters(
            clusters, by_id, geoms, cents, adjacency, tree, wa_ids, 50, 200, 25.0, None
        )
        a_group = next(cells for _seed, cells in result if "A" in cells)
        # Neither path works (B too big, C blocked by B) — A stays standalone
        # rather than bridging across B's territory.
        assert a_group == ["A"]

    def test_allowed_across_genuinely_empty_gap(self):
        from connect_labs.microplans.core.grouping import _top_up_undersized_clusters

        # Same distances, but no third cluster in the way at all — nothing but
        # empty terrain between A and C, so the merge proceeds normally.
        clusters = [["A"], ["C"]]
        by_id = {"A": {"building_count": 10}, "C": {"building_count": 100}}
        from shapely.geometry import Point
        from shapely.strtree import STRtree

        cents = {"A": (0.0, 0.0), "C": (20.0, 0.0)}
        geoms = {wid: Point(xy).buffer(4.0) for wid, xy in cents.items()}
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        adjacency = {wid: [] for wid in wa_ids}
        result = _top_up_undersized_clusters(
            clusters, by_id, geoms, cents, adjacency, tree, wa_ids, 50, 200, 25.0, None
        )
        a_group = next(cells for _seed, cells in result if "A" in cells)
        assert set(a_group) == {"A", "C"}


class TestAbsorbEnclosedClusters:
    """A cluster that geometrically FILLS A HOLE in another cluster's territory
    (boxed in on every side, not just "touches only one neighbour") must always
    be absorbed into it — independent of min_buildings/max_buildings, since
    there is no other valid destination. Being adjacent to only one neighbour
    is NOT sufficient on its own (an ordinary small WAG bordering a bigger one
    on just one edge, with open land on its other sides, must NOT be
    force-merged) — real grid geometry (unit squares) is used throughout so the
    hole-filling check is exercised for real, not just the graph prefilter."""

    @staticmethod
    def _grid_adjacency(cells: dict[str, tuple[int, int]]) -> dict[str, list[str]]:
        """4-connected adjacency for a {id: (col, row)} grid-position map."""
        pos_to_id = {xy: wid for wid, xy in cells.items()}
        adjacency: dict[str, list[str]] = {}
        for wid, (c, r) in cells.items():
            adjacency[wid] = [
                pos_to_id[nb] for nb in ((c - 1, r), (c + 1, r), (c, r - 1), (c, r + 1)) if nb in pos_to_id
            ]
        return adjacency

    def _ring_with_center(self, center_buildings=5, ring_buildings=10):
        from shapely.geometry import box

        # 3x3 grid: 8 "ring" cells (one cluster) fully surrounding a single
        # "center" cell (a separate, 1-cell cluster) — a real donut shape, so
        # the ring's union genuinely has an interior hole exactly where CTR is.
        positions = {}
        ring = []
        for r in range(3):
            for c in range(3):
                wid = "CTR" if (c, r) == (1, 1) else f"G{r}{c}"
                positions[wid] = (c, r)
                if wid != "CTR":
                    ring.append(wid)
        geoms = {wid: box(c, r, c + 1, r + 1) for wid, (c, r) in positions.items()}
        adjacency = self._grid_adjacency(positions)
        by_id = {w: {"building_count": ring_buildings} for w in ring}
        by_id["CTR"] = {"building_count": center_buildings}
        clusters = [ring, ["CTR"]]
        return clusters, adjacency, by_id, geoms, ring

    @staticmethod
    def _with_seeds(clusters: list[list[str]]) -> list[tuple[str, list[str]]]:
        return [(c[0], c) for c in clusters]

    def test_enclosed_cell_absorbed_into_surrounding_cluster(self):
        clusters, adjacency, by_id, geoms, ring = self._ring_with_center()
        result = _absorb_enclosed_clusters(self._with_seeds(clusters), adjacency, geoms)
        assert len(result) == 1
        seed, cells = result[0]
        assert set(cells) == set(ring) | {"CTR"}
        # The bigger, surrounding cluster's own cell stays the seed.
        assert seed == ring[0]

    def test_direction_is_geometric_not_size_based(self):
        # Even if the enclosed cell is given a much bigger building count than
        # the ring around it, it's still the one absorbed — a solid single cell
        # can never have an interior hole, so it can never be the "outer" side
        # regardless of size; only the ring (which has the actual hole) can be.
        clusters, adjacency, by_id, geoms, ring = self._ring_with_center(center_buildings=1000)
        result = _absorb_enclosed_clusters(self._with_seeds(clusters), adjacency, geoms)
        assert len(result) == 1
        assert result[0][0] == ring[0]

    def test_not_enclosed_when_only_bordering_one_edge(self):
        # A-B-C in a row: B only touches A and... no wait, B touches BOTH A and
        # C (two different clusters) — not an enclave. Also covers the simpler
        # "leftover row next to a village" shape: a single cell bordering just
        # one neighbour on ONE side, with open geometry on the others, must
        # never be merged just because "it only touches one other cluster."
        from shapely.geometry import box

        positions = {"A": (0, 0), "B": (1, 0), "C": (2, 0)}
        geoms = {wid: box(c, r, c + 1, r + 1) for wid, (c, r) in positions.items()}
        adjacency = self._grid_adjacency(positions)
        clusters = [["A"], ["B"], ["C"]]
        result = _absorb_enclosed_clusters(self._with_seeds(clusters), adjacency, geoms)
        assert sorted((cells for _seed, cells in result), key=len) == sorted(clusters, key=len)

    def test_leftover_row_next_to_a_village_is_not_an_enclave(self):
        # The exact shape that regressed earlier: a village block plus a
        # leftover row sitting along ONE of its edges. The row touches only the
        # village (one cluster), but doesn't fill a hole in it — must stay separate.
        from shapely.geometry import box

        positions = {}
        village = []
        for r in range(5):
            for c in range(5):
                wid = f"V{r}{c}"
                positions[wid] = (c, r)
                village.append(wid)
        leftover = []
        for c in range(3):
            wid = f"X{c}"
            positions[wid] = (c, -1)  # directly below the village's bottom row
            leftover.append(wid)
        geoms = {wid: box(c, r, c + 1, r + 1) for wid, (c, r) in positions.items()}
        adjacency = self._grid_adjacency(positions)
        clusters = [village, leftover]
        result = _absorb_enclosed_clusters(self._with_seeds(clusters), adjacency, geoms)
        assert sorted((cells for _seed, cells in result), key=len) == sorted(clusters, key=len)

    def test_nested_enclaves_resolve_in_one_pass(self):
        # 5x5 grid: OUTER (the 16-cell outer ring), MID (the 8-cell inner ring),
        # CTR (the single center cell) — CTR is enclosed by MID, and once
        # resolved MID+CTR together are enclosed by OUTER. Both must resolve.
        from shapely.geometry import box

        positions = {}
        outer, mid = [], []
        for r in range(5):
            for c in range(5):
                if (c, r) == (2, 2):
                    wid = "CTR"
                elif 1 <= c <= 3 and 1 <= r <= 3:
                    wid = f"M{r}{c}"
                    mid.append(wid)
                else:
                    wid = f"O{r}{c}"
                    outer.append(wid)
                positions[wid] = (c, r)
        geoms = {wid: box(c, r, c + 1, r + 1) for wid, (c, r) in positions.items()}
        adjacency = self._grid_adjacency(positions)
        clusters = [outer, mid, ["CTR"]]
        result = _absorb_enclosed_clusters(self._with_seeds(clusters), adjacency, geoms)
        assert len(result) == 1
        assert set(result[0][1]) == set(outer) | set(mid) | {"CTR"}


class TestReassignDominatedCells:
    """A work area whose combined STRICTLY orthogonal (real shared-edge, not
    just within the adjacency buffer) neighbours from OTHER WAGs outnumber
    its neighbours from its OWN WAG is locally dominated — even when the
    foreign side is split across several different WAGs rather than one —
    and moves to whichever foreign WAG it borders most, subject to the
    receiving group's max_buildings ceiling, barriers, and requiring that
    the move actually resolve the imbalance (so a cell sitting at a
    symmetric multi-WAG meeting point, where no single neighbour can absorb
    it outright, is left alone rather than flip-flopping)."""

    @staticmethod
    def _ring_dominated():
        # X has one real same-WAG neighbour (W, which has its own companion
        # W2 so W isn't a lone singleton) and a self-connected 5-cell "ring"
        # of a different WAG touching its other three sides — 1 own vs 3
        # foreign, a clean majority.
        from shapely.geometry import box

        return {
            "X": box(1, 1, 2, 2),
            "W": box(0, 1, 1, 2),
            "W2": box(-1, 1, 0, 2),
            "R10": box(1, 0, 2, 1),  # south of X
            "R21": box(2, 1, 3, 2),  # east of X
            "R12": box(1, 2, 2, 3),  # north of X
            "R20": box(2, 0, 3, 1),  # bridges R10-R21
            "R22": box(2, 2, 3, 3),  # bridges R21-R12
        }

    def test_dominated_cell_moves_to_the_majority_neighbour(self):
        from shapely.strtree import STRtree

        geoms = self._ring_dominated()
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        ring = ["R10", "R21", "R12", "R20", "R22"]
        clusters_with_seed = [("W", ["X", "W", "W2"]), ("R10", ring)]
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 1000, None)
        x_group = next(cells for _seed, cells in result if "X" in cells)
        assert set(x_group) == {"X", *ring}
        w_group = next(cells for _seed, cells in result if "W" in cells)
        assert set(w_group) == {"W", "W2"}  # left behind, correctly untouched

    def test_multi_wag_split_still_dominates(self):
        # The user's actual screenshots: X's foreign neighbours are split
        # across THREE different single-cell WAGs (1 each), none individually
        # reaching the old "3+ from one WAG" bar, but 3 combined > 1 own.
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "X": box(1, 1, 2, 2),
            "W": box(0, 1, 1, 2),
            "W2": box(-1, 1, 0, 2),
            "S": box(1, 0, 2, 1),
            "E": box(2, 1, 3, 2),
            "N": box(1, 2, 2, 3),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        clusters_with_seed = [("W", ["X", "W", "W2"]), ("S", ["S"]), ("E", ["E"]), ("N", ["N"])]
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 1000, None)
        # Each foreign singleton is itself locally dominated by X (0 own vs 1
        # foreign) so they all fold together into one group with X.
        assert len(result) == 1
        assert set(result[0][1]) == {"X", "W", "W2", "S", "E", "N"}

    def test_true_symmetric_meeting_point_is_stable(self):
        # A genuine 4-way meeting point: X has 1 own neighbour (W) and 3
        # foreign neighbours (N, E, S), each from a DIFFERENT WAG and each
        # with its own companion cell (so none is a lone singleton that would
        # get pulled in on its own). No single foreign WAG can absorb X
        # without leaving it still outnumbered by the other two, so the move
        # is blocked and nothing changes — this is the oscillation-prevention
        # safeguard the user's 4-WAG screenshot (yellow/red/blue/purple) needs.
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "X": box(1, 1, 2, 2),
            "W": box(0, 1, 1, 2),
            "N": box(1, 2, 2, 3),
            "N2": box(1, 3, 2, 4),
            "E": box(2, 1, 3, 2),
            "E2": box(3, 1, 4, 2),
            "S": box(1, 0, 2, 1),
            "S2": box(1, -1, 2, 0),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        clusters_with_seed = [
            ("W", ["X", "W"]),
            ("N", ["N", "N2"]),
            ("E", ["E", "E2"]),
            ("S", ["S", "S2"]),
        ]
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 1000, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {
            frozenset(["X", "W"]),
            frozenset(["N", "N2"]),
            frozenset(["E", "E2"]),
            frozenset(["S", "S2"]),
        }

    def test_tied_neighbours_stays_put(self):
        # X: own = W + N (2, only linked via X). foreign = S + E, bridged
        # into one connected WAG via SE (2). A tie either way, so nothing
        # moves — same intent as the old "two of four" test, now expressed
        # through the total-count rule instead of a single-WAG count.
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "X": box(1, 1, 2, 2),
            "W": box(0, 1, 1, 2),
            "N": box(1, 2, 2, 3),
            "S": box(1, 0, 2, 1),
            "E": box(2, 1, 3, 2),
            "SE": box(2, 0, 3, 1),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        clusters_with_seed = [("W", ["X", "W", "N"]), ("S", ["S", "E", "SE"])]
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 1000, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {frozenset(["X", "W", "N"]), frozenset(["S", "E", "SE"])}

    def test_moves_when_result_is_a_tie_not_just_a_majority(self):
        # Real screenshot from live testing: X has 1 own neighbour (G, which
        # has its own companion G2 so G isn't a lone singleton) and 3 foreign
        # neighbours split 2-and-1 across two different WAGs (P: north+west,
        # bridged into one WAG via P_NW; B: east, with its own companion so
        # it isn't a lone singleton either). Moving to P (the 2-count
        # majority) would leave X at a 2-own/2-foreign TIE, not an outright
        # majority — this must still be treated as a real improvement over
        # the original 1-own/3-foreign split and allowed through, since a tie
        # is itself a stable rest state (ties never re-trigger a move).
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "X": box(1, 1, 2, 2),
            "G": box(1, 0, 2, 1),
            "G2": box(1, -1, 2, 0),
            "P_N": box(1, 2, 2, 3),
            "P_W": box(0, 1, 1, 2),
            "P_NW": box(0, 2, 1, 3),
            "B_E": box(2, 1, 3, 2),
            "B_E2": box(3, 1, 4, 2),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        clusters_with_seed = [
            ("G", ["X", "G", "G2"]),
            ("P_N", ["P_N", "P_W", "P_NW"]),
            ("B_E", ["B_E", "B_E2"]),
        ]
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 1000, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {
            frozenset(["G", "G2"]),
            frozenset(["P_N", "P_NW", "P_W", "X"]),
            frozenset(["B_E", "B_E2"]),
        }

    def test_blocked_by_max_buildings_ceiling(self):
        from shapely.strtree import STRtree

        geoms = self._ring_dominated()
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        ring = ["R10", "R21", "R12", "R20", "R22"]
        by_id.update({w: {"building_count": 500} for w in ring})
        clusters_with_seed = [("W", ["X", "W", "W2"]), ("R10", ring)]
        # Receiving group already totals 2500; +X's 10 = 2510 > max=2505.
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 2505, None)
        x_group = next(cells for _seed, cells in result if "X" in cells)
        assert x_group == ["X", "W", "W2"]  # unchanged — the ceiling blocked the move

    def test_barrier_blocks_reassignment(self):
        from shapely.geometry import LineString
        from shapely.prepared import prep
        from shapely.strtree import STRtree

        geoms = self._ring_dominated()
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        by_id = {w: {"building_count": 10} for w in wa_ids}
        ring = ["R10", "R21", "R12", "R20", "R22"]
        clusters_with_seed = [("W", ["X", "W", "W2"]), ("R10", ring)]
        # A barrier running right through X's row blocks all links to the ring.
        barrier = prep(LineString([(0, 1.5), (5, 1.5)]))
        result = _reassign_dominated_cells(clusters_with_seed, by_id, geoms, wa_ids, tree, 1000, barrier)
        x_group = next(cells for _seed, cells in result if "X" in cells)
        assert x_group == ["X", "W", "W2"]


class TestReassignIsolatedPiecesTouchingOtherWags:
    """A disconnected piece within a WAG (cells only attached to EACH OTHER,
    cut off from the WAG's main body) is fine to keep its label if it only
    touches open terrain — but if it ALSO touches a different WAG (edge or
    corner), the whole piece must move there."""

    def test_disconnected_pair_touching_foreign_wag_moves_together(self):
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "O1": box(0, 0, 1, 1),
            "O2": box(1, 0, 2, 1),
            "O3": box(2, 0, 3, 1),
            "O4": box(0, 1, 1, 2),
            "O5": box(1, 1, 2, 2),
            "OX1": box(10, 10, 11, 11),
            "OX2": box(11, 10, 12, 11),
            "RED1": box(11, 11, 12, 12),  # corner-touches OX2 only
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        clusters_with_seed = [
            ("O1", ["O1", "O2", "O3", "O4", "O5", "OX1", "OX2"]),
            ("RED1", ["RED1"]),
        ]
        result = _reassign_isolated_pieces_touching_other_wags(clusters_with_seed, geoms, wa_ids, tree, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {frozenset(["O1", "O2", "O3", "O4", "O5"]), frozenset(["OX1", "OX2", "RED1"])}

    def test_disconnected_piece_touching_only_open_terrain_stays_put(self):
        # Same layout, no red cell at all — the stray pair touches nothing but
        # open terrain, so it stays under the main WAG's label (allowed to be
        # geographically disconnected — the whole point of the top-up pass
        # reaching across a real gap in sparse areas).
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "O1": box(0, 0, 1, 1),
            "O2": box(1, 0, 2, 1),
            "O3": box(2, 0, 3, 1),
            "O4": box(0, 1, 1, 2),
            "O5": box(1, 1, 2, 2),
            "OX1": box(10, 10, 11, 11),
            "OX2": box(11, 10, 12, 11),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        clusters_with_seed = [("O1", ["O1", "O2", "O3", "O4", "O5", "OX1", "OX2"])]
        result = _reassign_isolated_pieces_touching_other_wags(clusters_with_seed, geoms, wa_ids, tree, None)
        assert len(result) == 1
        assert set(result[0][1]) == {"O1", "O2", "O3", "O4", "O5", "OX1", "OX2"}

    def test_diagonal_only_touch_still_counts(self):
        # The stray piece touches the foreign WAG only at a CORNER (no shared
        # edge) — this pass counts that (unlike _reassign_dominated_cells,
        # which is strictly orthogonal).
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "O1": box(0, 0, 1, 1),
            "OX": box(10, 10, 11, 11),
            "RED": box(11, 11, 12, 12),  # touches OX only at the (11, 11) corner
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        clusters_with_seed = [("O1", ["O1", "OX"]), ("RED", ["RED"])]
        result = _reassign_isolated_pieces_touching_other_wags(clusters_with_seed, geoms, wa_ids, tree, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {frozenset(["O1"]), frozenset(["OX", "RED"])}

    def test_ordinary_single_piece_wag_is_never_touched(self):
        # A normal, single connected WAG bordering a different WAG along an
        # ordinary shared edge — this must NEVER trigger anything (it has
        # only one piece, so there's nothing "disconnected" to reassign).
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "O1": box(0, 0, 1, 1),
            "O2": box(1, 0, 2, 1),
            "RED": box(2, 0, 3, 1),  # touches O2 along a normal shared edge
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        clusters_with_seed = [("O1", ["O1", "O2"]), ("RED", ["RED"])]
        result = _reassign_isolated_pieces_touching_other_wags(clusters_with_seed, geoms, wa_ids, tree, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {frozenset(["O1", "O2"]), frozenset(["RED"])}

    def test_overrides_max_buildings_unlike_dominated_cells(self):
        # A stray piece must move even when the receiving WAG is already huge
        # — unlike _reassign_dominated_cells, there's no ceiling here.
        from shapely.geometry import box
        from shapely.strtree import STRtree

        geoms = {
            "O1": box(0, 0, 1, 1),
            "O2": box(1, 0, 2, 1),
            "OX": box(10, 10, 11, 11),
            "RED": box(11, 10, 12, 11),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        clusters_with_seed = [("O1", ["O1", "O2", "OX"]), ("RED", ["RED"])]
        result = _reassign_isolated_pieces_touching_other_wags(clusters_with_seed, geoms, wa_ids, tree, None)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {frozenset(["O1", "O2"]), frozenset(["OX", "RED"])}

    def test_barrier_blocks_reassignment(self):
        from shapely.geometry import LineString, box
        from shapely.prepared import prep
        from shapely.strtree import STRtree

        geoms = {
            "O1": box(0, 0, 1, 1),
            "OX": box(10, 10, 11, 11),
            "RED": box(11, 10, 12, 11),
        }
        wa_ids = list(geoms.keys())
        tree = STRtree([geoms[w] for w in wa_ids])
        clusters_with_seed = [("O1", ["O1", "OX"]), ("RED", ["RED"])]
        barrier = prep(LineString([(10.5, -5), (10.5, 20)]))  # runs right between OX and RED
        result = _reassign_isolated_pieces_touching_other_wags(clusters_with_seed, geoms, wa_ids, tree, barrier)
        groups = {frozenset(cells) for _seed, cells in result}
        assert groups == {frozenset(["O1", "OX"]), frozenset(["RED"])}
