"""Estimated population for an area with none of its own (drawn / pin / upload).

The arithmetic (``combine_ward_shares``) and the orchestration
(``estimate_area_population``, with the ward lookup and footprint fetch stubbed)
run with no database or network. ``overlapping_population_wards`` runs against the
PostGIS test DB, and the task wiring with every fetch stubbed.
"""

from __future__ import annotations

import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from connect_labs.microplans.core import area_population as ap
from connect_labs.microplans.core.area_population import (
    MAX_WARDS_FOR_BUILDING_SHARES,
    combine_ward_shares,
    count_points_within,
    estimate_area_population,
)

POPS_A = {"worldpop_total": 10000.0, "worldpop_u5": 2000.0, "meta_total": 9000.0}
POPS_B = {"worldpop_total": 4000.0, "worldpop_u5": 800.0}


def _ward(name, pops, *, inside=None, total=None, area_fraction=0.0, provider="geopode"):
    return {
        "name": name,
        "provider": provider,
        "populations": pops,
        "inside": inside,
        "total": total,
        "area_fraction": area_fraction,
    }


class TestCombineWardShares:
    def test_share_of_a_wards_buildings_scales_its_populations(self):
        out = combine_ward_shares([_ward("A", POPS_A, inside=250, total=1000, area_fraction=0.9)])
        # 25% of A's buildings → 25% of each of A's figures; the area share is unused.
        assert out["populations_est"] == {"meta_total": 2250.0, "worldpop_total": 2500.0, "worldpop_u5": 500.0}
        assert out["population_basis"] == [{"ward": "A", "provider": "geopode", "share": 0.25, "basis": "buildings"}]

    def test_sums_across_wards_keeping_only_keys_every_contributor_has(self):
        out = combine_ward_shares(
            [_ward("A", POPS_A, inside=100, total=1000), _ward("B", POPS_B, inside=50, total=100)]
        )
        # worldpop: 0.1×10000 + 0.5×4000. meta_total is dropped: B contributes but
        # has no Meta figure, so a Meta sum would silently undercount.
        assert out["populations_est"] == {"worldpop_total": 3000.0, "worldpop_u5": 600.0}

    def test_a_ward_contributing_nothing_does_not_veto_a_key(self):
        out = combine_ward_shares([_ward("A", POPS_A, inside=100, total=1000), _ward("B", POPS_B, inside=0, total=80)])
        assert out["populations_est"]["meta_total"] == 900.0
        assert [b["share"] for b in out["population_basis"]] == [0.1, 0.0]

    def test_ward_with_no_buildings_falls_back_to_area_share(self):
        out = combine_ward_shares([_ward("A", POPS_A, inside=0, total=0, area_fraction=0.2)])
        assert out["populations_est"]["worldpop_total"] == 2000.0
        assert out["population_basis"][0]["basis"] == "area"

    def test_unfetched_ward_total_falls_back_to_area_share(self):
        out = combine_ward_shares([_ward("A", POPS_A, inside=None, total=None, area_fraction=0.5)])
        assert out["populations_est"]["worldpop_u5"] == 1000.0
        assert out["population_basis"][0]["basis"] == "area"

    def test_share_is_clamped_to_the_whole_ward(self):
        # Centroids on a ward edge can make inside exceed the ward's own count.
        out = combine_ward_shares([_ward("A", POPS_A, inside=1010, total=1000)])
        assert out["populations_est"]["worldpop_total"] == 10000.0
        assert out["population_basis"][0]["share"] == 1.0

    def test_area_with_no_buildings_estimates_zero_not_blank(self):
        out = combine_ward_shares([_ward("A", POPS_A, inside=0, total=500), _ward("B", POPS_B, inside=0, total=40)])
        assert out["populations_est"] == {"worldpop_total": 0.0, "worldpop_u5": 0.0}

    def test_non_numeric_population_values_are_ignored(self):
        pops = {"worldpop_total": "1000", "meta_total": None, "grid3_v3_total": "n/a", "geopode_total": float("nan")}
        out = combine_ward_shares([_ward("A", pops, inside=1, total=2)])
        assert out["populations_est"] == {"worldpop_total": 500.0}

    def test_no_wards_gives_an_empty_estimate(self):
        assert combine_ward_shares([]) == {"populations_est": {}, "population_basis": []}


def test_count_points_within():
    sq = box(0, 0, 1, 1)
    assert count_points_within(sq, [0.5, 0.2, 1.5, -0.1], [0.5, 0.9, 0.5, 0.5]) == 2
    assert count_points_within(sq, [], []) == 0


