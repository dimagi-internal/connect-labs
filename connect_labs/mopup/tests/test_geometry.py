"""Tests for the work-area boundary/centroid pull (Connect's own work_areas
export, not a CommCare case property) — mocked AnalysisPipeline, no
network/DB."""

from __future__ import annotations

import json

import pytest

from connect_labs.mopup.core.geometry import fetch_work_area_geometry


class _FakeRow:
    def __init__(self, entity_id, **computed):
        self.entity_id = entity_id
        self.computed = computed


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakePipeline:
    def __init__(self, rows):
        self._rows = rows

    def stream_analysis_ignore_events(self, config, opportunity_id):
        return _FakeResult(self._rows)


class TestFetchWorkAreaGeometry:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            fetch_work_area_geometry(1)

    def test_parses_json_string_geometry(self):
        rows = [
            _FakeRow(
                "103083",
                wa_case_id="wa-1",
                boundary=json.dumps(
                    {"type": "Polygon", "coordinates": [[[11.18, 9.74], [11.19, 9.74], [11.19, 9.75], [11.18, 9.74]]]}
                ),
                centroid=json.dumps({"type": "Point", "coordinates": [11.182, 9.741]}),
            )
        ]
        pipeline = _FakePipeline(rows)
        geometry = fetch_work_area_geometry(1, pipeline=pipeline)
        assert geometry["wa-1"]["lon"] == pytest.approx(11.182)
        assert geometry["wa-1"]["lat"] == pytest.approx(9.741)
        assert geometry["wa-1"]["boundary"]["type"] == "Polygon"

    def test_accepts_already_parsed_dict_geometry(self):
        rows = [
            _FakeRow(
                "103083",
                wa_case_id="wa-1",
                boundary={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                centroid={"type": "Point", "coordinates": [0.5, 0.5]},
            )
        ]
        pipeline = _FakePipeline(rows)
        geometry = fetch_work_area_geometry(1, pipeline=pipeline)
        assert geometry["wa-1"]["lon"] == 0.5
        assert geometry["wa-1"]["lat"] == 0.5

    def test_missing_geometry_maps_to_none_not_skipped(self):
        rows = [_FakeRow("103083", wa_case_id="wa-1", boundary=None, centroid=None)]
        pipeline = _FakePipeline(rows)
        geometry = fetch_work_area_geometry(1, pipeline=pipeline)
        assert geometry["wa-1"] == {"lat": None, "lon": None, "boundary": None}

    def test_malformed_geometry_does_not_raise(self):
        rows = [_FakeRow("103083", wa_case_id="wa-1", boundary="not json", centroid="also not json")]
        pipeline = _FakePipeline(rows)
        geometry = fetch_work_area_geometry(1, pipeline=pipeline)
        assert geometry["wa-1"] == {"lat": None, "lon": None, "boundary": None}

    def test_rows_with_no_wa_case_id_are_skipped(self):
        rows = [_FakeRow("103083", wa_case_id=None)]
        pipeline = _FakePipeline(rows)
        assert fetch_work_area_geometry(1, pipeline=pipeline) == {}
