"""Tests for coverage mode (pure; fetch mocked, no network/DB)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from commcare_connect.microplans.core.workarea import build_coverage_work_areas, to_api_payload
from commcare_connect.microplans.coverage import frame as coverage_frame
from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

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
    def test_balanced_areas_cover_all_buildings(self, monkeypatch):
        monkeypatch.setattr(coverage_frame, "fetch_buildings", lambda area, min_confidence=None: _scatter(100, seed=1))
        res = generate_coverage_frame(_AREA, CoverageConfig(buildings_per_cluster=20, balance_tolerance=0.1))
        feats = res.areas_geojson["features"]
        assert len(feats) == 5  # 100 / 20
        # every building lands in some work area
        assert sum(f["properties"]["building_count"] for f in feats) == 100
        # coverage = visit every household → expected_visit_count == building_count
        for f in feats:
            assert f["properties"]["expected_visit_count"] == f["properties"]["building_count"]
        s = res.stats[0]
        assert s["work_areas"] == 5 and s["min_buildings"] >= 18 and s["max_buildings"] <= 22

    def test_config_defaults_to_no_confidence_gate(self):
        # coverage wants completeness (MS/OSM roofs too), unlike sampling's 0.7
        assert CoverageConfig().min_confidence is None


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
