"""Tests for the service-delivery GPS layer (pure pieces).

The pipeline-backed fetch is exercised against live labs separately; here we
test the pure units: GPS-string parsing, row -> GeoJSON, lat/lon auto-detection
for override pipelines, and boundary derivation geometry.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import shape

from connect_labs.microplans.service_delivery.hull import derive_boundary
from connect_labs.microplans.service_delivery.points import detect_lat_lon, points_to_geojson, rows_to_points

# A ~500m square block of points near Nairobi (lon, lat).
LON0, LAT0 = 36.8219, -1.2921


def _grid(n=5, step=0.0015):
    pts = []
    for i in range(n):
        for j in range(n):
            pts.append({"lon": LON0 + i * step, "lat": LAT0 + j * step})
    return pts


class TestRowsToPoints:
    def test_parses_packed_location_string(self):
        # Pipeline override may return the raw "lat lon alt acc" base column.
        rows = [{"location": "-1.2921 36.8219 1600 5", "username": "flw1", "status": "approved"}]
        pts = rows_to_points(rows)
        assert len(pts) == 1
        assert pts[0]["lon"] == pytest.approx(36.8219)
        assert pts[0]["lat"] == pytest.approx(-1.2921)
        assert pts[0]["username"] == "flw1"

    def test_uses_explicit_lat_lon_columns(self):
        rows = [{"latitude": -1.2921, "longitude": 36.8219, "username": "flw1"}]
        pts = rows_to_points(rows)
        assert pts[0]["lat"] == pytest.approx(-1.2921)
        assert pts[0]["lon"] == pytest.approx(36.8219)

    def test_drops_rows_without_gps(self):
        rows = [
            {"latitude": -1.29, "longitude": 36.82},
            {"latitude": None, "longitude": None},
            {"location": ""},
            {"username": "no-gps"},
        ]
        pts = rows_to_points(rows)
        assert len(pts) == 1

    def test_drops_null_island_and_out_of_range(self):
        rows = [
            {"latitude": 0.0, "longitude": 0.0},  # null island
            {"latitude": 999, "longitude": 36.0},  # out of range
            {"latitude": -1.29, "longitude": 36.82},  # good
        ]
        pts = rows_to_points(rows)
        assert len(pts) == 1
        assert pts[0]["lat"] == pytest.approx(-1.29)


class TestDetectLatLon:
    def test_prefers_explicit_columns(self):
        assert detect_lat_lon(["latitude", "longitude", "x"]) == ("latitude", "longitude")

    def test_suffix_columns(self):
        assert detect_lat_lon(["gps_lat", "gps_lon", "foo"]) == ("gps_lat", "gps_lon")

    def test_falls_back_to_location_string(self):
        assert detect_lat_lon(["location", "username"]) == ("location", "location")

    def test_returns_none_when_absent(self):
        assert detect_lat_lon(["username", "status"]) is None


class TestPointsToGeojson:
    def test_builds_feature_collection_with_props(self):
        pts = [{"lon": 36.82, "lat": -1.29, "username": "flw1", "status": "approved"}]
        fc = points_to_geojson(pts, opportunity_id=42, color="#ff0000")
        assert fc["type"] == "FeatureCollection"
        f = fc["features"][0]
        assert f["geometry"]["coordinates"] == [36.82, -1.29]
        assert f["properties"]["opportunity_id"] == 42
        assert f["properties"]["color"] == "#ff0000"
        assert f["properties"]["username"] == "flw1"


class TestDeriveBoundary:
    def test_concave_hull_contains_all_points(self):
        pts = _grid()
        geom = shape(derive_boundary(pts, method="concave", buffer_m=20))
        for p in pts:
            assert geom.contains(shape({"type": "Point", "coordinates": [p["lon"], p["lat"]]}))

    def test_convex_is_looser_than_concave(self):
        # An L-shaped cloud: convex hull fills the notch, concave should not.
        pts = []
        for i in range(8):
            pts.append({"lon": LON0 + i * 0.001, "lat": LAT0})
            pts.append({"lon": LON0, "lat": LAT0 + i * 0.001})
        convex = shape(derive_boundary(pts, method="convex", buffer_m=0))
        concave = shape(derive_boundary(pts, method="concave", concavity=0.1, buffer_m=0))
        assert convex.area > concave.area

    def test_buffer_grows_area(self):
        pts = _grid()
        small = shape(derive_boundary(pts, method="concave", buffer_m=0))
        big = shape(derive_boundary(pts, method="concave", buffer_m=100))
        assert big.area > small.area

    def test_returns_polygon_geojson(self):
        geom = derive_boundary(_grid(), method="concave")
        assert geom["type"] in ("Polygon", "MultiPolygon")
        assert geom["coordinates"]

    def test_handles_two_points_via_buffer(self):
        pts = [{"lon": LON0, "lat": LAT0}, {"lon": LON0 + 0.001, "lat": LAT0}]
        geom = shape(derive_boundary(pts, method="concave", buffer_m=30))
        assert geom.area > 0

    def test_raises_on_empty(self):
        with pytest.raises(ValueError):
            derive_boundary([], method="concave")

    def test_buffer_distance_is_metric(self):
        # A single point buffered by 100m should yield a circle whose radius is
        # ~100m at the equator-ish latitude (i.e. NOT 100 degrees).
        geom = shape(derive_boundary([{"lon": LON0, "lat": LAT0}], method="concave", buffer_m=100))
        # bounding box width in degrees should be ~ 2*100m / (111320*cos(lat))
        minx, miny, maxx, maxy = geom.bounds
        width_deg = maxx - minx
        expected = 2 * 100 / (111320 * math.cos(math.radians(LAT0)))
        assert width_deg == pytest.approx(expected, rel=0.15)


def test_downsample_features_under_cap_is_noop():
    from connect_labs.microplans.service_delivery.points import downsample_features

    feats = [{"id": i} for i in range(10)]
    out, sampled, total = downsample_features(feats, max_n=100)
    assert out == feats and sampled is False and total == 10


def test_downsample_features_over_cap_strides_evenly():
    from connect_labs.microplans.service_delivery.points import downsample_features

    feats = [{"id": i} for i in range(1000)]
    out, sampled, total = downsample_features(feats, max_n=100)
    assert sampled is True and total == 1000
    assert len(out) <= 100  # bounded
    # uniform stride (ceil(1000/100)=10) preserves spatial spread, no silent head-truncation
    assert out[0]["id"] == 0 and out[1]["id"] == 10
