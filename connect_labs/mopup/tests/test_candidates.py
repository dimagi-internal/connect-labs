"""Tests for the Phase 2 evaluation-input composition + ward summary rollup."""

from __future__ import annotations

from connect_labs.mopup.core.candidates import (
    build_evaluation_input,
    build_map_features,
    gap_feature_to_candidate_row,
    gap_summary_by_ward,
    summarize_candidates_by_ward,
)


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
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 10,
                "expected_visit_count": 5,
                "approved_hsd_count": 4,
                "approved_ncf_count": 1,
                "approved_inaccessible_count": 0,
            },
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 20,
                "expected_visit_count": 8,
                "approved_hsd_count": 6,
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 1,
            },
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 30,
                "expected_visit_count": 12,
                "approved_hsd_count": 9,
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 0,
            },
        ]
        candidates = [
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "severity_count": 1,
                "building_count": 10,
                "expected_visit_count": 5,
            },
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "severity_count": 3,
                "building_count": 20,
                "expected_visit_count": 8,
            },
        ]
        summary = summarize_candidates_by_ward(candidates, all_rows)
        assert summary == [
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "total_work_areas": 3,
                "total_hsd": 19,
                "total_ncf": 2,
                "total_buildings": 60,
                "total_evc": 25,
                "candidate_count": 2,
                "candidate_buildings": 30,
                "candidate_evc": 13,
                "flagged_by_2_plus": 1,
            }
        ]

    def test_no_candidates_returns_empty(self):
        assert summarize_candidates_by_ward([], []) == []

    def test_wards_with_zero_candidates_still_appear(self):
        # Real bug, caught live against program 217/opportunity 2154: with
        # thousands of work areas evaluated but zero flagged under the
        # current thresholds, this table was rendering completely empty —
        # it must show every evaluated ward, not just ones with candidates.
        all_rows = [
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"},
            {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"},
            {"ward": "Unguwar Arewa", "lga": "Rano", "state": "Kano"},
        ]
        summary = summarize_candidates_by_ward([], all_rows)
        assert summary == [
            {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "total_work_areas": 2,
                "total_hsd": 0,
                "total_ncf": 0,
                "total_buildings": 0,
                "total_evc": 0,
                "candidate_count": 0,
                "candidate_buildings": 0,
                "candidate_evc": 0,
                "flagged_by_2_plus": 0,
            },
            {
                "ward": "Unguwar Arewa",
                "lga": "Rano",
                "state": "Kano",
                "total_work_areas": 1,
                "total_hsd": 0,
                "total_ncf": 0,
                "total_buildings": 0,
                "total_evc": 0,
                "candidate_count": 0,
                "candidate_buildings": 0,
                "candidate_evc": 0,
                "flagged_by_2_plus": 0,
            },
        ]


class TestGapSummaryByWard:
    def test_rolls_up_gap_features_per_ward(self):
        features = [
            {
                "properties": {
                    "ward": "Sabon Gari",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 3,
                    "expected_visit_count": 5,
                }
            },
            {
                "properties": {
                    "ward": "Sabon Gari",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 2,
                    "expected_visit_count": 4,
                }
            },
            {
                "properties": {
                    "ward": "Unguwar Arewa",
                    "lga": "Rano",
                    "state": "Kano",
                    "building_count": 1,
                    "expected_visit_count": 2,
                }
            },
        ]
        summary = gap_summary_by_ward(features)
        assert summary == {
            "Kano|Rano|Sabon Gari": {
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "gap_wa_count": 2,
                "gap_buildings": 5,
                "gap_evc": 9,
            },
            "Kano|Rano|Unguwar Arewa": {
                "ward": "Unguwar Arewa",
                "lga": "Rano",
                "state": "Kano",
                "gap_wa_count": 1,
                "gap_buildings": 1,
                "gap_evc": 2,
            },
        }

    def test_no_features_returns_empty(self):
        assert gap_summary_by_ward([]) == {}


class TestBuildMapFeatures:
    _BOUNDARY = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}

    def test_skips_work_areas_without_boundary(self):
        all_rows = [{"wa_id": "wa-1", "ward": "Sabon Gari", "boundary": None}]
        fc = build_map_features(all_rows, [])
        assert fc["features"] == []

    def test_non_candidate_marked_not_included_with_no_indicator(self):
        all_rows = [{"wa_id": "wa-1", "ward": "Sabon Gari", "boundary": self._BOUNDARY}]
        fc = build_map_features(all_rows, [])
        assert len(fc["features"]) == 1
        props = fc["features"][0]["properties"]
        assert props == {
            "wa_id": "wa-1",
            "ward": "Sabon Gari",
            "included": False,
            "first_indicator": None,
            "source": "existing_wa",
        }

    def test_gap_features_appended_with_their_own_source(self):
        gap_feature = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]},
            "properties": {"cluster": "mopup-x-gap-C0", "ward": "Sabon Gari", "building_count": 3},
        }
        fc = build_map_features([], [], gap_features=[gap_feature])
        assert len(fc["features"]) == 1
        props = fc["features"][0]["properties"]
        assert props["source"] == "planning_gap"
        assert props["included"] is True
        assert props["wa_id"] == "mopup-x-gap-C0"
        assert fc["features"][0]["geometry"] == gap_feature["geometry"]

    def test_candidate_marked_included_with_first_triggered_indicator(self):
        all_rows = [{"wa_id": "wa-1", "ward": "Sabon Gari", "boundary": self._BOUNDARY}]
        candidates = [{"wa_id": "wa-1", "triggered_indicators": ["deworming", "muac"]}]
        fc = build_map_features(all_rows, candidates)
        props = fc["features"][0]["properties"]
        assert props["included"] is True
        assert props["first_indicator"] == "deworming"

    def test_feature_geometry_matches_boundary(self):
        all_rows = [{"wa_id": "wa-1", "ward": "Sabon Gari", "boundary": self._BOUNDARY}]
        fc = build_map_features(all_rows, [])
        assert fc["features"][0]["geometry"] == self._BOUNDARY


class TestGapFeatureToCandidateRow:
    def test_adapts_a_gap_feature_into_candidate_shape(self):
        boundary = {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]}
        feature = {
            "type": "Feature",
            "geometry": boundary,
            "properties": {
                "cluster": "mopup-kano-rano-sabon-gari-gap-C0",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 3,
                "expected_visit_count": 7,
            },
        }
        row = gap_feature_to_candidate_row(feature)
        assert row == {
            "wa_id": "mopup-kano-rano-sabon-gari-gap-C0",
            "ward": "Sabon Gari",
            "lga": "Rano",
            "state": "Kano",
            "flw_username": "",
            "boundary": boundary,
            "building_count": 3,
            "expected_visit_count": 7,
            "source": "planning_gap",
            "triggered_indicators": ["planning_gap"],
            "severity_count": 0,
            "detail": {},
        }
