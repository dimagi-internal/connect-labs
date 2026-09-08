"""Tests for planning-gap detection (design brief §7) — mocked
fetch_buildings/work-area lookups (no network/DB), real
microplans.core.clustering.grid_clusters (pure computation)."""

from __future__ import annotations

import pandas as pd
import pytest

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


class TestPlanningGapFeatures:
    def test_grids_remainder_into_tagged_features(self, monkeypatch):
        # Two buildings far enough apart to land in different 100m cells.
        buildings = _buildings_df([(0.0, 0.0), (0.005, 0.005)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        features = gaps.planning_gap_features("Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), [])
        assert len(features) >= 1
        for f in features:
            assert f["type"] == "Feature"
            assert f["properties"]["area_id"] == "mopup-kano-rano-sabon-gari"
            assert f["properties"]["ward"] == "Sabon Gari"
            assert f["properties"]["cluster"].startswith("mopup-kano-rano-sabon-gari-gap-")
            assert f["properties"]["building_count"] >= 1

    def test_existing_wa_boundaries_reduce_the_gridded_remainder(self, monkeypatch):
        buildings = _buildings_df([(0.001, 0.001), (0.008, 0.008)])
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: buildings)
        # Excluding the WA covering the first building leaves only the second.
        existing = [_square(0.0, 0.0, 0.002, 0.002)]
        features = gaps.planning_gap_features(
            "Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), existing
        )
        total_buildings = sum(f["properties"]["building_count"] for f in features)
        assert total_buildings == 1

    def test_no_remainder_returns_empty(self, monkeypatch):
        monkeypatch.setattr(gaps, "fetch_buildings", lambda area, **kw: _buildings_df([]))
        features = gaps.planning_gap_features("Sabon Gari", "Rano", "Kano", "mopup-kano-rano-sabon-gari", object(), [])
        assert features == []


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
