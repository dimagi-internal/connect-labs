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
