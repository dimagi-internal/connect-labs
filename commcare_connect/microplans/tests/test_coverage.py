"""Tests for coverage mode (pure; fetch mocked, no network/DB)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from commcare_connect.microplans.core.workarea import build_coverage_work_areas, to_api_payload
from commcare_connect.microplans.coverage import frame as coverage_frame
from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame


def test_coverage_caps_work_area_count(monkeypatch):
    """A pathological cell/area combo that yields > MAX_WORK_AREAS cells is a user
    error with an actionable message — not a silently-huge, multi-MB plan."""
    monkeypatch.setattr(coverage_frame, "fetch_buildings", lambda area, min_confidence=None: _scatter(10, seed=1))
    monkeypatch.setattr(
        coverage_frame.clustering,
        "grid_clusters",
        lambda buildings, cell_size_m: SimpleNamespace(psu_frame=list(range(coverage_frame.MAX_WORK_AREAS + 1))),
    )
    with pytest.raises(ValueError, match="work areas"):
        generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=10))


LON0, LAT0 = 13.155, 11.832
M_PER_DEG = 111_320.0


def _scatter(n, seed=0):
    rng = np.random.default_rng(seed)
    dlat = rng.uniform(-400, 400, n) / M_PER_DEG
    dlon = rng.uniform(-400, 400, n) / (M_PER_DEG * np.cos(np.radians(LAT0)))
    return pd.DataFrame({"lon": LON0 + dlon, "lat": LAT0 + dlat, "area_m2": 40.0, "confidence": 0.8})


_AREA = [
    {
        "arm": "coverage",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[13.15, 11.82], [13.16, 11.82], [13.16, 11.83], [13.15, 11.82]]],
        },
    }
]


class TestCoverageFrame:
    def test_grid_cells_cover_all_buildings(self, monkeypatch):
        monkeypatch.setattr(coverage_frame, "fetch_buildings", lambda area, min_confidence=None: _scatter(120, seed=7))
        res = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=150))
        feats = res.areas_geojson["features"]
        # grid is deterministic; every building lands in exactly one cell
        assert sum(f["properties"]["building_count"] for f in feats) == 120
        for f in feats:
            assert f["properties"]["expected_visit_count"] == f["properties"]["building_count"]
            assert f["properties"]["cell_size_m"] == 150.0
            # cell geometry is a closed-ring polygon (the cell box)
            ring = f["geometry"]["coordinates"][0]
            assert len(ring) == 5 and ring[0] == ring[-1]
        assert res.stats[0]["cell_size_m"] == 150.0
        assert res.stats[0]["work_areas"] == len(feats)

    def test_config_defaults(self):
        # coverage wants completeness (MS/OSM roofs too), unlike sampling's 0.7
        cfg = CoverageConfig()
        assert cfg.min_confidence is None
        assert cfg.cell_size_m == 100.0  # 100m × 100m default
        # near-pass-through area filters (coverage covers every household)
        assert cfg.area_min_m2 <= 1.0 and cfg.area_max_m2 >= 1000.0

    def test_smaller_cells_make_more_work_areas(self, monkeypatch):
        monkeypatch.setattr(coverage_frame, "fetch_buildings", lambda area, min_confidence=None: _scatter(200, seed=9))
        coarse = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=400))
        fine = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=50))
        assert len(fine.areas_geojson["features"]) > len(coarse.areas_geojson["features"])

    def test_no_arms_in_output(self, monkeypatch):
        # coverage has no intervention/comparison arms — single coverage zone.
        monkeypatch.setattr(coverage_frame, "fetch_buildings", lambda area, min_confidence=None: _scatter(50, seed=11))
        res = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=150))
        for f in res.areas_geojson["features"]:
            assert "arm" not in f["properties"]
        assert "arm" not in res.stats[0]

    def test_circle_area_input(self, monkeypatch):
        seen = {}

        def fake_fetch(area, min_confidence=None):
            seen["area"] = area
            return _scatter(40, seed=8)

        monkeypatch.setattr(coverage_frame, "fetch_buildings", fake_fetch)
        circle_area = [{"circle": {"lon": LON0, "lat": LAT0, "radius_m": 300}}]
        res = generate_coverage_frame(circle_area, CoverageConfig(cell_size_m=100))
        # the area passed to fetch is the buffered circle (a polygon), not empty
        assert seen["area"].geom_type in ("Polygon", "MultiPolygon")
        assert sum(f["properties"]["building_count"] for f in res.areas_geojson["features"]) == 40


class TestCoverageWorkAreas:
    def test_cluster_as_workarea(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[13.15, 11.82], [13.16, 11.82], [13.16, 11.83], [13.15, 11.83], [13.15, 11.82]]
                        ],
                    },
                    "properties": {"arm": "coverage", "cluster": "C1", "building_count": 50},
                }
            ],
        }
        was = build_coverage_work_areas(fc, lga="Maiduguri", state="Borno")
        assert len(was) == 1
        w = was[0]
        assert w.building_count == 50
        assert w.expected_visit_count == 50  # whole-area coverage
        assert "POLYGON" in w.boundary_wkt
        api = to_api_payload(was)[0]
        assert api["case_properties"]["mode"] == "coverage"
        assert 13.15 <= api["centroid"]["coordinates"][0] <= 13.16
