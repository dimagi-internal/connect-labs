"""Tests for the mop-up evaluation-data fetch Celery task — the one
expensive per-run pull, offloaded here because a synchronous web request
doing this for a real opportunity reliably gateway-times-out (confirmed
this session against program 217's real data)."""

from __future__ import annotations

import pytest

from connect_labs.labs.connect_tokens import ConnectReLoginRequired
from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQReLoginRequired
from connect_labs.mopup import tasks
from connect_labs.mopup.core.models import MopupRunRecord

pytestmark = pytest.mark.django_db


def _run(target_opportunity_id=2154, selected_wards=None):
    return MopupRunRecord(
        {
            "id": 1,
            "experiment": "217",
            "type": "mopup_run",
            "opportunity_id": None,
            "program_id": 217,
            "data": {
                "status": "analysis",
                "name": "Test run",
                "target_opportunity_id": target_opportunity_id,
                "selected_wards": selected_wards or [],
                "date_from": None,
                "date_to": None,
                "thresholds": {},
                "candidate_work_areas": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        }
    )


def _patch_da(monkeypatch, run):
    class FakeDA:
        def __init__(self, program_id, access_token=None):
            self.program_id = program_id

        def get_run(self, run_id):
            return run

    monkeypatch.setattr("connect_labs.mopup.core.data_access.MopupRunDataAccess", FakeDA)
    return FakeDA


class TestFetchEvaluationData:
    def test_success_returns_rows(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", lambda u: "cchq-token")
        _patch_da(monkeypatch, _run())

        fake_rows = [{"wa_id": "wa-1"}, {"wa_id": "wa-2"}]
        stages_seen = []

        def fake_build_evaluation_input(opp_id, wards, pipeline=None, on_stage=None):
            if on_stage:
                on_stage("Fetching work areas…")
            stages_seen.append((opp_id, wards))
            return fake_rows

        monkeypatch.setattr("connect_labs.mopup.core.candidates.build_evaluation_input", fake_build_evaluation_input)

        result = tasks.fetch_evaluation_data.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()
        assert result == {"rows": fake_rows}
        assert stages_seen == [(2154, [])]

    def test_connect_token_failure_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)

        def boom(u):
            raise ConnectReLoginRequired("dead refresh token")

        monkeypatch.setattr(tasks, "get_valid_access_token", boom)

        with pytest.raises(RuntimeError, match="Connect authorization needed"):
            tasks.fetch_evaluation_data.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_cchq_token_failure_raises_after_connect_succeeds(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")

        def boom(u):
            raise CCHQReLoginRequired("no CommCare HQ authorization")

        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", boom)

        with pytest.raises(RuntimeError, match="CommCare HQ authorization needed"):
            tasks.fetch_evaluation_data.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_missing_run_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", lambda u: "cchq-token")
        _patch_da(monkeypatch, None)

        with pytest.raises(RuntimeError, match="not found"):
            tasks.fetch_evaluation_data.apply(kwargs={"program_id": 217, "run_id": 999, "user_id": user.id}).get()


def _locked_run():
    run = _run()
    run.data["status"] = "locked"
    run.data["candidate_work_areas"] = [{"wa_id": "wa-1"}]
    return run


class TestCreateMopupPlan:
    """The Phase 3 hand-off, offloaded the same way as fetch_evaluation_data
    — see tasks.py's own docstring for why (a real include_planning_gaps
    hand-off measured well over a minute synchronously this session)."""

    def test_success_returns_the_handoff_response(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        _patch_da(monkeypatch, _locked_run())

        fake_resp = {"plan_id": 42, "plan_status": "draft", "urls": {"review": "/x/"}}
        stages_seen = []

        def fake_create_plan_from_locked_run(
            run, program_id, *, pipeline, access_token, grouping, group_id, on_stage=None
        ):
            if on_stage:
                on_stage("Creating the plan…")
            stages_seen.append((program_id, grouping, group_id))
            return dict(fake_resp)

        monkeypatch.setattr(
            "connect_labs.mopup.core.handoff.create_plan_from_locked_run", fake_create_plan_from_locked_run
        )

        result = tasks.create_mopup_plan.apply(
            kwargs={"program_id": 217, "run_id": 1, "user_id": user.id, "group_id": 7}
        ).get()
        assert result["status"] == "ok"
        assert result["plan_id"] == 42
        assert stages_seen == [(217, None, 7)]

    def test_handoff_error_becomes_a_normal_error_result_not_a_task_failure(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        _patch_da(monkeypatch, _locked_run())

        from connect_labs.mopup.core.handoff import HandoffError

        def boom(*a, **k):
            raise HandoffError("no boundary geometry")

        monkeypatch.setattr("connect_labs.mopup.core.handoff.create_plan_from_locked_run", boom)

        result = tasks.create_mopup_plan.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()
        assert result == {"status": "error", "detail": "no boundary geometry"}

    def test_connect_token_failure_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)

        def boom(u):
            raise ConnectReLoginRequired("dead refresh token")

        monkeypatch.setattr(tasks, "get_valid_access_token", boom)

        with pytest.raises(RuntimeError, match="Connect authorization needed"):
            tasks.create_mopup_plan.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_never_requires_a_cchq_token(self, django_user_model, monkeypatch):
        # Planning-gap ward lookups (the only cchq_cases-sourced part of a
        # mop-up hand-off) moved to Phase 2's Step 2 (preview_planning_gaps)
        # -- this task carries forward whatever Step 2 already computed
        # (run.planning_gap_features), so it never needs a CommCare HQ token
        # at all, even if one would fail.
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")

        def boom(u):
            raise CCHQReLoginRequired("no CommCare HQ authorization")

        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", boom)
        _patch_da(monkeypatch, _locked_run())
        monkeypatch.setattr(
            "connect_labs.mopup.core.handoff.create_plan_from_locked_run",
            lambda *a, **k: {"plan_id": 1, "plan_status": "draft", "urls": {}},
        )

        result = tasks.create_mopup_plan.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()
        assert result["status"] == "ok"

    def test_missing_run_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        _patch_da(monkeypatch, None)

        with pytest.raises(RuntimeError, match="not found"):
            tasks.create_mopup_plan.apply(kwargs={"program_id": 217, "run_id": 999, "user_id": user.id}).get()

    def test_unlocked_run_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        _patch_da(monkeypatch, _run())  # status "analysis", never locked

        with pytest.raises(RuntimeError, match="Lock the run"):
            tasks.create_mopup_plan.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()


_CANDIDATE_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[3.0, 6.0], [3.1, 6.0], [3.1, 6.1], [3.0, 6.1], [3.0, 6.0]]],
}


