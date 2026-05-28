"""Tests for area-input normalisation (GeoJSON polygon vs buildings-around-a-pin)."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import shape

from commcare_connect.microplans.core.area_input import resolve_area
from commcare_connect.microplans.core.geo import point_buffer

LON0, LAT0 = 13.155, 11.832


class TestResolveArea:
    def test_geojson_passthrough(self):
        geom = {"type": "Polygon", "coordinates": [[[13.0, 11.0], [13.1, 11.0], [13.1, 11.1], [13.0, 11.0]]]}
        out = resolve_area({"geometry": geom})
        assert out.equals(shape(geom))

    def test_circle_radius_is_metric(self):
        out = resolve_area({"circle": {"lon": LON0, "lat": LAT0, "radius_m": 500}})
        # area of a 500 m circle ≈ pi r^2 = 785398 m^2; check projected area within 2%
        projected = point_buffer(LON0, LAT0, 500)
        # reproject back to meters to measure: use the helper's own circle and compare to ideal
        import numpy as np

        from commcare_connect.microplans.core.geo import project_to_meters

        xs, ys = projected.exterior.coords.xy
        mx, my, _ = project_to_meters(np.array(xs), np.array(ys))
        from shapely.geometry import Polygon

        area_m2 = Polygon(zip(mx, my)).area
        assert math.isclose(area_m2, math.pi * 500**2, rel_tol=0.02)
        assert out.contains(shape({"type": "Point", "coordinates": [LON0, LAT0]}))

    def test_missing_both_raises(self):
        with pytest.raises(ValueError):
            resolve_area({"arm": "intervention"})

    def test_malformed_geometry_raises_valueerror(self):
        # not a 500: a bad GeoJSON must surface as ValueError (→ 400 at the view)
        with pytest.raises(ValueError):
            resolve_area({"geometry": {"type": "Nonsense", "coordinates": "oops"}})

    def test_malformed_circle_raises_valueerror(self):
        with pytest.raises(ValueError):
            resolve_area({"circle": {"lon": "abc", "lat": LAT0, "radius_m": 100}})
