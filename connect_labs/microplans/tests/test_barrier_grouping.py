"""Barrier-aware WAG grouping (pure geometry — no DB / no Overture)."""

from shapely.geometry import LineString, box, mapping

from connect_labs.microplans.core import grouping as G


def _cell(i, buildings=50, y=0.0):
    x0 = 0.001 * i
    g = box(x0, y, x0 + 0.0009, y + 0.0009)
    return {
        "id": f"c{i}",
        "geometry": mapping(g),
        "centroid": [x0 + 0.00045, y + 0.00045],
        "building_count": buildings,
        "work_area_group": "",
    }


def _groups(work_areas):
    out = {}
    for w in work_areas:
        out.setdefault(w["work_area_group"], []).append(w["id"])
    return out


def test_no_group_spans_a_barrier():
    was = [_cell(i) for i in range(8)]
    river = LineString([(0.00385, -1), (0.00385, 1)])  # between c3 and c4
    cfg = G.GroupingConfig(strategy="barrier_aware", max_buildings=1000, buffer_distance_m=200)
    G.group_work_areas(was, cfg, barriers=river)
    groups = _groups(was)
    left, right = {"c0", "c1", "c2", "c3"}, {"c4", "c5", "c6", "c7"}
    spanning = [g for g, ids in groups.items() if set(ids) & left and set(ids) & right]
    assert spanning == [], f"a group spanned the river: {groups}"


def test_barrier_forces_split_even_under_cap():
    # 4 cells, 200 total, target 200 → cap alone would make ONE group; a river must
    # still split them into two.
    was = [_cell(i) for i in range(4)]
    river = LineString([(0.00185, -1), (0.00185, 1)])  # between c1 and c2
    cfg = G.GroupingConfig(strategy="barrier_aware", max_buildings=200, buffer_distance_m=200)
    G.group_work_areas(was, cfg, barriers=river)
    assert len({w["work_area_group"] for w in was}) == 2


def test_falls_back_to_plain_without_barriers():
    was = [_cell(i) for i in range(4)]
    cfg = G.GroupingConfig(strategy="barrier_aware", max_buildings=1000, buffer_distance_m=200)
    G.group_work_areas(was, cfg, barriers=None)
    # No barrier + generous cap → a single walkable cluster.
    assert len({w["work_area_group"] for w in was}) == 1


def test_target_splits_large_region_near_target():
    # 12 contiguous cells x 50 = 600 buildings, target 200 → ~3 groups, none tiny.
    was = [_cell(i) for i in range(12)]
    cfg = G.GroupingConfig(strategy="barrier_aware", max_buildings=200, buffer_distance_m=200)
    G.group_work_areas(was, cfg, barriers=None)  # fallback path also respects the cap
    sizes = sorted(len(ids) for ids in _groups(was).values())
    assert sum(sizes) == 12 and min(sizes) >= 2  # no 1-cell dribble group
