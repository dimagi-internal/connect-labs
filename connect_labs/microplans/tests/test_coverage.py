"""Tests for coverage mode (pure; fetch mocked, no network/DB)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from connect_labs.microplans.core.plan import materialize_work_areas, recompute_area_visits
from connect_labs.microplans.core.workarea import CSV_HEADERS, build_coverage_work_areas, to_api_payload, to_csv_rows
from connect_labs.microplans.coverage import frame as coverage_frame
from connect_labs.microplans.coverage.frame import CoverageConfig, generate_coverage_frame


def test_coverage_caps_work_area_count(monkeypatch):
    """A pathological cell/area combo that yields > MAX_WORK_AREAS cells is a user
    error with an actionable message — not a silently-huge, multi-MB plan."""
    monkeypatch.setattr(
        coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(10, seed=1)
    )
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
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(120, seed=7)
        )
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
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(200, seed=9)
        )
        coarse = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=400))
        fine = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=50))
        assert len(fine.areas_geojson["features"]) > len(coarse.areas_geojson["features"])

    def test_no_arms_in_output(self, monkeypatch):
        # coverage has no intervention/comparison arms — single coverage zone.
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(50, seed=11)
        )
        res = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=150))
        for f in res.areas_geojson["features"]:
            assert "arm" not in f["properties"]
        assert "arm" not in res.stats[0]

    def test_circle_area_input(self, monkeypatch):
        seen = {}

        def fake_fetch(area, min_confidence=None, sources=None):
            seen["area"] = area
            return _scatter(40, seed=8)

        monkeypatch.setattr(coverage_frame, "fetch_buildings", fake_fetch)
        circle_area = [{"circle": {"lon": LON0, "lat": LAT0, "radius_m": 300}}]
        res = generate_coverage_frame(circle_area, CoverageConfig(cell_size_m=100))
        # the area passed to fetch is the buffered circle (a polygon), not empty
        assert seen["area"].geom_type in ("Polygon", "MultiPolygon")
        assert sum(f["properties"]["building_count"] for f in res.areas_geojson["features"]) == 40


def _cluster_plus_lone(seed=3):
    """12 buildings tightly clustered at the origin + one lone tiny building ~500m east.
    The cluster cell has >=2 buildings; the lone cell is a single far-away building."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(12):
        dlat = rng.uniform(-20, 20) / M_PER_DEG
        dlon = rng.uniform(-20, 20) / (M_PER_DEG * np.cos(np.radians(LAT0)))
        rows.append({"lon": LON0 + dlon, "lat": LAT0 + dlat, "area_m2": 60.0, "confidence": 0.8})
    rows.append(  # lone, far (~500m east), tiny roof
        {"lon": LON0 + 500 / (M_PER_DEG * np.cos(np.radians(LAT0))), "lat": LAT0, "area_m2": 5.0, "confidence": 0.8}
    )
    return pd.DataFrame(rows)


class TestCoverageExclusionFilters:
    def _gen(self, monkeypatch, **cfg):
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _cluster_plus_lone()
        )
        return generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=100, **cfg))

    def test_defaults_keep_everything(self, monkeypatch):
        res = self._gen(monkeypatch)
        assert sum(f["properties"]["building_count"] for f in res.areas_geojson["features"]) == 13
        assert res.stats[0]["removed_small_area"] == 0
        assert res.stats[0]["removed_isolated"] == 0
        # metrics are still annotated even with filters off
        assert all("roof_area_m2" in f["properties"] for f in res.areas_geojson["features"])

    def test_min_roof_area_drops_small_cell(self, monkeypatch):
        # lone cell roof = 5 m²; cluster cell roof = 12*60 = 720 m²
        res = self._gen(monkeypatch, min_cell_roof_area_m2=50)
        assert res.stats[0]["removed_small_area"] == 1
        assert sum(f["properties"]["building_count"] for f in res.areas_geojson["features"]) == 12

    def test_isolation_filter_drops_lone_far_cell(self, monkeypatch):
        res = self._gen(monkeypatch, exclude_isolated_singletons=True, isolation_dist_m=150)
        assert res.stats[0]["removed_isolated"] == 1
        # the surviving cells are the clustered ones (>=2 buildings)
        assert all(f["properties"]["building_count"] >= 2 for f in res.areas_geojson["features"])

    def test_isolation_keeps_lone_when_within_distance(self, monkeypatch):
        # generous distance threshold → the lone cell is "near enough", kept
        res = self._gen(monkeypatch, exclude_isolated_singletons=True, isolation_dist_m=1000)
        assert res.stats[0]["removed_isolated"] == 0


