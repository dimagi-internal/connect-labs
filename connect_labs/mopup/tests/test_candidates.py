"""Tests for the Phase 2 evaluation-input composition + ward summary rollup."""

from __future__ import annotations

from connect_labs.mopup.core.candidates import build_evaluation_input, summarize_candidates_by_ward


class TestBuildEvaluationInput:
    def test_scopes_to_selected_wards(self, monkeypatch):
        import connect_labs.mopup.core.candidates as candidates_module

        monkeypatch.setattr(
            candidates_module,
            "list_work_areas",
            lambda opportunity_id, request=None, pipeline=None: [
                {
                    "case_id": "wa-1",
                    "ward": "Sabon Gari",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 10,
                    "expected_visit_count": 8,
                    "status": "VISITED",
                    "owner_id": "flw-1",
                },
                {
                    "case_id": "wa-2",
                    "ward": "Other Ward",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 5,
                    "expected_visit_count": 4,
                    "status": "VISITED",
                    "owner_id": "flw-2",
                },
            ],
        )
        monkeypatch.setattr(
            candidates_module,
            "list_approved_visits",
            lambda opportunity_id, request=None, pipeline=None: [
                {
                    "wa_case_id": "wa-1",
                    "form_name": "Health Service Delivery",
                    "deworming_given": True,
                    "muac_recorded": True,
                    "vaccination_given": True,
                },
                {
                    "wa_case_id": "wa-2",
                    "form_name": "Health Service Delivery",
                    "deworming_given": True,
                    "muac_recorded": True,
                    "vaccination_given": True,
                },
            ],
        )
        monkeypatch.setattr(
            candidates_module, "fetch_work_area_geometry", lambda opportunity_id, request=None, pipeline=None: {}
        )
        rows = build_evaluation_input(1, [{"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"}], request=object())
        assert len(rows) == 1
        assert rows[0]["wa_id"] == "wa-1"
        assert rows[0]["approved_hsd_count"] == 1  # wa-2's visit correctly excluded from aggregation
        assert rows[0]["lat"] is None  # no geometry match -> None, not a crash

    def test_merges_geometry_by_wa_id(self, monkeypatch):
        import connect_labs.mopup.core.candidates as candidates_module

        monkeypatch.setattr(
            candidates_module,
            "list_work_areas",
            lambda opportunity_id, request=None, pipeline=None: [
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
            ],
        )
        monkeypatch.setattr(candidates_module, "list_approved_visits", lambda *a, **k: [])
        monkeypatch.setattr(
            candidates_module,
            "fetch_work_area_geometry",
            lambda opportunity_id, request=None, pipeline=None: {
                "wa-1": {"lat": 9.74, "lon": 11.18, "boundary": {"type": "Polygon", "coordinates": []}}
            },
        )
        rows = build_evaluation_input(1, [], request=object())
        assert rows[0]["lat"] == 9.74
        assert rows[0]["lon"] == 11.18
        assert rows[0]["boundary"]["type"] == "Polygon"

    def test_empty_selection_means_every_ward(self, monkeypatch):
        import connect_labs.mopup.core.candidates as candidates_module

        monkeypatch.setattr(
            candidates_module,
            "list_work_areas",
            lambda opportunity_id, request=None, pipeline=None: [
                {
                    "case_id": "wa-1",
                    "ward": "Sabon Gari",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 10,
                    "expected_visit_count": 8,
                    "status": "VISITED",
                    "owner_id": "flw-1",
                },
                {
                    "case_id": "wa-2",
                    "ward": "Other Ward",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 5,
                    "expected_visit_count": 4,
                    "status": "VISITED",
                    "owner_id": "flw-2",
                },
            ],
        )
        monkeypatch.setattr(candidates_module, "list_approved_visits", lambda *a, **k: [])
        monkeypatch.setattr(
            candidates_module, "fetch_work_area_geometry", lambda opportunity_id, request=None, pipeline=None: {}
        )
        rows = build_evaluation_input(1, [], request=object())
        assert len(rows) == 2


class TestSummarizeCandidatesByWard:
    def test_rolls_up_totals_and_severity(self):
        all_rows = [
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"},
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"},
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"},
        ]
        candidates = [
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "severity_count": 1},
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "severity_count": 3},
        ]
        summary = summarize_candidates_by_ward(candidates, all_rows)
        assert summary == [
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "total_work_areas": 3,
                "candidate_count": 2,
                "flagged_by_2_plus": 1,
            }
        ]

    def test_no_candidates_returns_empty(self):
        assert summarize_candidates_by_ward([], []) == []