# Two side-by-side wards (W1 west, W2 east) and a ward that only touches the area.
W1 = box(0, 0, 1, 1)
W2 = box(1, 0, 2, 1)
EDGE = box(-1, 0, 0, 1)  # shares only the x=0 edge with AREA below
AREA = box(0, 0.25, 1.5, 0.75)  # half of W1's height, west half of W2


def _wards_rows(*specs):
    return [
        {"boundary_id": name, "name": name, "source": "geopode", "admin_level": 3, "populations": pops, "geometry": g}
        for name, g, pops in specs
    ]


def _area_buildings(points):
    return pd.DataFrame({"lon": [p[0] for p in points], "lat": [p[1] for p in points]})


class TestEstimateAreaPopulation:
    def test_weights_each_ward_by_its_buildings_inside_the_area(self, monkeypatch):
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.overlapping_population_wards",
            lambda area: _wards_rows(("W1", W1, POPS_A), ("W2", W2, POPS_B), ("EDGE", EDGE, POPS_A)),
        )
        totals = {W1.wkt: 10, W2.wkt: 4}
        fetched = []

        def fake_fetch(geom, min_confidence=None, with_geom=False):
            fetched.append((geom.wkt, min_confidence, with_geom))
            return pd.DataFrame({"lon": [0.0] * totals[geom.wkt]})

        # 3 of the area's buildings in W1, 2 in W2.
        df = _area_buildings([(0.2, 0.5), (0.4, 0.5), (0.6, 0.5), (1.2, 0.5), (1.4, 0.5)])
        out = estimate_area_population(AREA, df, min_confidence=0.7, fetch=fake_fetch)

        # W1: 3/10 × 10000 + W2: 2/4 × 4000 = 5000.
        assert out["populations_est"]["worldpop_total"] == 5000.0
        assert out["populations_est"]["worldpop_u5"] == 1000.0  # 0.3 × 2000 + 0.5 × 800
        assert [(b["ward"], b["share"], b["basis"]) for b in out["population_basis"]] == [
            ("W1", 0.3, "buildings"),
            ("W2", 0.5, "buildings"),
        ]
        # The edge-only ward is skipped (no fetch); totals use the same confidence.
        assert sorted(w for w, _, _ in fetched) == sorted([W1.wkt, W2.wkt])
        assert all(c == 0.7 and g is False for _, c, g in fetched)
        assert "population_note" not in out

    def test_too_many_wards_uses_area_shares_without_fetching(self, monkeypatch):
        n = MAX_WARDS_FOR_BUILDING_SHARES + 1
        strips = [(f"S{i}", box(i, 0, i + 1, 1), {"worldpop_total": 100.0}) for i in range(n)]
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.overlapping_population_wards",
            lambda area: _wards_rows(*strips),
        )

        def no_fetch(*a, **k):
            raise AssertionError("ward footprints must not be fetched past the cap")

        area = box(0, 0, n, 0.5)  # half of every strip
        out = estimate_area_population(area, _area_buildings([]), fetch=no_fetch)
        assert out["populations_est"]["worldpop_total"] == pytest.approx(50.0 * n)
        assert {b["basis"] for b in out["population_basis"]} == {"area"}
        assert str(n) in out["population_note"]

    def test_failed_ward_fetch_falls_back_to_area_share(self, monkeypatch):
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.overlapping_population_wards",
            lambda area: _wards_rows(("W1", W1, POPS_A)),
        )

        def failing_fetch(*a, **k):
            raise ValueError("Area is too large")

        out = estimate_area_population(box(0, 0, 0.5, 1), _area_buildings([(0.2, 0.5)]), fetch=failing_fetch)
        assert out["population_basis"][0]["basis"] == "area"
        assert out["populations_est"]["worldpop_total"] == 5000.0

    def test_no_overlapping_ward_gives_none(self, monkeypatch):
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.overlapping_population_wards", lambda area: []
        )
        assert estimate_area_population(AREA, _area_buildings([]), fetch=lambda *a, **k: None) is None

    def test_self_crossing_drawn_polygon_is_repaired(self, monkeypatch):
        seen = []

        def wards(area):
            seen.append(area.is_valid)
            return []

        monkeypatch.setattr("connect_labs.microplans.core.admin_boundaries.overlapping_population_wards", wards)
        bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
        assert not bowtie.is_valid
        estimate_area_population(bowtie, _area_buildings([]), fetch=lambda *a, **k: None)
        assert seen == [True]


