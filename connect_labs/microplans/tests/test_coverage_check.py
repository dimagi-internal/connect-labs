"""The buildings-reached check (``microplans/coverage/check.py``), on drawn villages, footprint reader injected."""

import numpy as np
import pandas as pd
import pytest

from connect_labs.microplans.coverage import check

LON0, LAT0 = 8.0, 13.5
M = 1 / 111_000  # a metre, in degrees of latitude


def _village(n=100, spacing=40):
    side = int(np.ceil(np.sqrt(n)))
    dlon = spacing * M / np.cos(np.radians(LAT0))
    rows = [(LON0 + i * dlon, LAT0 + j * spacing * M, 60.0) for j in range(side) for i in range(side)][:n]
    return pd.DataFrame(rows, columns=["lon", "lat", "area_m2"])


VILLAGE = _village()


def fetch(lon, lat, radius_m):
    """Every village building within radius_m (a flat-earth distance is plenty for a test)."""
    d = np.hypot((VILLAGE["lon"] - lon) * 111_000 * np.cos(np.radians(LAT0)), (VILLAGE["lat"] - lat) * 111_000)
    return VILLAGE[d <= radius_m].reset_index(drop=True)


def gps(i, dx=3):
    b = VILLAGE.iloc[i]
    return f"{b.lat + dx * M:.6f} {b.lon + dx * M:.6f} 0.0 6.0"


FACILITY = {"id": "C1", "lon": LON0 + 200 * M, "lat": LAT0 + 200 * M}


def spec(**kw):
    base = {"facilities": [FACILITY], "catchment_k": 60, "max_dist_m": 15}
    return check.CheckSpec.from_dict({**base, **kw})


def test_the_sql_reads_one_row_per_household_from_the_visits_relation():
    sql = check.points_sql(spec())
    assert sql.startswith("SELECT min(form_json #>> '{form,localisation,csi_id}') AS grp")
    assert "COALESCE(form_json #>> '{form,administration_dose,geolocalisation}'" in sql
    assert "string_agg(DISTINCT form_json #>> '{form,var,passage}', ',') AS cycles" in sql
    assert "FROM visits WHERE form_json #>> '{form,var_menage,case_id_menage}' IS NOT NULL GROUP BY 2" in sql


@pytest.mark.parametrize("bad", ["form.a; DROP TABLE x", "form.a b", "", "form.a'}'"])
def test_a_path_that_is_not_a_dotted_form_path_is_refused(bad):
    with pytest.raises(check.SpecError):
        spec(household_path=bad)


def test_settings_are_validated():
    with pytest.raises(check.SpecError, match="facilities"):
        check.CheckSpec.from_dict({"catchment_k": 5})
    with pytest.raises(check.SpecError, match="exactly one"):
        spec(catchment_radius_m=100)
    with pytest.raises(check.SpecError, match="unknown setting"):
        spec(max_dist=3)
    with pytest.raises(check.SpecError, match="footprint filter"):
        spec(footprint_filters={"nope": 1})
    assert spec(catchment_k=None, catchment_radius_m=300).catchment_radius_m == 300


def test_geopoint_text_and_cycle_values_are_read_as_commcare_writes_them():
    assert check.parse_gps("13.5 8.0 0.0 7.5") == (8.0, 13.5, 7.5)
    assert check.parse_gps("13.5 8.0") == (8.0, 13.5, None)
    assert check.parse_gps("") is None and check.parse_gps(None) is None and check.parse_gps("x y") is None
    assert (
        check.cycle_number("contact_3") == 3
        and check.cycle_number("cycle 12 b") == 12
        and check.cycle_number("") is None
    )


def test_a_catchment_is_the_nearest_k_buildings_to_the_facility_whatever_the_visits_did():
    blds, radius = check.catchment(spec(catchment_k=10), FACILITY, fetch)
    assert len(blds) == 10 and 0 < radius < 130
    near = check.catchment(spec(catchment_k=None, catchment_radius_m=100), FACILITY, fetch)[0]
    assert 0 < len(near) < 40


def test_buildings_reached_per_cycle_with_everything_counted():
    rows = [
        ("C1", "hh1", gps(55), "contact_1,contact_2"),  # located, visited both cycles
        ("C1", "hh2", gps(45), "contact_1"),
        ("C1", "hh3", None, "contact_1,contact_2"),  # no point at all
        ("C1", "hh4", "0.000000 0.000000 0.0 0.0", "contact_2"),  # null island
        ("C1", "hh5", "13.500000 40.000000 0.0 5.0", "contact_1"),  # far from the facility
        ("C1", "hh6", f"{LAT0 + 100 * M:.6f} {LON0 + 100 * M:.6f} 0.0 400.0", "contact_1"),  # poor accuracy
        ("ZZ", "hh7", gps(5), "contact_1"),  # a group with no facility
    ]
    out = check.run_check(rows, spec(), fetch)
    (area,) = out["areas"]
    assert area["group"] == "C1" and area["households"] == 6 and area["buildings"] == 60
    assert area["data_quality"] == {"no_point": 1, "null_island": 1, "off_area": 1, "poor_accuracy": 1, "usable": 2}
    c1, c2 = area["cycles"][1], area["cycles"][2]
    assert (c1["households_visited"], c1["households_unplaced"]) == (5, 3)
    assert (c2["households_visited"], c2["households_unplaced"]) == (3, 2)
    assert c1["buildings_reached"] >= 1 and c2["buildings_reached"] >= 1
    assert out["groups_without_a_facility"] == ["ZZ"]
    assert out["households_total"] == 7


