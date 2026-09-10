"""Tests for planning-gap detection (design brief §7) — mocked
fetch_buildings/work-area lookups (no network/DB), real
microplans.core.clustering.grid_clusters (pure computation)."""

from __future__ import annotations

import pandas as pd
import pytest
from shapely.geometry import shape

from connect_labs.mopup.core import gaps

# A ward boundary big enough to hold every synthetic building below.
_WARD_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]],
}


def _buildings_df(rows):
    """rows: list of (lon, lat) tuples."""
    return pd.DataFrame(
        {
            "lon": [r[0] for r in rows],
            "lat": [r[1] for r in rows],
            "area_m2": [50.0] * len(rows),
            "confidence": [None] * len(rows),
            "dataset": ["overture"] * len(rows),
        }
    )


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


class TestBuildingsNotCovered:
    def test_no_existing_boundaries_returns_everything(self, monkeypatch):
        buildings = _buildings_df([(0.001, 0.001), (0.005, 0.005)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        result = gaps.buildings_not_covered(object(), [])
        assert len(result) == 2

    def test_buildings_inside_existing_wa_are_excluded(self, monkeypatch):
        # One building inside the existing WA square (0,0)-(0.002,0.002),
        # one clearly outside it.
        buildings = _buildings_df([(0.001, 0.001), (0.008, 0.008)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        existing = [_square(0.0, 0.0, 0.002, 0.002)]
        result = gaps.buildings_not_covered(object(), existing)
        assert len(result) == 1
        assert result.iloc[0]["lon"] == pytest.approx(0.008)

    def test_empty_buildings_returns_empty(self, monkeypatch):
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: _buildings_df([]))
        result = gaps.buildings_not_covered(object(), [_square(0.0, 0.0, 0.002, 0.002)])
        assert result.empty

    def test_pre_built_buildings_skip_fetch_buildings_entirely(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("fetch_buildings should not be called when buildings= is given")

        monkeypatch.setattr(gaps, "fetch_buildings", boom)
        buildings = _buildings_df([(0.001, 0.001), (0.008, 0.008)])
        existing = [_square(0.0, 0.0, 0.002, 0.002)]
        result = gaps.buildings_not_covered(shape(_WARD_BOUNDARY), existing, buildings=buildings)
        assert len(result) == 1
        assert result.iloc[0]["lon"] == pytest.approx(0.008)

    def test_pre_built_buildings_outside_the_ward_boundary_are_clipped(self, monkeypatch):
        # Real bug, caught live: a pre-built (uploaded) DataFrame is matched
        # to a ward by NAME only (see buildings_from_upload), never by
        # geometry -- unlike fetch_buildings, which only ever returns
        # buildings already inside the queried area. Without an explicit
        # clip here, an upload row tagged with the right ward name but
        # sitting outside that ward's actual reviewed boundary would
        # incorrectly become a gap-fill work area.
        def boom(*a, **k):
            raise AssertionError("fetch_buildings should not be called when buildings= is given")

        monkeypatch.setattr(gaps, "fetch_buildings", boom)
        # One building inside _WARD_BOUNDARY (0,0)-(0.01,0.01), one clearly
        # outside it -- no existing work areas to exclude, so only the
        # ward-boundary clip is at play.
        buildings = _buildings_df([(0.005, 0.005), (5.0, 5.0)])
        result = gaps.buildings_not_covered(shape(_WARD_BOUNDARY), [], buildings=buildings)
        assert len(result) == 1
        assert result.iloc[0]["lon"] == pytest.approx(0.005)


class TestFilterUploadToWards:
    def _df(self, rows):
        return pd.DataFrame(rows)

    def test_keeps_only_rows_matching_one_of_the_given_wards(self):
        df = self._df(
            [
                {"wardname": "Nafada Central", "lganame": "Nafada", "statename": "Gombe"},
                {"wardname": "Birin Bolawa", "lganame": "Nafada", "statename": "Gombe"},
                {"wardname": "Some Other Ward", "lganame": "Other LGA", "statename": "Other State"},
            ]
        )
        wards = [{"ward": "Nafada Central", "lga": "Nafada", "state": "Gombe"}]
        result = gaps.filter_upload_to_wards(df, wards)
        assert len(result) == 1
        assert result.iloc[0]["wardname"] == "Nafada Central"

    def test_matches_multiple_wards_and_normalizes_case_and_whitespace(self):
        df = self._df(
            [
                {"wardname": "  nafada central  ", "lganame": "NAFADA", "statename": "gombe"},
                {"wardname": "Birin Bolawa", "lganame": "Nafada", "statename": "Gombe"},
            ]
        )
        wards = [
            {"ward": "Nafada Central", "lga": "Nafada", "state": "Gombe"},
            {"ward": "Birin Bolawa", "lga": "Nafada", "state": "Gombe"},
        ]
        result = gaps.filter_upload_to_wards(df, wards)
        assert len(result) == 2

    def test_empty_ward_list_matches_nothing(self):
        df = self._df([{"wardname": "Nafada Central", "lganame": "Nafada", "statename": "Gombe"}])
        assert gaps.filter_upload_to_wards(df, []).empty

    def test_missing_required_column_raises_keyerror(self):
        df = self._df([{"wardname": "W", "lganame": "L"}])  # no statename
        with pytest.raises(KeyError, match="statename"):
            gaps.filter_upload_to_wards(df, [{"ward": "W", "lga": "L", "state": "S"}])


class TestBuildingsFromUpload:
    def _df(self, rows):
        """rows: list of dicts with latitude/longitude/wardname/lganame/statename
        (+ optional area_in_meters/confidence), mirroring the real CSV shape."""
        return pd.DataFrame(rows)

    def test_filters_to_the_matching_ward_only(self):
        df = self._df(
            [
                {
                    "latitude": 11.09,
                    "longitude": 11.33,
                    "wardname": "Nafada Central",
                    "lganame": "Nafada",
                    "statename": "Gombe",
                    "area_in_meters": 14.35,
                    "confidence": 0.66,
                },
                {
                    "latitude": 10.80,
                    "longitude": 11.34,
                    "wardname": "Birin Bolawa",
                    "lganame": "Nafada",
                    "statename": "Gombe",
                    "area_in_meters": 28.95,
                    "confidence": 0.79,
                },
            ]
        )
        result = gaps.buildings_from_upload(df, "Nafada Central", "Nafada", "Gombe")
        assert len(result) == 1
        assert result.iloc[0]["lat"] == pytest.approx(11.09)
        assert result.iloc[0]["lon"] == pytest.approx(11.33)
        assert result.iloc[0]["area_m2"] == pytest.approx(14.35)
        assert result.iloc[0]["confidence"] == pytest.approx(0.66)
        assert result.iloc[0]["dataset"] == "uploaded"

    def test_matching_is_exact_not_fuzzy_but_normalizes_case_and_whitespace(self):
        df = self._df(
            [
                {
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "wardname": "  nafada central  ",
                    "lganame": "NAFADA",
                    "statename": "gombe",
                },
                {
                    "latitude": 3.0,
                    "longitude": 4.0,
                    "wardname": "Nafada Centrall",  # deliberately NOT a match
                    "lganame": "Nafada",
                    "statename": "Gombe",
                },
            ]
        )
        result = gaps.buildings_from_upload(df, "Nafada Central", "Nafada", "Gombe")
        assert len(result) == 1
        assert result.iloc[0]["lat"] == pytest.approx(1.0)

    def test_rows_for_other_wards_are_never_matched(self):
        df = self._df([{"latitude": 1.0, "longitude": 2.0, "wardname": "Elsewhere", "lganame": "X", "statename": "Y"}])
        result = gaps.buildings_from_upload(df, "Nafada Central", "Nafada", "Gombe")
        assert result.empty

    def test_missing_optional_columns_default_to_none(self):
        df = self._df([{"latitude": 1.0, "longitude": 2.0, "wardname": "W", "lganame": "L", "statename": "S"}])
        result = gaps.buildings_from_upload(df, "W", "L", "S")
        assert len(result) == 1
        assert result.iloc[0]["area_m2"] is None
        assert result.iloc[0]["confidence"] is None

    def test_missing_required_column_raises_keyerror(self):
        df = self._df([{"latitude": 1.0, "longitude": 2.0, "wardname": "W", "lganame": "L"}])  # no statename
        with pytest.raises(KeyError, match="statename"):
            gaps.buildings_from_upload(df, "W", "L", "S")


class TestPlanningGapFeatures:
    def test_grids_remainder_into_tagged_features(self, monkeypatch):
        # Two buildings far enough apart to land in different 100m cells.
        buildings = _buildings_df([(0.0, 0.0), (0.005, 0.005)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        features, points = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), []
        )
        assert len(features) >= 1
        for f in features:
            assert f["type"] == "Feature"
            assert f["properties"]["area_id"] == "mopup-kano-rano-sabon-gari"
            assert f["properties"]["ward"] == "Sabon Gari"
            assert f["properties"]["cluster"].startswith("mopup-kano-rano-sabon-gari-gap-")
            assert f["properties"]["building_count"] >= 1
        assert len(points) == 2
        assert {"lon", "lat"} <= points[0].keys()

    def test_existing_wa_boundaries_reduce_the_gridded_remainder(self, monkeypatch):
        buildings = _buildings_df([(0.001, 0.001), (0.008, 0.008)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        # Excluding the WA covering the first building leaves only the second.
        existing = [_square(0.0, 0.0, 0.002, 0.002)]
        features, points = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), existing
        )
        total_buildings = sum(f["properties"]["building_count"] for f in features)
        assert total_buildings == 1
        assert len(points) == 1
        assert points[0]["lon"] == pytest.approx(0.008)

    def test_no_remainder_returns_empty(self, monkeypatch):
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: _buildings_df([]))
        features, points = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), []
        )
        assert features == []
        assert points == []

    def test_min_buildings_per_cell_drops_small_cells(self, monkeypatch):
        # One isolated building (its own cell, n_buildings=1) plus two close
        # together (share a cell, n_buildings=2).
        buildings = _buildings_df([(0.009, 0.009), (0.0001, 0.0001), (0.00011, 0.00011)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        features, points = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), [], min_buildings_per_cell=2
        )
        assert len(features) == 1
        assert features[0]["properties"]["building_count"] == 2
        # min_buildings_per_cell only drops CELLS from the gridded output --
        # the raw point list still carries every building in the remainder.
        assert len(points) == 3

    def test_min_confidence_and_sources_are_forwarded_to_fetch_buildings(self, monkeypatch):
        seen = {}

        def fake_fetch_buildings(area, **kw):
            seen.update(kw)
            return _buildings_df([(0.001, 0.001)])

        monkeypatch.setattr(gaps, "fetch_buildings", fake_fetch_buildings)
        gaps.planning_gap_features(
            "Sabon Gari",
            "Rano",
            "Kano",
            "mopup-kano-rano-sabon-gari",
            object(),
            [],
            min_confidence=0.75,
            sources=["Google Open Buildings"],
        )
        assert seen["min_confidence"] == 0.75
        assert seen["sources"] == ["Google Open Buildings"]

    def test_visits_per_building_sets_expected_visit_count_estimate(self, monkeypatch):
        buildings = _buildings_df([(0.0, 0.0), (0.0001, 0.0001), (0.0002, 0.0002)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        features, _points = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), [], visits_per_building=2.5
        )
        assert len(features) == 1
        assert features[0]["properties"]["building_count"] == 3
        assert features[0]["properties"]["expected_visit_count"] == round(2.5 * 3)

    def test_no_visits_per_building_keeps_building_count_placeholder(self, monkeypatch):
        buildings = _buildings_df([(0.0, 0.0), (0.0001, 0.0001)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        features, _points = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), []
        )
        assert features[0]["properties"]["expected_visit_count"] == features[0]["properties"]["building_count"]


class TestWardVisitsPerBuilding:
    def test_computes_ratio_over_concluded_work_areas_in_the_ward(self):
        all_rows = [
            {"ward": "Sabon Gari", "status": "VISITED", "approved_hsd_count": 8, "building_count": 4},
            {"ward": "Sabon Gari", "status": "EXPECTED_VISIT_REACHED", "approved_hsd_count": 2, "building_count": 2},
            # Different ward -- must not pollute Sabon Gari's rate.
            {"ward": "Unguwar Arewa", "status": "VISITED", "approved_hsd_count": 100, "building_count": 1},
        ]
        rate = gaps.ward_visits_per_building(all_rows, "Sabon Gari")
        assert rate == pytest.approx((8 + 2) / (4 + 2))

    def test_excludes_not_yet_visited_work_areas(self):
        all_rows = [
            {"ward": "Sabon Gari", "status": "NOT_VISITED", "approved_hsd_count": 0, "building_count": 10},
            {"ward": "Sabon Gari", "status": "VISITED", "approved_hsd_count": 4, "building_count": 2},
        ]
        rate = gaps.ward_visits_per_building(all_rows, "Sabon Gari")
        assert rate == pytest.approx(4 / 2)

    def test_returns_zero_not_zerodivision_when_no_eligible_data(self):
        assert gaps.ward_visits_per_building([], "Sabon Gari") == 0.0
        all_rows = [{"ward": "Sabon Gari", "status": "NOT_VISITED", "approved_hsd_count": 0, "building_count": 5}]
        assert gaps.ward_visits_per_building(all_rows, "Sabon Gari") == 0.0

    def test_gates_on_actual_visit_activity_not_the_status_property(self):
        # Real bug, caught live against program 217/opportunity 2154: a work
        # area's CommCare HQ case `status` stayed NOT_VISITED even after real
        # HSD visit forms were submitted for it (the case property and the
        # visit record don't always move together) -- gating on `status` (as
        # EVC-shortfall does) made this return a false 0.0 for a ward where
        # hundreds of approved visits had clearly happened. Gate on
        # `approved_hsd_count > 0` instead.
        all_rows = [
            {"ward": "Sabon Gari", "status": "NOT_VISITED", "approved_hsd_count": 6, "building_count": 3},
        ]
        rate = gaps.ward_visits_per_building(all_rows, "Sabon Gari")
        assert rate == pytest.approx(6 / 3)


class _FakeRow:
    def __init__(self, entity_id, **computed):
        self.entity_id = entity_id
        self.computed = computed


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakePipeline:
    """Scripts core.areas.work_area_ids_for_ward's cchq_cases lookup and
    core.geometry.fetch_work_area_geometry's connect_export lookup off the
    same pair of canned row sets, keyed by data_source.type — mirrors
    test_areas.py's TestWardChildrenPerBuilding fake."""

    def __init__(self, wa_rows, geometry_rows):
        self._wa = wa_rows
        self._geometry = geometry_rows

    def stream_analysis_ignore_events(self, config, opportunity_id, force_refresh=False):
        if config.data_source.type == "cchq_cases":
            return _FakeResult(self._wa)
        return _FakeResult(self._geometry)


class TestWorkAreaBoundariesForWard:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            gaps.work_area_boundaries_for_ward(None, 1, "Sabon Gari", "Rano", "Kano")

    def test_joins_ids_against_geometry(self):
        wa_rows = [
            _FakeRow("wa-1", ward="Sabon Gari", lga="Rano", state="Kano"),
            _FakeRow("wa-2", ward="Sabon Gari", lga="Rano", state="Kano"),
            _FakeRow("wa-3", ward="Other Ward", lga="Rano", state="Kano"),
        ]
        geometry_rows = [
            _FakeRow("wa-1", wa_case_id="wa-1", boundary=_square(0, 0, 1, 1), centroid=None),
            _FakeRow("wa-2", wa_case_id="wa-2", boundary=None, centroid=None),  # no boundary -> skipped
            _FakeRow("wa-3", wa_case_id="wa-3", boundary=_square(5, 5, 6, 6), centroid=None),
        ]
        pipeline = _FakePipeline(wa_rows, geometry_rows)
        boundaries = gaps.work_area_boundaries_for_ward(pipeline, 1, "Sabon Gari", "Rano", "Kano")
        # wa-1 has a boundary; wa-2 has none (dropped); wa-3 is a different ward
        # (never in the id set to begin with).
        assert boundaries == [_square(0, 0, 1, 1)]

    def test_no_matching_work_areas_returns_empty(self):
        pipeline = _FakePipeline([], [])
        assert gaps.work_area_boundaries_for_ward(pipeline, 1, "Nowhere", "Nowhere", "Nowhere") == []
