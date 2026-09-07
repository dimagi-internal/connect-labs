"""Tests for the Phase 2 -> Phase 3 hand-off (core/handoff.py) — mocks the
microplans functions it calls into (generate_coverage_frame, ProgramPlanDataAccess,
etc.), mirroring the mocking style the deleted ProgramCreateMopupPlanView tests
used before this logic moved into this sibling app."""

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


def _candidate(wa_id, ward="Sabon Gari", lga="Rano", state="Kano", boundary=_UNSET):
    return {
        "wa_id": wa_id,
        "ward": ward,
        "lga": lga,
        "state": state,
        "flw_username": "flw-1",
        "boundary": _DEFAULT_BOUNDARY if boundary is _UNSET else boundary,
        "triggered_indicators": ["evc_shortfall"],
        "severity_count": 1,
        "detail": {},
    }


def _mock_microplans(monkeypatch, *, target_by_ward=None, building_count=100, plans=None):
    """Patch every microplans call handoff.py makes, in-place (matching
    core/handoff.py's own local-import style)."""
    import connect_labs.microplans.core.data_access as data_access_module
    import connect_labs.mopup.core.handoff as handoff_module

    plans = plans if plans is not None else {}
    calls = {"generate": [], "targets": [], "create_plan": []}

    def fake_generate(areas, config):
        calls["generate"].append((areas, config))
        features = [
            {
                "type": "Feature",
                "geometry": a["geometry"],
                "properties": {
                    "area_id": a["area_id"],
                    "ward": a["ward"],
                    "lga": a["lga"],
                    "state": a["state"],
                    "building_count": building_count,
                    "expected_visit_count": building_count,
                },
            }
            for a in areas
        ]
        return SimpleNamespace(
            areas_geojson={"type": "FeatureCollection", "features": features},
            stats=[{"work_areas": len(features)}],
        )

    def fake_target(ward, lga, state, opportunity_ids, *, request=None, pipeline=None):
        calls["targets"].append((ward, lga, state, opportunity_ids))
        return (target_by_ward or {}).get(ward, 1.5)

    class FakeProgramPlanDataAccess:
        def __init__(self, program_id, request=None):
            self.program_id = program_id

        def create_plan(self, **kwargs):
            calls["create_plan"].append(kwargs)
            plan_id = len(plans) + 1
            plans[plan_id] = SimpleNamespace(id=plan_id, data={**kwargs, "status": "draft"})
            return plans[plan_id]

        def add_plan_to_group(self, group_id, plan_id):
            calls.setdefault("group", []).append((group_id, plan_id))

    import connect_labs.microplans.coverage.frame as frame_module

    monkeypatch.setattr(frame_module, "generate_coverage_frame", fake_generate)
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

    def test_creates_plan_and_skips_candidates_without_geometry(self, monkeypatch):
        plans = {}
        calls = _mock_microplans(monkeypatch, target_by_ward={"Sabon Gari": 2.0}, plans=plans)
        run = _run([_candidate("wa-1"), _candidate("wa-2", boundary=None)])

        resp = create_plan_from_locked_run(run, 217)

        assert resp["plan_status"] == "draft"
        assert resp["skipped_no_geometry"] == ["wa-2"]
        assert calls["targets"] == [("Sabon Gari", "Rano", "Kano", [2154])]
        assert len(plans) == 1
        # area_targets = rate (2.0) * retained_buildings (100) = 200, matching
        # core/areas.py's documented double-division gotcha.
        assert calls["create_plan"][0]["area_targets"]["mopup-kano-rano-sabon-gari"] == 200.0
        assert calls["create_plan"][0]["run_meta"]["mopup_run_id"] == 1

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