class TestCoverageExpectedVisits:
    def _gen(self, monkeypatch, **cfg):
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(120, seed=7)
        )
        return generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=150, **cfg))

    def test_legacy_evc_equals_building_count(self, monkeypatch):
        res = self._gen(monkeypatch)  # population unset
        assert res.stats[0]["people_per_building"] is None
        for f in res.areas_geojson["features"]:
            assert f["properties"]["expected_visit_count"] == f["properties"]["building_count"]
            assert f["properties"]["target_population"] is None

    def test_population_weighted_evc(self, monkeypatch):
        import math

        res = self._gen(monkeypatch, population=4000)
        st = res.stats[0]
        assert st["population"] == 4000
        # ppb = population / retained buildings (all 120 retained here)
        assert st["retained_buildings"] == 120
        assert st["people_per_building"] == pytest.approx(4000 / 120, rel=1e-3)
        ppb = 4000 / 120
        for f in res.areas_geojson["features"]:
            n = f["properties"]["building_count"]
            assert f["properties"]["expected_visit_count"] == max(1, math.ceil(n * ppb))
            assert f["properties"]["target_population"] == round(n * ppb)


class TestCoveragePerArea:
    """Per-area generation (#8/#14): each selected area is fetched + gridded
    independently and its work areas are tagged with the source ward/LGA/state."""

    def test_each_area_tagged_and_counted(self, monkeypatch):
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(40, seed=5)
        )
        areas = [
            {"geometry": _AREA[0]["geometry"], "ward": "Dabi", "lga": "Gwiwa", "state": "Jigawa"},
            {"geometry": _AREA[0]["geometry"], "ward": "Madobi", "lga": "Madobi", "state": "Kano"},
        ]
        res = generate_coverage_frame(areas, CoverageConfig(cell_size_m=150))
        feats = res.areas_geojson["features"]
        wards = {f["properties"]["ward"] for f in feats}
        assert wards == {"Dabi", "Madobi"}
        # every feature carries its area's LGA/state + a unique area-namespaced cluster
        for f in feats:
            p = f["properties"]
            assert p["state"] in ("Jigawa", "Kano") and p["lga"] in ("Gwiwa", "Madobi")
            assert "-" in p["cluster"]  # "<area_id>-C<n>"
        # per-area breakdown present in stats
        per = {a["ward"]: a for a in res.stats[0]["per_area"]}
        assert set(per) == {"Dabi", "Madobi"} and all(a["work_areas"] > 0 for a in per.values())

    def test_drawn_area_without_identity_gets_numeric_ward(self, monkeypatch):
        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _scatter(30, seed=6)
        )
        res = generate_coverage_frame(_AREA, CoverageConfig(cell_size_m=150))  # no ward/lga/state
        assert all(f["properties"]["ward"] == "area_1" for f in res.areas_geojson["features"])


