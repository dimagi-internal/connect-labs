"""Tests for the CHC mop-up microplanning seam (pure geometry + mocked analysis
pipeline calls — no network/DB, mirrors test_coverage.py's fetch-mocking style)."""

from __future__ import annotations

import pytest
from shapely.geometry import shape

from connect_labs.microplans.core import mopup
from connect_labs.microplans.core.mopup import build_mopup_areas, ward_children_per_building

# ---------------------------------------------------------------------------
# build_mopup_areas
# ---------------------------------------------------------------------------


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


class TestBuildMopupAreas:
    def test_single_ward_single_wa_passthrough(self):
        wa = {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "geometry": _square(0, 0, 1, 1)}
        areas = build_mopup_areas([wa])
        assert len(areas) == 1
        area = areas[0]
        assert area["ward"] == "Sabon Gari"
        assert area["lga"] == "Rano"
        assert area["state"] == "Kano"
        assert area["area_id"] == "mopup-kano-rano-sabon-gari"
        assert shape(area["geometry"]).equals(shape(wa["geometry"]))

    def test_groups_by_ward_and_unions_geometry(self):
        # Two adjacent WAs in the same ward -> one unioned area covering both.
        wa1 = {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "geometry": _square(0, 0, 1, 1)}
        wa2 = {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "geometry": _square(1, 0, 2, 1)}
        areas = build_mopup_areas([wa1, wa2])
        assert len(areas) == 1
        union = shape(areas[0]["geometry"])
        assert union.area == pytest.approx(2.0)
        assert union.contains(shape({"type": "Point", "coordinates": [0.5, 0.5]}))
        assert union.contains(shape({"type": "Point", "coordinates": [1.5, 0.5]}))

    def test_distinct_wards_stay_separate(self):
        wa1 = {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "geometry": _square(0, 0, 1, 1)}
        wa2 = {"ward": "Unguwar Arewa", "lga": "Rano", "state": "Kano", "geometry": _square(5, 5, 6, 6)}
        areas = build_mopup_areas([wa1, wa2])
        assert {a["ward"] for a in areas} == {"Sabon Gari", "Unguwar Arewa"}
        assert {a["area_id"] for a in areas} == {"mopup-kano-rano-sabon-gari", "mopup-kano-rano-unguwar-arewa"}

    def test_same_ward_name_different_lga_not_conflated(self):
        """Two same-named wards in different LGAs must get distinct area_ids — a
        real prior bug class in this codebase (see core/frame.py:_area_meta and
        core/ward_codes.py's module docstring for the "Doka"/"Doka Dawa" incident
        this exact scenario is modeled on)."""
        wa1 = {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "geometry": _square(0, 0, 1, 1)}
        wa2 = {"ward": "Sabon Gari", "lga": "Fagge", "state": "Kano", "geometry": _square(5, 5, 6, 6)}
        areas = build_mopup_areas([wa1, wa2])
        assert len(areas) == 2
        assert len({a["area_id"] for a in areas}) == 2

    def test_missing_ward_raises(self):
        with pytest.raises(ValueError, match="ward"):
            build_mopup_areas([{"lga": "Rano", "state": "Kano", "geometry": _square(0, 0, 1, 1)}])

    def test_malformed_geometry_raises(self):
        with pytest.raises(ValueError):
            build_mopup_areas(
                [{"ward": "Sabon Gari", "lga": "Rano", "state": "Kano", "geometry": {"type": "Nonsense"}}]
            )

    def test_empty_input_returns_empty(self):
        assert build_mopup_areas([]) == []


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

    def stream_analysis_ignore_events(self, config, opportunity_id):
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
        monkeypatch.setattr(mopup, "fetch_buildings", lambda area: [object()] * 10)

        rate = ward_children_per_building("Sabon Gari", "Rano", "Kano", [1], pipeline=pipeline)
        # 2 distinct children (A, B) / 10 buildings
        assert rate == pytest.approx(0.2)

    def test_no_matching_work_areas_contributes_zero_children(self, monkeypatch):
        pipeline = _FakePipeline({1: []}, {1: []})
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward, candidates=None: {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        monkeypatch.setattr(mopup, "fetch_buildings", lambda area: [object()] * 5)
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
        monkeypatch.setattr(mopup, "fetch_buildings", lambda area: [])
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
        monkeypatch.setattr(mopup, "fetch_buildings", lambda area: [object()] * 4)
        rate = ward_children_per_building("Sabon Gari", "Rano", "Kano", [1, 2], pipeline=pipeline)
        # 2 distinct children total (one per opp) / 4 buildings
        assert rate == pytest.approx(0.5)
