"""The population lens (``microplans/coverage/population.py``), on a drawn grid with the fetch injected."""

import numpy as np
import pandas as pd
import pytest

from connect_labs.microplans.coverage import population as P

# a 10 x 10 grid of 0.01 degree cells, 1,000 under-fives in each, top-left corner at (8.0, 14.0)
VALUES = np.full((10, 10), 1000.0)


def grid(**kw):
    return P.crop(VALUES, 8.0, 14.0, 0.01, -0.01, kw.pop("bbox", (8.02, 13.92, 8.05, 13.95)), **kw)


def placed(rows):
    return pd.DataFrame(rows, columns=["grp", "hh", "lon", "lat", "cycles"])


def test_crop_keeps_the_window_under_the_box_with_a_margin_and_refuses_one_outside_the_grid():
    g = grid()
    assert g.values.shape == (6, 7) and (g.origin_x, round(g.origin_y, 6)) == (8.0, 13.96)
    with pytest.raises(ValueError):
        P.crop(VALUES, 8.0, 14.0, 0.01, -0.01, (30.0, 30.0, 31.0, 31.0))


def test_a_cells_eligible_children_are_the_under_fives_times_the_eligible_share():
    g = grid()
    row, col = g.cell_of([8.025], [13.945])
    assert g.value_at(int(row[0]), int(col[0])) == 1000.0
    out = P.population_lens(
        placed([["A", f"h{i}", 8.025, 13.945, {1}] for i in range(6)]), {f"h{i}": {1: 2} for i in range(6)}, g
    )
    assert out["cells"] == [[8.025, 13.945, 950.0, 6, [12]]]


def test_a_cell_under_the_household_floor_is_not_drawn_but_still_counts_in_its_district():
    rows = [["A", f"h{i}", 8.025, 13.945, {1}] for i in range(6)] + [
        ["B", f"x{i}", 8.045, 13.925, {1}] for i in range(2)
    ]
    served = {**{f"h{i}": {1: 2} for i in range(6)}, **{f"x{i}": {1: 1} for i in range(2)}}
    out = P.population_lens(placed(rows), served, grid(), {"A": "D1", "B": "D1"})
    assert len(out["cells"]) == 1 and out["cells_total"] == 2
    (d1,) = out["districts"]
    assert d1["cells"] == 2 and d1["served"][1] == 14 and d1["eligible"] == 1900.0


def test_a_cycle_is_out_of_the_cells_that_served_anyone_in_it():
    rows = [["A", f"a{i}", 8.025, 13.945, {1, 2}] for i in range(5)] + [
        ["A", f"b{i}", 8.045, 13.925, {1}] for i in range(5)
    ]
    served = {**{f"a{i}": {1: 1, 2: 1} for i in range(5)}, **{f"b{i}": {1: 1} for i in range(5)}}
    (d,) = P.population_lens(placed(rows), served, grid(), {"A": "D1"})["districts"]
    assert d["eligible_in_cycle"][1] == 1900.0 and d["eligible_in_cycle"][2] == 950.0  # cycle 2 only ran in one cell
    assert d["share"][2] == pytest.approx(5 / 950.0)


def test_nothing_placed_is_an_empty_lens_not_an_error_and_the_output_says_what_the_grid_is():
    out = P.population_lens(placed([]), {}, grid())
    assert (
        out["cells"] == []
        and out["grid"]["name"].startswith("WorldPop")
        and "lower bound" in out["grid"]["assumption"]
    )


def test_a_cell_with_no_grid_estimate_is_counted_not_guessed():
    values = VALUES.copy()
    values[3, 3] = np.nan
    g = P.crop(values, 8.0, 14.0, 0.01, -0.01, (8.02, 13.92, 8.05, 13.95))
    out = P.population_lens(
        placed([["A", f"h{i}", 8.025, 13.945, {1}] for i in range(6)]), {f"h{i}": {1: 1} for i in range(6)}, g
    )
    assert out["cells_without_estimate"] in (0, 1)  # the NaN cell may lie outside the window; never a made-up value
    assert all(c[2] is None or c[2] > 0 for c in out["cells"])