class TestRecomputeAreaVisits:
    """Per-area expected-visit spread (#9): EVC = ceil(wa_buildings × target ÷ area
    RETAINED buildings), per area, recomputed against the non-excluded set."""

    def _wa(self, wid, ward, blds, status="UNASSIGNED"):
        return {"id": wid, "building_count": blds, "status": status, "properties": {"ward": ward}}

    def test_per_area_spread_and_round_up(self):
        was = [
            self._wa("a1", "Dabi", 10),
            self._wa("a2", "Dabi", 3),
            self._wa("b1", "Madobi", 20),
        ]
        recompute_area_visits(was, {"Dabi": 100, "Madobi": 50})
        # Dabi: rate 100/13 = 7.69 → 10*=76.9→77, 3*=23.1→24 ; Madobi: 50/20=2.5 → 20*=50
        assert was[0]["expected_visit_count"] == 77
        assert was[1]["expected_visit_count"] == 24
        assert was[2]["expected_visit_count"] == 50

    def test_excluded_dropped_from_denominator(self):
        was = [
            self._wa("a1", "Dabi", 10),
            self._wa("a2", "Dabi", 10, status="EXCLUDED"),
        ]
        recompute_area_visits(was, {"Dabi": 100})
        # retained = 10 (the excluded cell's 10 don't count) → rate 10 → 10*10 = 100
        assert was[0]["expected_visit_count"] == 100

    def test_min_one_visit(self):
        was = [self._wa("a1", "Dabi", 1)]
        recompute_area_visits(was, {"Dabi": 0.4})  # rate <1 → ceil to 1
        assert was[0]["expected_visit_count"] == 1


class TestCoverageWorkAreaMetrics:
    """The exclusion-filter metrics (roof_area_m2, dist_to_multi_m) must persist on
    each coverage work area so the review page can filter live after creation (#7)."""

    def test_metrics_persist_in_properties(self):
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
                    "properties": {
                        "cluster": "C1",
                        "building_count": 10,
                        "expected_visit_count": 7,
                        "target_population": 7,
                        "roof_area_m2": 420.5,
                        "dist_to_multi_m": 0.0,
                        "cell_size_m": 50.0,
                    },
                }
            ],
        }
        was = materialize_work_areas("coverage", {}, fc, grouping={})
        props = was[0]["properties"]
        assert props["roof_area_m2"] == 420.5
        assert props["dist_to_multi_m"] == 0.0


class TestCoverageEndToEndCSV:
    """Frame → work areas → Connect CSV, with exclusion filters + population set.
    Verifies the actual deliverable (the importable CSV), not just the frame."""

    def test_pipeline_to_connect_csv(self, monkeypatch):
        import math

        monkeypatch.setattr(
            coverage_frame, "fetch_buildings", lambda area, min_confidence=None, sources=None: _cluster_plus_lone()
        )
        res = generate_coverage_frame(
            _AREA, CoverageConfig(cell_size_m=100, min_cell_roof_area_m2=50, population=1000)
        )
        was = build_coverage_work_areas(res.areas_geojson, lga="Madobi", state="Kano")
        rows = to_csv_rows(was)

        # exact Connect importer schema (no dummyField — it isn't in Connect's HEADERS)
        assert set(rows[0].keys()) == set(CSV_HEADERS.values())
        # the small lone cell (5 m² roof) was excluded before export
        assert sum(int(r["Building Count"]) for r in rows) == 12
        retained = res.stats[0]["retained_buildings"]
        ppb = 1000 / retained
        for r in rows:
            assert r["LGA"] == "Madobi" and r["State"] == "Kano"  # Connect rejects blank
            assert r["Boundary"].startswith("POLYGON")
            assert len(r["Centroid"].split()) == 2  # "lon lat"
            n = int(r["Building Count"])
            assert int(r["Expected Visit Count"]) == max(1, math.ceil(n * ppb))
            assert int(r["Target Population"]) == round(n * ppb)


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

    def test_population_weighted_props_flow_to_workarea(self):
        # a population-weighted frame supplies expected_visit_count + target_population
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
                    "properties": {
                        "cluster": "C1",
                        "building_count": 10,
                        "expected_visit_count": 7,
                        "target_population": 7,
                    },
                }
            ],
        }
        w = build_coverage_work_areas(fc, lga="Maiduguri", state="Borno")[0]
        assert w.building_count == 10
        assert w.expected_visit_count == 7  # honoured from props, not = building_count
        assert w.target_population == 7
