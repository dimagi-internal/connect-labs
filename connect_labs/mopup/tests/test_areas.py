"""Tests for the mop-up/microplans seam (pure geometry + mocked analysis
pipeline calls — no network/DB, mirrors microplans/tests/test_coverage.py's
fetch-mocking style)."""

from __future__ import annotations

import pytest
from shapely.geometry import shape

from connect_labs.mopup.core import areas
from connect_labs.mopup.core.areas import carry_forward_features, distinct_wards, ward_children_per_building

# ---------------------------------------------------------------------------
# carry_forward_features / distinct_wards
# ---------------------------------------------------------------------------


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _candidate(wa_id, ward, lga, state, boundary, **overrides):
    base = {
        "wa_id": wa_id,
        "ward": ward,
        "lga": lga,
        "state": state,
        "boundary": boundary,
        "building_count": 5,
        "expected_visit_count": 5,
    }
    base.update(overrides)
    return base


class TestCarryForwardFeatures:
    def test_single_candidate_passthrough(self):
        c = _candidate("wa-1", "Sabon Gari", "Rano", "Kano", _square(0, 0, 1, 1))
        features = carry_forward_features([c])
        assert len(features) == 1
        f = features[0]
        assert f["type"] == "Feature"
        assert shape(f["geometry"]).equals(shape(c["boundary"]))
        assert f["properties"]["ward"] == "Sabon Gari"
        assert f["properties"]["lga"] == "Rano"
        assert f["properties"]["state"] == "Kano"
        assert f["properties"]["area_id"] == "mopup-kano-rano-sabon-gari"
        assert f["properties"]["building_count"] == 5
        assert f["properties"]["expected_visit_count"] == 5

    def test_same_ward_candidates_stay_distinct_not_unioned(self):
        # Two adjacent WAs in the same ward -> TWO features, each its own
        # shape, sharing one area_id (not unioned into one blob — this is
        # the carry-forward redesign's whole point).
        c1 = _candidate("wa-1", "Sabon Gari", "Rano", "Kano", _square(0, 0, 1, 1))
        c2 = _candidate("wa-2", "Sabon Gari", "Rano", "Kano", _square(1, 0, 2, 1))
        features = carry_forward_features([c1, c2])
        assert len(features) == 2
        shapes = [shape(f["geometry"]) for f in features]
        assert shapes[0].area == pytest.approx(1.0)
        assert shapes[1].area == pytest.approx(1.0)
        assert not shapes[0].equals(shapes[1])
        assert {f["properties"]["area_id"] for f in features} == {"mopup-kano-rano-sabon-gari"}
        # cluster names must not collide between candidates in the same ward.
        assert len({f["properties"]["cluster"] for f in features}) == 2

    def test_distinct_wards_stay_separate(self):
        c1 = _candidate("wa-1", "Sabon Gari", "Rano", "Kano", _square(0, 0, 1, 1))
        c2 = _candidate("wa-2", "Unguwar Arewa", "Rano", "Kano", _square(5, 5, 6, 6))
        features = carry_forward_features([c1, c2])
        assert {f["properties"]["ward"] for f in features} == {"Sabon Gari", "Unguwar Arewa"}
        assert {f["properties"]["area_id"] for f in features} == {
            "mopup-kano-rano-sabon-gari",
            "mopup-kano-rano-unguwar-arewa",
        }

    def test_same_ward_name_different_lga_not_conflated(self):
        """Two same-named wards in different LGAs must get distinct area_ids —
        a real prior bug class in this codebase (see
        microplans/core/frame.py:_area_meta and core/ward_codes.py's module
        docstring for the "Doka"/"Doka Dawa" incident this scenario is modeled
        on)."""
        c1 = _candidate("wa-1", "Sabon Gari", "Rano", "Kano", _square(0, 0, 1, 1))
        c2 = _candidate("wa-2", "Sabon Gari", "Fagge", "Kano", _square(5, 5, 6, 6))
        features = carry_forward_features([c1, c2])
        assert len({f["properties"]["area_id"] for f in features}) == 2

    def test_missing_ward_raises(self):
        with pytest.raises(ValueError, match="ward"):
            carry_forward_features([_candidate("wa-1", "", "Rano", "Kano", _square(0, 0, 1, 1))])

    def test_malformed_geometry_raises(self):
        with pytest.raises(ValueError):
            carry_forward_features([_candidate("wa-1", "Sabon Gari", "Rano", "Kano", {"type": "Nonsense"})])

    def test_empty_input_returns_empty(self):
        assert carry_forward_features([]) == []

    def test_building_count_never_zero(self):
        # _make_work_area/_coverage_properties assume a >=1 building_count;
        # a candidate with 0 (or missing) building_count must not propagate
        # a zero straight through.
        c = _candidate("wa-1", "Sabon Gari", "Rano", "Kano", _square(0, 0, 1, 1), building_count=0)
        features = carry_forward_features([c])
        assert features[0]["properties"]["building_count"] == 1


