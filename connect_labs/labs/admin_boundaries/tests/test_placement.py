"""Placement (``labs/admin_boundaries/placement.py``): the core is pure, so it is tested on drawn places."""

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from connect_labs.labs.admin_boundaries.placement import Unit, place_points, units_from_features

# Two regions side by side; the left one is split into two districts, the right into one that
# leaves a gap (so some points fall in a region but in no district).
R1 = Unit("t:r1", "Region 1", 1, box(0, 0, 2, 2))
R2 = Unit("t:r2", "Region 2", 1, box(2, 0, 4, 2))
D1 = Unit("t:d1", "District 1", 2, box(0, 0, 1, 2))
D2 = Unit("t:d2", "District 2", 2, box(1, 0, 2, 2))
D3 = Unit("t:d3", "District 3", 2, box(2, 0, 3, 1))  # covers only part of region 2
UNITS = [R1, R2, D1, D2, D3]


def _pts(coords, **extra):
    return pd.DataFrame({"lon": [c[0] for c in coords], "lat": [c[1] for c in coords], **extra})


def test_each_point_is_counted_once_per_level_in_the_unit_that_contains_it():
    p = place_points(_pts([(0.5, 1), (0.6, 1), (1.5, 1), (2.5, 0.5), (3.5, 1.5)]), UNITS)
    assert p.total == 5
    assert p.levels[1].weights == {"t:r1": 3, "t:r2": 2}
    assert p.levels[2].weights == {"t:d1": 2, "t:d2": 1, "t:d3": 1}
    assert p.levels[1].unplaced == 0
    assert p.levels[2].unplaced == 1  # the point at (3.5, 1.5) is in region 2 but in no district


def test_weights_count_people_or_visits_not_just_points():
    p = place_points(_pts([(0.5, 1), (1.5, 1)], reached=[30, 10]), UNITS, weight="reached")
    assert p.total == 40
    assert p.levels[2].weights == {"t:d1": 30, "t:d2": 10}
    assert p.levels[2].share("t:d1") == pytest.approx(0.75)


def test_a_point_without_coordinates_is_unplaced_everywhere_but_still_in_the_total():
    p = place_points(_pts([(0.5, 1), (np.nan, 1), (None, None)]), UNITS)
    assert p.total == 3
    assert p.levels[1].unplaced == 2 and p.levels[2].unplaced == 2
    assert p.levels[1].placed_share == pytest.approx(1 / 3)


def test_a_point_on_a_shared_border_goes_to_the_lowest_key_whatever_the_load_order():
    border = _pts([(1.0, 1.0)])  # exactly between D1 and D2
    a = place_points(border, UNITS)
    b = place_points(border, list(reversed(UNITS)))
    assert a.levels[2].weights == b.levels[2].weights == {"t:d1": 1}


def test_areas_are_the_units_holding_a_minimum_share_of_all_the_weight():
    pts = _pts([(0.5, 1)] * 80 + [(1.5, 1)] * 19 + [(2.5, 0.5)] + [(3.5, 1.5)])
    lvl = place_points(pts, UNITS).levels[2]
    assert [u.key for u in lvl.areas(min_share=0.05)] == ["t:d1", "t:d2"]
    assert [u.key for u in lvl.areas(min_share=0.005)] == ["t:d1", "t:d2", "t:d3"]
    assert [(u.key, w) for u, w in lvl.top(1)] == [("t:d1", 80)]
    assert place_points(pts.iloc[0:0], UNITS).levels[2].areas() == []


def test_the_finest_level_that_still_explains_the_work():
    mostly_covered = _pts([(0.5, 1)] * 19 + [(3.5, 1.5)])  # 95% of points fall in a district
    assert place_points(mostly_covered, UNITS).finest_level() == 2
    gappy = _pts([(0.5, 1)] * 5 + [(3.5, 1.5)] * 5)  # half fall in no district
    p = place_points(gappy, UNITS)
    assert p.levels[2].placed_share == pytest.approx(0.5)
    assert p.finest_level() == 1
    assert p.finest_level(min_placed_share=0.4) == 2
    assert place_points(_pts([(50, 50)]), UNITS).finest_level() is None


def test_units_come_from_resolver_features():
    class Feature:
        def __init__(self, bid, level, geometry):
            self.source, self.boundary_id, self.name, self.level = "overture", bid, f"n{bid}", level
            self.geometry, self.country, self.parent_name = geometry, "NER", "Zinder"

    feats = [
        Feature("a", 3, {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}),
        Feature("b", 3, {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}),  # bow-tie
    ]
    units = units_from_features(feats)
    assert [u.key for u in units] == ["overture:a", "overture:b"] and all(u.geometry.is_valid for u in units)
    assert units[0].country == "NER" and units[0].level == 3
