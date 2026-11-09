"""Buildings reached (``microplans/coverage/reached.py``): a pure method, tested on generated places."""

import numpy as np
import pandas as pd
import pytest

from connect_labs.microplans.coverage import reached

LON0, LAT0 = 8.0, 13.5  # a place in Niger; a degree of latitude is about 111 km


def _village(n=100, spacing_m=40, area=60.0):
    """A grid of n buildings spaced ``spacing_m`` apart, each ``area`` m2."""
    side = int(np.ceil(np.sqrt(n)))
    dlat = spacing_m / 111_000
    dlon = spacing_m / (111_000 * np.cos(np.radians(LAT0)))
    rows = [(LON0 + i * dlon, LAT0 + j * dlat) for j in range(side) for i in range(side)][:n]
    return pd.DataFrame({"lon": [r[0] for r in rows], "lat": [r[1] for r in rows], "area_m2": area})


def _points_at(buildings, idx, cycle=None, jitter_m=5):
    sub = buildings.iloc[idx]
    d = jitter_m / 111_000
    out = pd.DataFrame({"lon": sub["lon"].to_numpy() + d, "lat": sub["lat"].to_numpy() + d})
    if cycle is not None:
        out["cycle"] = cycle
    return out


def test_a_point_near_a_building_reaches_it_and_a_far_one_does_not():
    b = _village(25)
    got = reached.reached_mask(b, _points_at(b, [0, 7]), max_dist_m=30)
    assert got.sum() == 2 and got[0] and got[7]
    far = pd.DataFrame({"lon": [LON0 + 0.5], "lat": [LAT0 + 0.5]})
    assert not reached.reached_mask(b, far).any()


def test_the_distance_is_a_parameter():
    b = _village(25, spacing_m=100)
    # 40 m due north of building 0, and 60 m south of the next building up.
    p = pd.DataFrame({"lon": [b["lon"].iloc[0]], "lat": [b["lat"].iloc[0] + 40 / 111_000]})
    assert not reached.reached_mask(b, p, max_dist_m=30).any()
    assert reached.reached_mask(b, p, max_dist_m=50).sum() == 1


def test_per_cycle_cumulative_and_ever():
    b = _village(100)
    pts = pd.concat([_points_at(b, range(0, 40), 1), _points_at(b, range(20, 70), 2)], ignore_index=True)
    res = reached.reached_by_cycle(b, pts)
    assert res.buildings == 100
    assert res.by_cycle == {1: 40, 2: 50}
    assert res.cumulative == {1: 40, 2: 70}
    assert res.reached_ever == 70 and res.share_ever == pytest.approx(0.7)
    assert res.share(2) == pytest.approx(0.5)


def test_a_building_is_counted_once_however_many_points_are_near_it():
    b = _village(25)
    pts = pd.concat([_points_at(b, [3], 1)] * 5, ignore_index=True)
    assert reached.reached_by_cycle(b, pts).by_cycle == {1: 1}


def test_no_points_or_no_buildings_is_zero_not_an_error():
    b = _village(10)
    assert reached.reached_by_cycle(b, pd.DataFrame({"lon": [], "lat": [], "cycle": []})).reached_ever == 0
    assert reached.reached_by_cycle(b.iloc[0:0], _points_at(b, [0], 1)).share_ever is None


def test_bad_points_are_counted_by_kind_and_never_silently_dropped():
    pts = pd.DataFrame(
        {
            "lon": [LON0, None, 0.0, 50.0, LON0 + 0.001, LON0 + 0.001, LON0 + 0.002],
            "lat": [LAT0, LAT0, 0.0, 10.0, LAT0, LAT0, LAT0],
            "accuracy_m": [5, 5, 5, 5, 200, 5, 5],
            "cycle": [1, 1, 1, 1, 1, 1, 1],
        }
    )
    kept, q = reached.valid_points(pts, bounds=(LON0 - 1, LAT0 - 1, LON0 + 1, LAT0 + 1))
    assert (q.total, q.missing, q.null_island, q.out_of_bounds, q.poor_accuracy, q.duplicate) == (7, 1, 1, 1, 1, 0)
    assert q.valid == len(kept) == 3
    assert q.valid_share == pytest.approx(3 / 7)
    assert q.total == q.valid + q.missing + q.null_island + q.out_of_bounds + q.poor_accuracy + q.duplicate


def test_the_same_household_in_the_same_cycle_is_one_point_but_not_across_cycles():
    pts = pd.DataFrame({"lon": [LON0] * 3, "lat": [LAT0] * 3, "cycle": [1, 1, 2]})
    kept, q = reached.valid_points(pts)
    assert q.duplicate == 1 and len(kept) == 2


def test_cleaning_uses_the_microplanners_own_filters():
    b = _village(10)
    b.loc[0, "area_m2"] = 5000.0  # a market, not a home
    from connect_labs.microplans.core.filters import FilterConfig

    assert len(reached.clean_buildings(b)) == 9
    assert len(reached.clean_buildings(b, FilterConfig(area_max_m2=10_000))) == 10