class TestDistinctWards:
    def test_one_row_per_distinct_area_id(self):
        c1 = _candidate("wa-1", "Sabon Gari", "Rano", "Kano", _square(0, 0, 1, 1))
        c2 = _candidate("wa-2", "Sabon Gari", "Rano", "Kano", _square(1, 0, 2, 1))
        c3 = _candidate("wa-3", "Unguwar Arewa", "Rano", "Kano", _square(5, 5, 6, 6))
        wards = distinct_wards(carry_forward_features([c1, c2, c3]))
        assert len(wards) == 2
        assert {w["ward"] for w in wards} == {"Sabon Gari", "Unguwar Arewa"}

    def test_empty_input_returns_empty(self):
        assert distinct_wards([]) == []


# ---------------------------------------------------------------------------
# ward_children_per_building
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, entity_id, **computed):
        self.entity_id = entity_id
        self.computed = computed


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakePipeline:
    """Stands in for AnalysisPipeline: returns canned per-opportunity results
    keyed by data_source type, so the two internal queries (work-area lookup,
    HSD visit lookup) can be scripted independently per test."""

    def __init__(self, work_area_rows_by_opp, visit_rows_by_opp):
        self._wa = work_area_rows_by_opp
        self._visits = visit_rows_by_opp
        self.last_configs = []

    def stream_analysis_ignore_events(self, config, opportunity_id):
        self.last_configs.append(config)
        if config.data_source.type == "cchq_cases":
            return _FakeResult(self._wa.get(opportunity_id, []))
        return _FakeResult(self._visits.get(opportunity_id, []))