def test_a_larger_distance_reaches_at_least_as_many_buildings():
    rows = [("C1", f"hh{i}", gps(33 + i * 3), "contact_1") for i in range(12)]
    shares = [
        check.run_check(rows, spec(max_dist_m=d), fetch)["areas"][0]["cycles"][1]["buildings_reached"]
        for d in (5, 15, 40)
    ]
    assert shares == sorted(shares) and shares[0] < shares[-1]


def test_the_total_row_adds_areas_and_recomputes_the_share():
    rows = [("C1", "hh1", gps(55), "contact_1"), ("C1", "hh2", gps(45), "contact_1")]
    out = check.run_check(rows, spec(), fetch)
    tot = check.total_row(out)
    assert tot["buildings"] == 60
    assert tot["cycles"][1]["buildings_reached"] == out["areas"][0]["cycles"][1]["buildings_reached"]
    assert tot["cycles"][1]["share"] == pytest.approx(tot["cycles"][1]["buildings_reached"] / 60)


def test_no_visits_gives_an_area_with_no_cycles_not_an_error():
    out = check.run_check([], spec(), fetch)
    assert out["areas"][0]["households"] == 0 and out["areas"][0]["cycles"] == {}


def test_shards_split_the_households_exactly_and_stay_under_the_row_cap():
    for households in (0, 1, 3999, 4000, 4001, 50_000, 10**7):
        ranges = check.shard_bounds(households)
        flat = [int(h, 16) for pair in ranges for h in pair]
        assert flat[0] == 0 and flat[-1] == 255
        assert all(int(hi, 16) + 1 == int(nxt[0], 16) for (_, hi), nxt in zip(ranges, ranges[1:]))
        assert len(ranges) == min(256, max(1, -(-households // check.ROWS_PER_SHARD)))


def test_a_shard_is_a_hash_range_on_the_household_id_and_only_hex_is_accepted():
    sql = check.points_sql(spec(), ("00", "3f"))
    assert "left(md5(form_json #>> '{form,var_menage,case_id_menage}'), 2) BETWEEN '00' AND '3f'" in sql
    with pytest.raises(check.SpecError):
        check.points_sql(spec(), ("0'; --", "ff"))
    assert check.count_sql(spec()).startswith(
        "SELECT count(DISTINCT form_json #>> '{form,var_menage,case_id_menage}')"
    )


def test_a_cycle_run_in_only_some_areas_is_measured_against_those_areas_only():
    second = {"id": "C2", "lon": LON0 + 200 * M, "lat": LAT0 + 200 * M}
    rows = [("C1", "hh1", gps(55), "contact_1,contact_2"), ("C2", "hh2", gps(45), "contact_1")]
    out = check.run_check(rows, spec(facilities=[FACILITY, second]), fetch)
    tot = check.total_row(out)
    assert tot["buildings"] == 120
    assert tot["cycles"][1]["buildings"] == 120 and tot["cycles"][2]["buildings"] == 60
    assert tot["cycles"][2]["share"] == pytest.approx(tot["cycles"][2]["buildings_reached"] / 60)


# --- cells: the catchment gridded for a map ------------------------------------------------------------


def test_cells_grid_the_catchment_and_count_reached_per_cycle():
    blds = _village(100, spacing=40)
    rows = [["C1", f"hh{i}", gps(i), "contact_1" if i % 2 == 0 else "contact_2"] for i in range(0, 100, 3)]
    out = check.run_check(rows, spec(cell_m=100), fetch)
    (area,) = out["areas"]
    grid = area["cells"]
    assert grid["size_m"] == 100 and grid["cycles"] == [1, 2]
    assert sum(r[2] for r in grid["rows"]) <= len(blds)  # cells under the floor are left out, never invented
    assert all(r[2] >= 5 for r in grid["rows"])
    assert all(0 <= x <= r[2] for r in grid["rows"] for x in r[3])
    assert area["facility"] == [FACILITY["lon"], FACILITY["lat"]]


def test_a_cell_under_the_floor_is_not_returned_but_its_buildings_still_count():
    rows = [["C1", "hh0", gps(0), "contact_1"]]
    with_cells = check.run_check(rows, spec(cell_m=100, min_cell_buildings=10_000), fetch)
    (area,) = with_cells["areas"]
    assert area["cells"]["rows"] == []
    assert area["buildings"] == 60  # the table's denominator is untouched by the map's floor


def test_no_cells_unless_asked_and_a_bad_cell_size_is_refused():
    rows = [["C1", "hh0", gps(0), "contact_1"]]
    assert "cells" not in check.run_check(rows, spec(), fetch)["areas"][0]
    with pytest.raises(check.SpecError):
        spec(cell_m=5)


def test_the_rollup_carries_the_centre_of_its_facilities():
    result = {
        "areas": [
            {
                "group": "A",
                "facility": [8.0, 13.0],
                "buildings": 10,
                "households": 1,
                "data_quality": {"usable": 1},
                "cycles": {},
            },
            {
                "group": "B",
                "facility": [8.2, 13.2],
                "buildings": 10,
                "households": 1,
                "data_quality": {"usable": 1},
                "cycles": {},
            },
        ]
    }
    (row,) = check.rollup(result, {"A": "D1", "B": "D1"})
    assert row["center"] == pytest.approx([8.1, 13.1])