class TestPreviewAreaStatsTask:
    """The estimate rides on the existing building-count entry, only for areas
    without populations, and never costs the user the counts."""

    @pytest.fixture(autouse=True)
    def _stub_fetch(self, monkeypatch):
        monkeypatch.setattr("connect_labs.microplans.tasks.set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(
            "connect_labs.microplans.core.footprints.fetch_buildings",
            lambda area, **k: pd.DataFrame({"lon": [0.1, 0.2], "lat": [0.1, 0.2], "dataset": ["OpenStreetMap"] * 2}),
        )

    def _run(self, areas):
        from connect_labs.microplans.tasks import preview_area_stats_task

        return preview_area_stats_task.run(areas, None)

    def test_only_areas_without_populations_get_an_estimate(self, monkeypatch):
        calls = []

        def fake_estimate(area, df, **k):
            calls.append(len(df))
            return {"populations_est": {"worldpop_total": 12.0}, "population_basis": []}

        monkeypatch.setattr(ap, "estimate_area_population", fake_estimate)
        geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        out = self._run([{"geometry": geom, "populations": {"worldpop_total": 5.0}}, {"geometry": geom}])
        ward, drawn = out["stats"]
        assert ward == {"index": 0, "source_counts": {"OpenStreetMap": 2}, "total": 2}
        assert drawn["populations_est"] == {"worldpop_total": 12.0}
        assert drawn["source_counts"] == {"OpenStreetMap": 2} and drawn["total"] == 2
        assert calls == [2]  # estimated from the drawn area's own fetched buildings

    def test_a_failing_estimate_keeps_the_building_counts(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(ap, "estimate_area_population", boom)
        out = self._run([{"circle": {"lon": 3.0, "lat": 6.0, "radius_m": 200}}])
        assert out["status"] == "ok"
        assert out["stats"] == [{"index": 0, "source_counts": {"OpenStreetMap": 2}, "total": 2}]


def _make_boundary(name, level, wkt, bid, *, source="geopode", extra=None):
    from django.contrib.gis.geos import GEOSGeometry, MultiPolygon

    from connect_labs.labs.admin_boundaries.models import AdminBoundary

    return AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=level,
        name=name,
        boundary_id=bid,
        geometry=MultiPolygon(GEOSGeometry(wkt, srid=4326), srid=4326),
        source=source,
        extra=extra or {},
    )


@pytest.mark.django_db
def test_overlapping_population_wards_dedupes_and_keeps_the_finest_level():
    from connect_labs.microplans.core.admin_boundaries import overlapping_population_wards

    pops = {"populations": {"worldpop_total": 100.0}}
    w1 = "POLYGON((13.0 11.0, 13.1 11.0, 13.1 11.1, 13.0 11.1, 13.0 11.0))"
    w2 = "POLYGON((13.1 11.0, 13.2 11.0, 13.2 11.1, 13.1 11.1, 13.1 11.0))"
    far = "POLYGON((20.0 20.0, 20.1 20.0, 20.1 20.1, 20.0 20.1, 20.0 20.0))"
    lga = "POLYGON((13.0 11.0, 13.2 11.0, 13.2 11.2, 13.0 11.2, 13.0 11.0))"
    # W1 twice: geopode wins over its grid3 twin.
    _make_boundary("Ward One", 3, w1, "gp-1", source="geopode", extra={"populations": {"geopode_total": 90.0}})
    _make_boundary("Ward One", 3, w1, "g3-1", source="grid3", extra=pops)
    # W2: the geopode twin has no bag, so the grid3 row (which has one) is used.
    _make_boundary("Ward Two", 3, w2, "gp-2", source="geopode", extra={})
    _make_boundary("Ward Two", 3, w2, "g3-2", source="grid3", extra=pops)
    _make_boundary("Far Ward", 3, far, "gp-far", source="geopode", extra=pops)
    # A coarser level with populations is ignored while wards have them.
    _make_boundary("The LGA", 2, lga, "lga-1", source="geopode", extra=pops)

    rows = overlapping_population_wards(box(13.05, 11.02, 13.15, 11.08))
    assert [(r["name"], r["boundary_id"], r["source"]) for r in rows] == [
        ("Ward One", "gp-1", "geopode"),
        ("Ward Two", "g3-2", "grid3"),
    ]
    assert rows[0]["populations"] == {"geopode_total": 90.0}
    assert rows[0]["geometry"].geom_type == "MultiPolygon"