class TestWardChildrenPerBuilding:
    def test_requires_request_or_pipeline(self):
        with pytest.raises(ValueError, match="request.*pipeline"):
            ward_children_per_building("Sabon Gari", "Rano", "Kano", [1])

    def test_counts_distinct_children_at_matching_wards_only(self, monkeypatch):
        # Two work areas in the target ward (wa-1, wa-2), one in a different ward (wa-3).
        wa_rows = [
            _FakeRow("wa-1", ward="Sabon Gari", lga="Rano", state="Kano"),
            _FakeRow("wa-2", ward="Sabon Gari", lga="Rano", state="Kano"),
            _FakeRow("wa-3", ward="Other Ward", lga="Rano", state="Kano"),
        ]
        visit_rows = [
            # Same child visited twice at wa-1 (HSD) -> counts once.
            _FakeRow("v1", form_name="Health Service Delivery", wa_case_id="wa-1", child_case_id="child-A"),
            _FakeRow("v2", form_name="Health Service Delivery", wa_case_id="wa-1", child_case_id="child-A"),
            # A different child at wa-2 (HSD) -> counts.
            _FakeRow("v3", form_name="Health Service Delivery", wa_case_id="wa-2", child_case_id="child-B"),
            # NCF form at wa-1 -> excluded (not HSD).
            _FakeRow("v4", form_name="No Children Found", wa_case_id="wa-1", child_case_id="child-C"),
            # HSD visit at wa-3, which is NOT in the target ward -> excluded.
            _FakeRow("v5", form_name="Health Service Delivery", wa_case_id="wa-3", child_case_id="child-D"),
        ]
        pipeline = _FakePipeline({1: wa_rows}, {1: visit_rows})

        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward, candidates=None: {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        monkeypatch.setattr(areas, "fetch_buildings", lambda area: [object()] * 10)

        rate = ward_children_per_building("Sabon Gari", "Rano", "Kano", [1], pipeline=pipeline)
        # 2 distinct children (A, B) / 10 buildings
        assert rate == pytest.approx(0.2)

        # Same pipeline_id=None cache-clobbering bug documented in
        # core/work_areas.py/core/visits.py/core/geometry.py — the two
        # internal queries here (work-area lookup, HSD visit lookup) need
        # their own isolated raw-cache slots too.
        wa_config = next(c for c in pipeline.last_configs if c.data_source.type == "cchq_cases")
        visit_config = next(c for c in pipeline.last_configs if c.data_source.type == "connect_csv")
        assert wa_config.pipeline_id == 12965
        assert visit_config.pipeline_id == 12968

    def test_no_matching_work_areas_contributes_zero_children(self, monkeypatch):
        pipeline = _FakePipeline({1: []}, {1: []})
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward, candidates=None: {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        monkeypatch.setattr(areas, "fetch_buildings", lambda area: [object()] * 5)
        rate = ward_children_per_building("Nowhere", "Nowhere", "Nowhere", [1], pipeline=pipeline)
        assert rate == 0.0

    def test_no_boundary_match_returns_zero(self, monkeypatch):
        pipeline = _FakePipeline({}, {})
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward, candidates=None: None,
        )
        rate = ward_children_per_building("Sabon Gari", "Rano", "Kano", [1], pipeline=pipeline)
        assert rate == 0.0

    def test_zero_buildings_returns_zero_not_zerodivision(self, monkeypatch):
        wa_rows = [_FakeRow("wa-1", ward="Sabon Gari", lga="Rano", state="Kano")]
        visit_rows = [
            _FakeRow("v1", form_name="Health Service Delivery", wa_case_id="wa-1", child_case_id="child-A"),
        ]
        pipeline = _FakePipeline({1: wa_rows}, {1: visit_rows})
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward, candidates=None: {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        monkeypatch.setattr(areas, "fetch_buildings", lambda area: [])
        rate = ward_children_per_building("Sabon Gari", "Rano", "Kano", [1], pipeline=pipeline)
        assert rate == 0.0

    def test_sums_across_multiple_opportunities(self, monkeypatch):
        wa_rows_by_opp = {
            1: [_FakeRow("wa-1", ward="Sabon Gari", lga="Rano", state="Kano")],
            2: [_FakeRow("wa-9", ward="Sabon Gari", lga="Rano", state="Kano")],
        }
        visit_rows_by_opp = {
            1: [_FakeRow("v1", form_name="Health Service Delivery", wa_case_id="wa-1", child_case_id="child-A")],
            2: [_FakeRow("v2", form_name="Health Service Delivery", wa_case_id="wa-9", child_case_id="child-B")],
        }
        pipeline = _FakePipeline(wa_rows_by_opp, visit_rows_by_opp)
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward, candidates=None: {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        monkeypatch.setattr(areas, "fetch_buildings", lambda area: [object()] * 4)
        rate = ward_children_per_building("Sabon Gari", "Rano", "Kano", [1, 2], pipeline=pipeline)
        # 2 distinct children total (one per opp) / 4 buildings
        assert rate == pytest.approx(0.5)
