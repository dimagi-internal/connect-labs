"""Tests for the cheap per-opportunity work-area case pull (Phase 1's ward
picker source) — mocked AnalysisPipeline, no network/DB."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connect_labs.mopup.core.work_areas import fetch_connect_implementation_areas, list_work_areas, summarize_wards


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

    def stream_analysis_ignore_events(self, config, opportunity_id):
        self.last_config = config
        return _FakeResult(self._rows)


class TestListWorkAreas:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            list_work_areas(1)

    def test_sets_pipeline_id_for_raw_cache_isolation(self):
        # Real production bug, found live this session: leaving pipeline_id
        # unset makes every ad-hoc config in this app (this one,
        # list_approved_visits, fetch_work_area_geometry) share ONE raw-visit
        # -cache slot per opportunity, so each one's wholesale DELETE+INSERT
        # clobbers whatever the others just wrote — exactly the
        # AnalysisPipelineConfig.pipeline_id docstring's documented "issue
        # #116" pattern. Confirmed live: every work area's case id (wa_id)
        # came back null once a second ad-hoc fetch had run in between.
        pipeline = _FakePipeline([])
        list_work_areas(1, pipeline=pipeline)
        assert pipeline.last_config.pipeline_id == 12965

    def test_terminal_stage_is_the_real_enum_not_a_string(self):
        # See the identical test/comment in test_geometry.py — same
        # string-vs-enum dispatch bug, same fix.
        from connect_labs.labs.analysis.config import CacheStage

        pipeline = _FakePipeline([])
        list_work_areas(1, pipeline=pipeline)
        assert pipeline.last_config.terminal_stage == CacheStage.VISIT_LEVEL

    def test_row_with_none_computed_does_not_crash(self):
        # A real case hit against production data (program 217, opportunity
        # 2154): row.computed is None (not {}) when field extraction found
        # nothing to compute for that case.
        rows = [SimpleNamespace(entity_id="wa-1", computed=None)]
        pipeline = _FakePipeline(rows)
        result = list_work_areas(1, pipeline=pipeline)
        assert result == [
            {
                "case_id": "wa-1",
                "ward": "",
                "lga": "",
                "state": "",
                "building_count": 0,
                "expected_visit_count": 0,
                "status": "",
                "owner_id": "",
            }
        ]

    def test_projects_and_coerces_fields(self):
        rows = [
            _FakeRow(
                "wa-1",
                ward="Sabon Gari",
                lga="Rano",
                state="Kano",
                building_count="42",
                expected_visit_count="10",
                status="VISITED",
                owner_id="flw-1",
            ),
            _FakeRow(
                "wa-2",
                ward="Sabon Gari",
                lga="Rano",
                state="Kano",
                building_count=None,
                expected_visit_count=None,
                status="NOT_VISITED",
                owner_id=None,
            ),
        ]
        pipeline = _FakePipeline(rows)
        result = list_work_areas(1, pipeline=pipeline)
        assert result == [
            {
                "case_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 42,
                "expected_visit_count": 10,
                "status": "VISITED",
                "owner_id": "flw-1",
            },
            {
                "case_id": "wa-2",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 0,
                "expected_visit_count": 0,
                "status": "NOT_VISITED",
                "owner_id": "",
            },
        ]


class TestSummarizeWards:
    def test_rolls_up_by_state_lga_ward(self):
        work_areas = [
            {
                "case_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 10,
                "expected_visit_count": 5,
                "status": "VISITED",
            },
            {
                "case_id": "wa-2",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 8,
                "expected_visit_count": 4,
                "status": "NOT_VISITED",
            },
            {
                "case_id": "wa-3",
                "ward": "Unguwar Arewa",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 3,
                "expected_visit_count": 2,
                "status": "VISITED",
            },
        ]
        wards = summarize_wards(work_areas)
        assert wards == [
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "work_area_count": 2,
                "building_count": 18,
                "expected_visit_count": 9,
            },
            {
                "ward": "Unguwar Arewa",
                "lga": "Rano",
                "state": "Kano",
                "work_area_count": 1,
                "building_count": 3,
                "expected_visit_count": 2,
            },
        ]

    def test_skips_work_areas_with_no_ward(self):
        work_areas = [{"ward": "", "lga": "Rano", "state": "Kano", "building_count": 1, "expected_visit_count": 1}]
        assert summarize_wards(work_areas) == []

    def test_empty_input_returns_empty(self):
        assert summarize_wards([]) == []


class TestFetchConnectImplementationAreas:
    """The read side of commcare-connect#1517 (2026-09-10) — Connect's own
    Implementation Area boundaries, previously write-only from labs."""

    def test_returns_records_from_the_export_client(self, monkeypatch):
        records = [{"id": 1, "name": "Sabon Gari", "centroid": {}, "boundary": {}}]

        class _FakeClient:
            def fetch_all(self, endpoint):
                return records

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        monkeypatch.setattr(
            "connect_labs.labs.integrations.connect.factory.get_export_client",
            lambda **kwargs: _FakeClient(),
        )
        result = fetch_connect_implementation_areas(2154, "tok")
        assert result == records

    def test_export_api_error_returns_empty_list_not_raises(self, monkeypatch):
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError

        def _raise(**kwargs):
            raise ExportAPIError("boom")

        monkeypatch.setattr("connect_labs.labs.integrations.connect.factory.get_export_client", _raise)
        assert fetch_connect_implementation_areas(2154, "tok") == []