def _locked_run_with_geometry():
    run = _run()
    run.data["status"] = "locked"
    run.data["candidate_work_areas"] = [
        {
            "wa_id": "wa-1",
            "ward": "Sabon Gari",
            "lga": "Rano",
            "state": "Kano",
            "boundary": _CANDIDATE_BOUNDARY,
            "building_count": 10,
            "expected_visit_count": 10,
        }
    ]
    return run


class TestPreviewPlanningGaps:
    """Phase 2 Step 2's building fetch + gap-grid preview, offloaded the same
    way as fetch_evaluation_data/create_mopup_plan -- confirmed this session
    that a real hand-off-time equivalent took well over a minute
    synchronously."""

    def _mock_common(self, monkeypatch, run, *, gap_features=None):
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", lambda u: "cchq-token")
        _patch_da(monkeypatch, run)
        monkeypatch.setattr("connect_labs.mopup.core.candidates.build_evaluation_input", lambda *a, **k: [])
        monkeypatch.setattr(
            "connect_labs.mopup.core.gaps.work_area_boundaries_for_ward", lambda *a, **k: [_CANDIDATE_BOUNDARY]
        )
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward: {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        monkeypatch.setattr(
            "connect_labs.mopup.core.gaps.planning_gap_features", lambda *a, **k: list(gap_features or [])
        )

    def test_success_returns_features_and_config(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _locked_run_with_geometry()
        gap_feature = {"type": "Feature", "properties": {"ward": "Sabon Gari"}}
        self._mock_common(monkeypatch, run, gap_features=[gap_feature])

        result = tasks.preview_planning_gaps.apply(
            kwargs={
                "program_id": 217,
                "run_id": 1,
                "user_id": user.id,
                "building_sources": ["Google Open Buildings"],
                "min_confidence": 0.6,
                "min_buildings_per_cell": 2,
                "cell_size_m": 50.0,
            }
        ).get()
        assert result["status"] == "ok"
        assert result["features"] == [gap_feature]
        assert result["cells_added"] == 1
        assert result["warnings"] == {}
        assert result["config"] == {
            "mode": "overture",
            "building_sources": ["Google Open Buildings"],
            "min_confidence": 0.6,
            "min_buildings_per_cell": 2,
            "cell_size_m": 50.0,
        }

    def test_ward_failure_is_best_effort_not_fatal(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _locked_run_with_geometry()
        self._mock_common(monkeypatch, run)

        def boom(*a, **k):
            raise RuntimeError("grid generation blew up")

        monkeypatch.setattr("connect_labs.mopup.core.gaps.planning_gap_features", boom)

        result = tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()
        assert result["status"] == "ok"
        assert result["cells_added"] == 0
        assert result["warnings"] == {"Sabon Gari": "grid generation blew up"}

    def test_missing_ward_boundary_is_reported_as_a_warning(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _locked_run_with_geometry()
        self._mock_common(monkeypatch, run)
        monkeypatch.setattr(
            "connect_labs.microplans.core.admin_boundaries.find_ward_boundary_geometry",
            lambda state, lga, ward: None,
        )

        result = tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()
        assert result["cells_added"] == 0
        assert result["warnings"] == {"Sabon Gari": "no ward boundary match — skipped"}

    def test_connect_token_failure_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)

        def boom(u):
            raise ConnectReLoginRequired("dead refresh token")

        monkeypatch.setattr(tasks, "get_valid_access_token", boom)

        with pytest.raises(RuntimeError, match="Connect authorization needed"):
            tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_cchq_token_failure_raises_after_connect_succeeds(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")

        def boom(u):
            raise CCHQReLoginRequired("no CommCare HQ authorization")

        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", boom)

        with pytest.raises(RuntimeError, match="CommCare HQ authorization needed"):
            tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_missing_run_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", lambda u: "cchq-token")
        _patch_da(monkeypatch, None)

        with pytest.raises(RuntimeError, match="not found"):
            tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 999, "user_id": user.id}).get()

    def test_unlocked_run_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", lambda u: "cchq-token")
        _patch_da(monkeypatch, _run())  # status "analysis", never locked

        with pytest.raises(RuntimeError, match="Lock the run"):
            tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_no_geometry_raises(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _run()
        run.data["status"] = "locked"
        run.data["candidate_work_areas"] = [{"wa_id": "wa-1", "ward": "Sabon Gari", "boundary": None}]
        self._mock_common(monkeypatch, run)

        with pytest.raises(RuntimeError, match="boundary geometry"):
            tasks.preview_planning_gaps.apply(kwargs={"program_id": 217, "run_id": 1, "user_id": user.id}).get()

    def test_skip_mode_returns_immediately_with_no_features(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _locked_run_with_geometry()
        monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "get_valid_access_token", lambda u: "connect-token")
        monkeypatch.setattr(tasks, "get_valid_cchq_access_token", lambda u: "cchq-token")
        _patch_da(monkeypatch, run)

        def boom(*a, **k):
            raise AssertionError("skip mode should never reach the per-ward gap computation")

        monkeypatch.setattr("connect_labs.mopup.core.candidates.build_evaluation_input", boom)

        result = tasks.preview_planning_gaps.apply(
            kwargs={"program_id": 217, "run_id": 1, "user_id": user.id, "mode": "skip"}
        ).get()
        assert result == {
            "status": "ok",
            "features": [],
            "cells_added": 0,
            "warnings": {},
            "config": {
                "mode": "skip",
                "building_sources": None,
                "min_confidence": None,
                "min_buildings_per_cell": 1,
                "cell_size_m": 100.0,
            },
        }

    def test_upload_mode_reads_the_stored_csv_and_filters_per_ward(self, django_user_model, monkeypatch):
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _locked_run_with_geometry()
        self._mock_common(monkeypatch, run)

        import pandas as pd

        uploaded_df = pd.DataFrame(
            [
                {
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "wardname": "Sabon Gari",
                    "lganame": "Rano",
                    "statename": "Kano",
                },
                {  # a different ward -- must never leak into Sabon Gari's cells
                    "latitude": 3.0,
                    "longitude": 4.0,
                    "wardname": "Somewhere Else",
                    "lganame": "Rano",
                    "statename": "Kano",
                },
            ]
        )
        monkeypatch.setattr(tasks, "_read_uploaded_buildings_csv", lambda key: uploaded_df)

        seen_buildings = {}

        def fake_planning_gap_features(ward, lga, state, area_id, ward_boundary, existing, **kw):
            seen_buildings["buildings"] = kw.get("buildings")
            return []

        monkeypatch.setattr("connect_labs.mopup.core.gaps.planning_gap_features", fake_planning_gap_features)

        result = tasks.preview_planning_gaps.apply(
            kwargs={
                "program_id": 217,
                "run_id": 1,
                "user_id": user.id,
                "mode": "upload",
                "csv_storage_key": "mopup/uploads/run-1/abc.csv",
            }
        ).get()
        assert result["status"] == "ok"
        assert result["config"]["mode"] == "upload"
        matched = seen_buildings["buildings"]
        assert len(matched) == 1
        assert matched.iloc[0]["lat"] == pytest.approx(1.0)

    def test_upload_mode_without_a_stored_key_raises(self, django_user_model, monkeypatch):
        # Defensive only -- MopupPlanningGapsView already rejects this
        # synchronously (400) before ever dispatching the task, so this
        # path is a belt-and-suspenders guard, not a normal-flow warning.
        user = django_user_model.objects.create(username="tester", email="t@example.com")
        run = _locked_run_with_geometry()
        self._mock_common(monkeypatch, run)

        with pytest.raises(RuntimeError, match="Upload a building-data file"):
            tasks.preview_planning_gaps.apply(
                kwargs={"program_id": 217, "run_id": 1, "user_id": user.id, "mode": "upload", "csv_storage_key": None}
            ).get()
