"""Tests for the cheap per-opportunity work-area case pull (Phase 1's ward
picker source) — mocked AnalysisPipeline, no network/DB."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connect_labs.mopup.core.work_areas import list_work_areas, summarize_wards


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


class TestListWorkAreas:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            list_work_areas(1)

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
