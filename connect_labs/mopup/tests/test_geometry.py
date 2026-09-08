"""Tests for the work-area boundary/centroid pull (Connect's own work_areas
export, not a CommCare case property) — mocked AnalysisPipeline, no
network/DB."""

from __future__ import annotations

import json
from types import SimpleNamespace

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
        self.last_config = None
        self.last_force_refresh = None

    def stream_analysis_ignore_events(self, config, opportunity_id, force_refresh=False):
        self.last_config = config
        self.last_force_refresh = force_refresh
        return _FakeResult(self._rows)


class TestFetchWorkAreaGeometry:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            fetch_work_area_geometry(1)

    def test_sets_pipeline_id_for_raw_cache_isolation(self):
        # See the identical test/comment in test_work_areas.py — same
        # pipeline_id=None cache-clobbering bug, same fix.
        pipeline = _FakePipeline([])
        fetch_work_area_geometry(1, pipeline=pipeline)
        assert pipeline.last_config.pipeline_id == 12971

    def test_forces_refresh_to_bypass_lenient_headless_cache_check(self):
        # A request=None (headless, e.g. Celery-task) pipeline has an empty
        # labs_context, which makes expected_visits_for always return 0 for
        # it — making the processed-cache validity check accept ANY existing
        # cached row regardless of staleness/completeness. force_refresh=True
        # is this call's only way to guarantee a fresh read.
        pipeline = _FakePipeline([])
        fetch_work_area_geometry(1, pipeline=pipeline)
        assert pipeline.last_force_refresh is True

    def test_row_with_none_computed_does_not_crash(self):
        # A real case hit against production data (program 217, opportunity
        # 2154): row.computed is None (not {}) when field extraction found
        # nothing to compute for that row.
        rows = [SimpleNamespace(entity_id="103083", computed=None)]
        pipeline = _FakePipeline(rows)
        assert fetch_work_area_geometry(1, pipeline=pipeline) == {}

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
