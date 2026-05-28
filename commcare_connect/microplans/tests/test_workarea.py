"""Tests for the pins → WorkArea payload builder (building-as-WorkArea)."""

from __future__ import annotations

from shapely import wkt

from commcare_connect.microplans.sampling.geo import project_to_meters
from commcare_connect.microplans.workarea import build_work_areas, to_api_payload, to_csv_rows

PINS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [13.155, 11.832]},
            "properties": {"arm": "intervention", "cluster": "C3", "role": "primary", "order_in_cluster": 1},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [13.156, 11.833]},
            "properties": {"arm": "comparison", "cluster": "C7", "role": "alternate", "order_in_cluster": 9},
        },
    ],
}


def test_one_workarea_per_pin_with_tiny_area_semantics():
    was = build_work_areas(PINS, lga="Maiduguri", state="Borno")
    assert len(was) == 2
    for w in was:
        assert w.building_count == 1
        assert w.expected_visit_count == 1
    a, b = was
    assert a.case_properties["role"] == "primary"
    assert a.case_properties["cluster"] == "C3"
    assert b.case_properties["arm"] == "comparison"
    assert a.case_properties["lga"] == "Maiduguri"
    assert a.slug != b.slug  # unique slugs


def test_boundary_is_a_small_square_around_the_pin():
    was = build_work_areas(PINS, boundary_half_m=8.0)
    poly = wkt.loads(was[0].boundary_wkt)
    assert poly.geom_type == "Polygon"
    # Centroid of the square should sit on the pin.
    assert abs(poly.centroid.x - 13.155) < 1e-4
    assert abs(poly.centroid.y - 11.832) < 1e-4
    # Side length ≈ 16m → area ≈ 256 m² (allow projection slack).
    xs, ys = poly.exterior.coords.xy
    px, py, _ = project_to_meters(list(xs), list(ys))
    width_m = max(px) - min(px)
    assert 14 < width_m < 18


def test_ward_defaults_to_arm_but_is_overridable():
    default = build_work_areas(PINS)
    assert default[0].ward == "intervention"
    mapped = build_work_areas(PINS, ward_for_arm={"intervention": "Gwange", "comparison": "Tsaki"})
    assert mapped[0].ward == "Gwange"
    assert mapped[1].ward == "Tsaki"


def test_api_payload_shape():
    payload = to_api_payload(build_work_areas(PINS))
    row = payload[0]
    assert row["centroid"]["type"] == "Point"
    assert row["centroid"]["coordinates"] == [13.155, 11.832]
    assert row["building_count"] == 1
    assert "boundary_wkt" in row
    assert row["case_properties"]["cluster"] == "C3"


def test_csv_rows_use_connect_column_labels():
    rows = to_csv_rows(build_work_areas(PINS, lga="Maiduguri", state="Borno"))
    row = rows[0]
    assert row["Centroid"] == "13.155 11.832"
    assert row["Building Count"] == 1
    assert row["Expected Visit Count"] == 1
    assert row["LGA"] == "Maiduguri"
    assert row["State"] == "Borno"
    assert "POLYGON" in row["Boundary"]
