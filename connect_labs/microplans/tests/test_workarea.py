"""Tests for the pins → WorkArea payload builder (building-as-WorkArea)."""

from __future__ import annotations

import re

from shapely import wkt
from shapely.geometry import shape

from connect_labs.microplans.core.geo import project_to_meters
from connect_labs.microplans.core.workarea import _new_slug, build_work_areas, to_api_payload, to_csv_rows

_SLUG_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{5}$")

PINS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [13.155, 11.832]},
            "properties": {
                "arm": "intervention",
                "cluster": "C3",
                "sample_type": "primary",
                "order_in_cluster": 1,
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [13.156, 11.833]},
            "properties": {
                "arm": "comparison",
                "cluster": "C7",
                "sample_type": "alternate",
                "order_in_cluster": 9,
            },
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
    assert a.case_properties["sample_type"] == "primary"
    assert a.case_properties["cluster"] == "C3"
    assert a.case_properties["lga"] == "Maiduguri"
    # arm is a labs-side field, intentionally NOT in case_properties (keeps the
    # shared/pushed plan blind to study-arm assignment).
    assert a.arm == "intervention"
    assert b.arm == "comparison"
    assert "arm" not in a.case_properties
    assert "arm" not in b.case_properties
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


def test_boundary_uses_the_real_building_footprint_when_present():
    # A ~10m x 6m building footprint riding on the pin's properties.
    footprint = {
        "type": "Polygon",
        "coordinates": [
            [
                [13.15500, 11.83200],
                [13.15509, 11.83200],
                [13.15509, 11.83205],
                [13.15500, 11.83205],
                [13.15500, 11.83200],
            ]
        ],
    }
    pins = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [13.155045, 11.832025]},
                "properties": {
                    "arm": "intervention",
                    "cluster": "C3",
                    "sample_type": "primary",
                    "order_in_cluster": 1,
                    "geom_json": footprint,
                },
            }
        ],
    }
    was = build_work_areas(pins, boundary_buffer_m=3.0)
    poly = wkt.loads(was[0].boundary_wkt)
    assert poly.geom_type == "Polygon"
    raw_poly = shape(footprint)

    def _width_m(p):
        xs, ys = p.exterior.coords.xy
        mx = project_to_meters(list(xs), list(ys))[0]
        return max(mx) - min(mx)

    raw_width = _width_m(raw_poly)
    out_width = _width_m(poly)
    # The exported boundary tracks the real building footprint, just grown by the
    # ~3m doorstep buffer on each side — not the generic centroid square.
    assert out_width > raw_width
    assert raw_width < out_width < raw_width + 10  # ≈ +2*3m, with rounding slack


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


def test_slug_matches_connect_style_short_format():
    # Short, Connect-style Area Slug (e.g. "DA490TL") instead of the old
    # "int-c3-prim-1" scheme — no admin-boundary/cluster info embedded, so no
    # stray characters (e.g. a ":" from an area_id) that Connect's own slugs
    # never carry.
    was = build_work_areas(PINS)
    for w in was:
        assert _SLUG_RE.match(w.slug), w.slug


def test_new_slug_unique_at_scale():
    # A single work-area file can carry up to MAX_WORK_AREAS (20,000) rows —
    # confirm collisions are actually avoided at that scale, not just for 2.
    used = set()
    slugs = [_new_slug(used) for _ in range(20000)]
    assert len(set(slugs)) == 20000
    assert all(_SLUG_RE.match(s) for s in slugs)
