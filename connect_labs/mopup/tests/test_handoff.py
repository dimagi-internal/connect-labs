"""Tests for the Phase 2 -> Phase 3 hand-off (core/handoff.py) — mocks the
microplans functions it calls into (ProgramPlanDataAccess, plan_to_json), and
this app's own ward_children_per_building/planning-gap functions. Uses the
REAL carry_forward_features (pure shapely, no network/DB) so hulls/area_id
content is genuinely exercised, not mocked away."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connect_labs.mopup.core.handoff import HandoffError, create_plan_from_locked_run
from connect_labs.mopup.core.models import MopupRunRecord


def _run(candidates, *, name="CHC Mop-up", target_opportunity_id=2154):
    return MopupRunRecord(
        {
            "id": 1,
            "experiment": "217",
            "type": "mopup_run",
            "opportunity_id": None,
            "program_id": 217,
            "data": {
                "status": "locked",
                "name": name,
                "target_opportunity_id": target_opportunity_id,
                "selected_wards": [],
                "date_from": None,
                "date_to": None,
                "thresholds": {},
                "candidate_work_areas": candidates,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        }
    )


_DEFAULT_BOUNDARY = {"type": "Polygon", "coordinates": [[[3.0, 6.0], [3.1, 6.0], [3.1, 6.1], [3.0, 6.1], [3.0, 6.0]]]}
_UNSET = object()


def _candidate(wa_id, ward="Sabon Gari", lga="Rano", state="Kano", boundary=_UNSET, building_count=100):
    return {
        "wa_id": wa_id,
        "ward": ward,
        "lga": lga,
        "state": state,
        "flw_username": "flw-1",
        "boundary": _DEFAULT_BOUNDARY if boundary is _UNSET else boundary,
        "building_count": building_count,
        "expected_visit_count": building_count,
        "source": "existing_wa",
        "triggered_indicators": ["evc_shortfall"],
        "severity_count": 1,
        "detail": {},
    }


def _mock_microplans(monkeypatch, *, target_by_ward=None, plans=None):
    """Patch every microplans/mop-up call handoff.py makes that isn't pure
    geometry math (matching core/handoff.py's own local-import style)."""
    import connect_labs.microplans.core.data_access as data_access_module
    import connect_labs.mopup.core.handoff as handoff_module

    plans = plans if plans is not None else {}
    calls = {"targets": [], "create_plan": []}

    def fake_target(ward, lga, state, opportunity_ids, *, request=None, pipeline=None):
        calls["targets"].append((ward, lga, state, opportunity_ids))
        return (target_by_ward or {}).get(ward, 1.5)

    class FakeProgramPlanDataAccess:
        def __init__(self, program_id, request=None, access_token=None):
            self.program_id = program_id

        def create_plan(self, **kwargs):
            calls["create_plan"].append(kwargs)
            plan_id = len(plans) + 1
            plans[plan_id] = SimpleNamespace(id=plan_id, data={**kwargs, "status": "draft"})
            return plans[plan_id]

        def add_plan_to_group(self, group_id, plan_id):
            calls.setdefault("group", []).append((group_id, plan_id))

    # handoff.py imports ward_children_per_building at module load time (`from
    # ...areas import ...`), so patch the NAME AS BOUND IN handoff.py — patching
    # core.areas's own attribute wouldn't touch handoff.py's already-resolved
    # reference.
    monkeypatch.setattr(handoff_module, "ward_children_per_building", fake_target)
    monkeypatch.setattr(data_access_module, "ProgramPlanDataAccess", FakeProgramPlanDataAccess)

    import connect_labs.microplans.serialization as serialization_module

    monkeypatch.setattr(
        serialization_module, "plan_to_json", lambda plan: {"plan_id": plan.id, "status": plan.data.get("status")}
    )

    return calls


class TestCreatePlanFromLockedRun:
    def test_raises_when_no_candidates(self):
        run = _run([])
        with pytest.raises(HandoffError, match="no locked candidates"):
            create_plan_from_locked_run(run, 217)

    def test_raises_when_no_target_opportunity(self):
        run = _run([_candidate("wa-1")], target_opportunity_id=None)
        with pytest.raises(HandoffError, match="no target opportunity"):
            create_plan_from_locked_run(run, 217)

    def test_raises_when_no_candidate_has_geometry(self):
        run = _run([_candidate("wa-1", boundary=None)])
        with pytest.raises(HandoffError, match="boundary geometry"):
            create_plan_from_locked_run(run, 217)

    def test_creates_plan_from_carry_forward_only(self, monkeypatch):
        plans = {}
        calls = _mock_microplans(monkeypatch, target_by_ward={"Sabon Gari": 2.0}, plans=plans)
        run = _run([_candidate("wa-1"), _candidate("wa-2", boundary=None)])

        resp = create_plan_from_locked_run(run, 217)

        assert resp["plan_status"] == "draft"
        assert resp["skipped_no_geometry"] == ["wa-2"]
        assert resp["planning_gap_cells_added"] == 0
        assert resp["planning_gap_warnings"] == {}
        assert calls["targets"] == [("Sabon Gari", "Rano", "Kano", [2154])]
        assert len(plans) == 1

        hulls = calls["create_plan"][0]["hulls"]
        assert len(hulls["features"]) == 1
        feature = hulls["features"][0]
        assert feature["properties"]["area_id"] == "mopup-kano-rano-sabon-gari"
        assert feature["properties"]["building_count"] == 100
        from shapely.geometry import shape

        assert shape(feature["geometry"]).equals(shape(_DEFAULT_BOUNDARY))

        # area_targets = rate (2.0) * retained_buildings (100, from the
        # candidate's own building_count, NOT a mocked grid function) = 200,
        # matching core/areas.py's documented double-division gotcha.
        assert calls["create_plan"][0]["area_targets"]["mopup-kano-rano-sabon-gari"] == 200.0
        assert calls["create_plan"][0]["run_meta"]["mopup_run_id"] == 1
        assert calls["create_plan"][0]["run_meta"]["include_planning_gaps"] is False

    def test_two_candidates_same_ward_stay_distinct_in_hulls(self, monkeypatch):
        # Directly exercises the carry-forward property this whole redesign
        # is for: two locked candidates in the same ward produce TWO hull
        # features (their own shapes), not one unioned blob.
        plans = {}
        calls = _mock_microplans(monkeypatch, plans=plans)
        boundary2 = {"type": "Polygon", "coordinates": [[[4.0, 6.0], [4.1, 6.0], [4.1, 6.1], [4.0, 6.1], [4.0, 6.0]]]}
        run = _run([_candidate("wa-1"), _candidate("wa-2", boundary=boundary2)])

        create_plan_from_locked_run(run, 217)

        # Two features, not one: a union would collapse these to a single
        # blob and still produce a valid plan, so only the COUNT plus the
        # distinct geometries can tell the two apart.
        hulls = calls["create_plan"][0]["hulls"]
        assert len(hulls["features"]) == 2

        from shapely.geometry import shape

        shapes = [shape(f["geometry"]) for f in hulls["features"]]
        assert not shapes[0].equals(shapes[1]), "both hulls carry the same shape — they were unioned"
        assert any(s.equals(shape(_DEFAULT_BOUNDARY)) for s in shapes)
        assert any(s.equals(shape(boundary2)) for s in shapes)

        # Same ward, so both retain that ward's area_id; the features are
        # distinguished by their own geometry, not by being re-keyed.
        assert {f["properties"]["area_id"] for f in hulls["features"]} == {"mopup-kano-rano-sabon-gari"}

    def test_ward_target_failure_is_best_effort_not_fatal(self, monkeypatch):
        plans = {}

        def boom(*a, **k):
            raise RuntimeError("boundary lookup blew up")

        _mock_microplans(monkeypatch, plans=plans)
        import connect_labs.mopup.core.handoff as handoff_module

        monkeypatch.setattr(handoff_module, "ward_children_per_building", boom)

        run = _run([_candidate("wa-1")])
        resp = create_plan_from_locked_run(run, 217)
        assert resp["plan_status"] == "draft"
        assert len(plans) == 1

    def test_group_id_adds_to_group(self, monkeypatch):
        plans = {}
        calls = _mock_microplans(monkeypatch, plans=plans)
        run = _run([_candidate("wa-1")])
        create_plan_from_locked_run(run, 217, group_id=99)
        assert calls["group"] == [(99, 1)]

    def test_planning_gap_features_already_on_the_run_are_merged_in(self, monkeypatch):
        # Planning gaps are computed by Phase 2's Step 2
        # (mopup.tasks.preview_planning_gaps) and stored onto the run —
        # create_plan_from_locked_run just carries whatever's there forward,
        # it never computes them itself.
        plans = {}
        calls = _mock_microplans(monkeypatch, target_by_ward={"Sabon Gari": 2.0}, plans=plans)

        gap_feature = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[9, 9], [9.001, 9], [9.001, 9.001], [9, 9.001], [9, 9]]]},
            "properties": {
                "cluster": "mopup-kano-rano-sabon-gari-gap-C0",
                "area_id": "mopup-kano-rano-sabon-gari",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 3,
                "expected_visit_count": 3,
                "cell_size_m": 100.0,
            },
        }
        run = _run([_candidate("wa-1")])
        run.data["planning_gap_features"] = [gap_feature]

        resp = create_plan_from_locked_run(run, 217)

        assert resp["planning_gap_cells_added"] == 1
        assert resp["planning_gap_warnings"] == {}
        hulls = calls["create_plan"][0]["hulls"]
        assert len(hulls["features"]) == 2
        area_ids = {f["properties"]["area_id"] for f in hulls["features"]}
        assert area_ids == {"mopup-kano-rano-sabon-gari"}
        clusters = {f["properties"]["cluster"] for f in hulls["features"]}
        assert any("-existing-" in c for c in clusters)
        assert any("-gap-" in c for c in clusters)
        # The gap cell's 3 buildings pool into the SAME area_id's retained
        # count as the carry-forward candidate's 100, so area_targets scales
        # by (100 + 3), not just 100.
        assert calls["create_plan"][0]["area_targets"]["mopup-kano-rano-sabon-gari"] == pytest.approx(2.0 * 103)
        assert calls["create_plan"][0]["run_meta"]["include_planning_gaps"] is True

    def test_no_planning_gap_features_on_the_run_means_carry_forward_only(self, monkeypatch):
        plans = {}
        calls = _mock_microplans(monkeypatch, plans=plans)
        run = _run([_candidate("wa-1")])  # planning_gap_features defaults to []
        create_plan_from_locked_run(run, 217)
        assert len(calls["create_plan"][0]["hulls"]["features"]) == 1
        assert calls["create_plan"][0]["run_meta"]["include_planning_gaps"] is False

    def test_planning_gap_warnings_already_on_the_run_are_passed_through_unchanged(self, monkeypatch):
        # Step 2 already recorded per-ward failures (e.g. an expired
        # CommCare HQ session) -- handoff.py doesn't recompute or
        # re-validate them, just carries them into the response so the
        # reviewer sees the same warning at hand-off time too.
        plans = {}
        _mock_microplans(monkeypatch, plans=plans)
        run = _run([_candidate("wa-1")])
        run.data["planning_gap_warnings"] = {"Sabon Gari": "grid generation blew up"}

        resp = create_plan_from_locked_run(run, 217)
        assert resp["plan_status"] == "draft"
        assert resp["planning_gap_cells_added"] == 0
        assert resp["planning_gap_warnings"] == {"Sabon Gari": "grid generation blew up"}

    def test_on_stage_reports_progress_through_the_hand_off(self, monkeypatch):
        plans = {}
        _mock_microplans(monkeypatch, plans=plans)
        run = _run([_candidate("wa-1")])
        stages = []
        create_plan_from_locked_run(run, 217, on_stage=stages.append)
        assert "Preparing candidate work areas…" in stages
        assert "Creating the plan…" in stages
