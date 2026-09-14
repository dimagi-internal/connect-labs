"""Tests for the approved-visit pull + per-work-area aggregation (Phase 2's
expensive data pull) — mocked AnalysisPipeline, no network/DB."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connect_labs.mopup.core.visits import aggregate_visits_by_wa, build_evaluation_rows, list_approved_visits


class _FakeRow:
    def __init__(self, entity_id, username="", visit_date=None, **computed):
        self.entity_id = entity_id
        self.username = username
        self.visit_date = visit_date
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


class TestListApprovedVisits:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            list_approved_visits(1)

    def test_sets_pipeline_id_for_raw_cache_isolation(self):
        # See the identical test/comment in test_work_areas.py — same
        # pipeline_id=None cache-clobbering bug, same fix.
        pipeline = _FakePipeline([])
        list_approved_visits(1, pipeline=pipeline)
        assert pipeline.last_config.pipeline_id == 12968

    def test_terminal_stage_is_the_real_enum_not_a_string(self):
        # See the identical test/comment in test_geometry.py — same
        # string-vs-enum dispatch bug, same fix.
        from connect_labs.labs.analysis.config import CacheStage

        pipeline = _FakePipeline([])
        list_approved_visits(1, pipeline=pipeline)
        assert pipeline.last_config.terminal_stage == CacheStage.VISIT_LEVEL

    def test_row_with_none_computed_does_not_crash(self):
        # A real case hit against production data (program 217, opportunity
        # 2154): row.computed is None (not {}) when field extraction found
        # nothing to compute for that visit.
        rows = [SimpleNamespace(entity_id="v1", computed=None)]
        pipeline = _FakePipeline(rows)
        assert list_approved_visits(1, pipeline=pipeline) == []

    def test_filters_to_known_form_types_and_extracts_dq_fields(self):
        rows = [
            _FakeRow(
                "v1",
                username="flw-1",
                visit_date="2026-01-05",
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
            "username": "flw-1",
            "visit_date": "2026-01-05",
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
            "flw_username": "",
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

    def test_flw_username_is_the_wards_last_submitter_by_visit_date(self):
        # It shouldn't normally happen that two different FLWs submit to the
        # same work area, but if it does, the most RECENT submitter wins.
        visits = [
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "username": "flw-early",
                "visit_date": "2026-01-01",
            },
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "username": "flw-late",
                "visit_date": "2026-01-10",
            },
        ]
        agg = aggregate_visits_by_wa(visits)
        assert agg["wa-1"]["flw_username"] == "flw-late"

    def test_flw_username_out_of_order_visits_still_pick_the_latest(self):
        # Same as above but the later-dated visit is encountered FIRST in
        # iteration order -- the pick must be by date, not by arrival order.
        visits = [
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "username": "flw-late",
                "visit_date": "2026-01-10",
            },
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "username": "flw-early",
                "visit_date": "2026-01-01",
            },
        ]
        agg = aggregate_visits_by_wa(visits)
        assert agg["wa-1"]["flw_username"] == "flw-late"

    def test_flw_username_first_seen_wins_when_no_visit_dates_available(self):
        visits = [
            {"wa_case_id": "wa-1", "form_name": "Health Service Delivery", "username": "flw-a"},
            {"wa_case_id": "wa-1", "form_name": "Health Service Delivery", "username": "flw-b"},
        ]
        agg = aggregate_visits_by_wa(visits)
        assert agg["wa-1"]["flw_username"] == "flw-a"

    def test_flw_username_visit_without_username_does_not_clear_the_pick(self):
        visits = [
            {
                "wa_case_id": "wa-1",
                "form_name": "Health Service Delivery",
                "username": "flw-a",
                "visit_date": "2026-01-01",
            },
            {"wa_case_id": "wa-1", "form_name": "No Children Found"},  # no username at all
        ]
        agg = aggregate_visits_by_wa(visits)
        assert agg["wa-1"]["flw_username"] == "flw-a"


class TestBuildEvaluationRows:
    def test_merges_case_data_with_visit_aggregates(self):
        work_areas = [
            {
                "case_id": "wa-1",
                "wa_name": "Household 12",
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
                "wa_name": "Household 12",
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
        # No visits at all -> nothing to derive a submitter from, falls
        # back to the case's own owner_id (empty here, same as the fixture).
        assert rows[0]["flw_username"] == ""

    def test_visit_derived_flw_username_takes_priority_over_case_owner_id(self):
        # The actual bug fix: a WA case's own `owner_id` is a raw CommCare
        # HQ user UUID that fetch_flw_names() can never resolve to a name --
        # the last submitting FLW's Connect username (aggregate_visits_by_wa's
        # `flw_username`) is what should end up on the row.
        work_areas = [
            {
                "case_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 10,
                "expected_visit_count": 8,
                "status": "VISITED",
                "owner_id": "3022f9e591b741f28a9b76a0c10692bd",
            }
        ]
        aggregates = {
            "wa-1": {
                "approved_hsd_count": 1,
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 0,
                "deworming_given": 0,
                "muac_given": 0,
                "vaccination_given": 0,
                "flw_username": "real_connect_username",
            }
        }
        rows = build_evaluation_rows(work_areas, aggregates)
        assert rows[0]["flw_username"] == "real_connect_username"
