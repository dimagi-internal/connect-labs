"""Tests for the approved-visit pull + per-work-area aggregation (Phase 2's
expensive data pull) — mocked AnalysisPipeline, no network/DB."""

from __future__ import annotations

import pytest

from connect_labs.mopup.core.visits import aggregate_visits_by_wa, build_evaluation_rows, list_approved_visits


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


class TestListApprovedVisits:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            list_approved_visits(1)

    def test_filters_to_known_form_types_and_extracts_dq_fields(self):
        rows = [
            _FakeRow(
                "v1",
                form_name="Health Service Delivery",
                wa_case_id="wa-1",
                deworming="DW Delivered",
                muac="12.3",
                vaccination="yes",
            ),
            _FakeRow("v2", form_name="No Children Found", wa_case_id="wa-2"),
            _FakeRow("v3", form_name="Inaccessible WA", wa_case_id="wa-3"),
            _FakeRow("v4", form_name="Some Other Form", wa_case_id="wa-4"),  # excluded
        ]
        pipeline = _FakePipeline(rows)
        visits = list_approved_visits(1, pipeline=pipeline)
        assert len(visits) == 3
        hsd = visits[0]
        assert hsd == {
            "wa_case_id": "wa-1",
            "form_name": "Health Service Delivery",
            "deworming_given": True,
            "muac_recorded": True,
            "vaccination_given": True,
        }

    def test_missing_dq_fields_are_falsy_not_crashed(self):
        rows = [_FakeRow("v1", form_name="Health Service Delivery", wa_case_id="wa-1")]
        pipeline = _FakePipeline(rows)
        visits = list_approved_visits(1, pipeline=pipeline)
        assert visits[0]["deworming_given"] is False
        assert visits[0]["muac_recorded"] is False
        assert visits[0]["vaccination_given"] is False


class TestAggregateVisitsByWa:
    def test_counts_by_form_type_and_dq_fields(self):
        visits = [
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "deworming_given": True,
                "muac_recorded": True,
                "vaccination_given": False,
            },
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "deworming_given": False,
                "muac_recorded": True,
                "vaccination_given": True,
            },
            {"wa_case_id": "wa-1", "form_name": "No Children Found"},
            {"wa_case_id": "wa-1", "form_name": "Inaccessible WA"},
            {"wa_case_id": "wa-2", "form_name": "Health Service Delivery", "deworming_given": True},
        ]
        agg = aggregate_visits_by_wa(visits)
        assert agg["wa-1"] == {
            "approved_hsd_count": 2,
            "approved_ncf_count": 1,
            "approved_inaccessible_count": 1,
            "deworming_given": 1,
            "muac_given": 2,
            "vaccination_given": 1,
        }
        assert agg["wa-2"]["approved_hsd_count"] == 1

    def test_restricts_to_given_wa_ids(self):
        visits = [
            {"wa_case_id": "wa-1", "form_name": "Health Service Delivery"},
            {"wa_case_id": "wa-2", "form_name": "Health Service Delivery"},
        ]
        agg = aggregate_visits_by_wa(visits, wa_ids={"wa-1"})
        assert set(agg.keys()) == {"wa-1"}

    def test_visits_with_no_wa_case_id_are_skipped(self):
        visits = [{"wa_case_id": None, "form_name": "Health Service Delivery"}]
        assert aggregate_visits_by_wa(visits) == {}

    def test_empty_input_returns_empty(self):
        assert aggregate_visits_by_wa([]) == {}


class TestBuildEvaluationRows:
    def test_merges_case_data_with_visit_aggregates(self):
        work_areas = [
            {
                "case_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 10,
                "expected_visit_count": 8,
                "status": "VISITED",
                "owner_id": "flw-1",
            }
        ]
        aggregates = {
            "wa-1": {
                "approved_hsd_count": 5,
                "approved_ncf_count": 1,
                "approved_inaccessible_count": 0,
                "deworming_given": 4,
                "muac_given": 5,
                "vaccination_given": 3,
            }
        }
        rows = build_evaluation_rows(work_areas, aggregates)
        assert rows == [
            {
                "wa_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "flw_username": "flw-1",
                "lat": None,
                "lon": None,
                "status": "VISITED",
                "building_count": 10,
                "expected_visit_count": 8,
                "approved_hsd_count": 5,
                "approved_ncf_count": 1,
                "approved_inaccessible_count": 0,
                "deworming_given": 4,
                "muac_given": 5,
                "vaccination_given": 3,
            }
        ]

    def test_work_area_with_no_visits_gets_zeroed_aggregate(self):
        work_areas = [
            {
                "case_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 10,
                "expected_visit_count": 8,
                "status": "NOT_VISITED",
                "owner_id": "",
            }
        ]
        rows = build_evaluation_rows(work_areas, {})
        assert rows[0]["approved_hsd_count"] == 0
        assert rows[0]["deworming_given"] == 0
