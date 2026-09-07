"""View tests for the mop-up Phase 1 setup flow — auth gate, request
validation, and the JSON envelopes, with the data-access/work-area layers
mocked out (mirrors microplans/tests/test_views.py's style)."""

from __future__ import annotations

import json
import time

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _login(client, django_user_model):
    user = django_user_model.objects.create(username="tester", email="t@example.com")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()
    return user


def _make_fake_run_da(monkeypatch, runs=None):
    """A MopupRunDataAccess stand-in over an in-memory store."""
    from connect_labs.mopup.core.models import STATUS_ANALYSIS, STATUS_SETUP, MopupRunRecord

    runs = runs if runs is not None else {}
    seq = {"run": (max(runs) if runs else 0) + 1}

    class FakeDA:
        def __init__(self, program_id, *a, **k):
            self.program_id = int(program_id)

        def create_run(self, *, target_opportunity_id, name=""):
            rid = seq["run"]
            seq["run"] += 1
            runs[rid] = MopupRunRecord(
                {
                    "id": rid,
                    "experiment": str(self.program_id),
                    "type": "mopup_run",
                    "opportunity_id": None,
                    "program_id": self.program_id,
                    "data": {
                        "status": STATUS_SETUP,
                        "name": name or "CHC Mop-up",
                        "target_opportunity_id": target_opportunity_id,
                        "selected_wards": [],
                        "date_from": None,
                        "date_to": None,
                        "thresholds": {},
                        "candidate_work_areas": [],
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                }
            )
            return runs[rid]

        def get_run(self, run_id):
            return runs.get(int(run_id))

        def list_runs(self):
            return list(runs.values())

        def update_run(self, run, **field_updates):
            run.data.update(field_updates)
            return run

        def set_ward_selection(self, run, *, wards, date_from=None, date_to=None):
            return self.update_run(
                run, selected_wards=wards, date_from=date_from, date_to=date_to, status=STATUS_ANALYSIS
            )

    import connect_labs.mopup.views as views_module

    monkeypatch.setattr(views_module, "MopupRunDataAccess", FakeDA)
    return runs


# --- MopupSetupView ---------------------------------------------------------


def test_setup_view_requires_login(client):
    resp = client.get(reverse("mopup:setup", kwargs={"program_id": 217}))
    assert resp.status_code in (302, 401, 403)


def test_setup_view_renders_program_opportunities(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    import connect_labs.mopup.views as views_module

    monkeypatch.setattr(
        views_module,
        "_program_opportunities",
        lambda request, program_id: [{"id": 2154, "name": "CHC - NG - JHF - RCT - AUG 26", "is_active": True}],
    )
    resp = client.get(reverse("mopup:setup", kwargs={"program_id": 217}))
    assert resp.status_code == 200
    assert b"CHC - NG - JHF - RCT - AUG 26" in resp.content


# --- MopupWardListView -------------------------------------------------------


def test_ward_list_requires_opportunity_id(client, django_user_model):
    _login(client, django_user_model)
    resp = client.get(reverse("mopup:ward_list", kwargs={"program_id": 217}))
    assert resp.status_code == 400


def test_ward_list_rejects_non_integer_opportunity_id(client, django_user_model):
    _login(client, django_user_model)
    resp = client.get(reverse("mopup:ward_list", kwargs={"program_id": 217}), {"opportunity_id": "not-a-number"})
    assert resp.status_code == 400


def test_ward_list_returns_summarized_wards(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    import connect_labs.mopup.views as views_module

    monkeypatch.setattr(
        views_module,
        "list_work_areas",
        lambda opportunity_id, request=None, pipeline=None: [
            {
                "case_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 10,
                "expected_visit_count": 5,
                "status": "VISITED",
            }
        ],
    )
    resp = client.get(reverse("mopup:ward_list", kwargs={"program_id": 217}), {"opportunity_id": "2154"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["wards"] == [
        {
            "ward": "Sabon Gari",
            "lga": "Rano",
            "state": "Kano",
            "work_area_count": 1,
            "building_count": 10,
            "expected_visit_count": 5,
        }
    ]


def test_ward_list_fetch_failure_is_502(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    import connect_labs.mopup.views as views_module

    def boom(opportunity_id, request=None, pipeline=None):
        raise RuntimeError("CCHQ auth expired")

    monkeypatch.setattr(views_module, "list_work_areas", boom)
    resp = client.get(reverse("mopup:ward_list", kwargs={"program_id": 217}), {"opportunity_id": "2154"})
    assert resp.status_code == 502


# --- MopupCreateRunView ------------------------------------------------------


def test_create_run_requires_login(client):
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data=json.dumps({"opportunity_id": 2154}),
        content_type="application/json",
    )
    assert resp.status_code in (302, 401, 403)


def test_create_run_persists_wards_and_dates(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data=json.dumps(
            {
                "opportunity_id": 2154,
                "wards": [{"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"}],
                "date_from": "2026-01-01",
                "date_to": "2026-06-01",
                "name": "First mop-up round",
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["target_opportunity_id"] == 2154
    assert body["selected_wards"] == [{"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"}]
    assert body["date_from"] == "2026-01-01"
    assert body["date_to"] == "2026-06-01"
    from connect_labs.mopup.core.models import STATUS_ANALYSIS

    assert body["run_status"] == STATUS_ANALYSIS
    assert len(runs) == 1


def test_create_run_defaults_to_no_wards_and_no_dates(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data=json.dumps({"opportunity_id": 2154}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["selected_wards"] == []
    assert body["date_from"] is None
    assert body["date_to"] is None


def test_create_run_requires_opportunity_id(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_create_run_rejects_malformed_body(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data="not json",
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_create_run_rejects_non_list_wards(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data=json.dumps({"opportunity_id": 2154, "wards": "not-a-list"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# --- MopupCandidatesView -----------------------------------------------------


def _seed_run(runs, *, target_opportunity_id=2154, thresholds=None):
    from connect_labs.mopup.core.models import STATUS_ANALYSIS, MopupRunRecord

    runs[1] = MopupRunRecord(
        {
            "id": 1,
            "experiment": "217",
            "type": "mopup_run",
            "opportunity_id": None,
            "program_id": 217,
            "data": {
                "status": STATUS_ANALYSIS,
                "name": "Test run",
                "target_opportunity_id": target_opportunity_id,
                "selected_wards": [],
                "date_from": None,
                "date_to": None,
                "thresholds": thresholds or {},
                "candidate_work_areas": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        }
    )
    return runs[1]


def _mock_evaluation_input(monkeypatch, rows):
    import connect_labs.mopup.views as views_module

    monkeypatch.setattr(views_module, "build_evaluation_input", lambda opp_id, wards, request=None: rows)


def test_candidates_requires_login(client):
    resp = client.post(reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code in (302, 401, 403)


def test_candidates_returns_404_for_missing_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 999}))
    assert resp.status_code == 404


def test_candidates_uses_default_config_when_none_saved(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)
    _mock_evaluation_input(
        monkeypatch,
        [
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
                "expected_visit_count": 10,
                "approved_hsd_count": 1,
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 0,
                "deworming_given": 0,
                "muac_given": 0,
                "vaccination_given": 0,
            }
        ],
    )
    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["total_work_areas"] == 1
    # Default granularity is cluster-aware (§6's recommended default) — a
    # single isolated WA has no neighbors to corroborate a bad EVC ratio, so
    # it correctly does NOT become a candidate (see test_indicators.py's
    # isolated-outlier coverage for the same rule in isolation).
    assert body["candidate_count"] == 0


def test_candidates_accepts_threshold_override(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)
    _mock_evaluation_input(
        monkeypatch,
        [
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
                "expected_visit_count": 10,
                "approved_hsd_count": 9,
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 0,
                "deworming_given": 0,
                "muac_given": 0,
                "vaccination_given": 0,
            }
        ],
    )
    from connect_labs.mopup.core import indicators as ind

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps(
            {
                "indicator_configs": {
                    ind.EVC_SHORTFALL: {
                        "enabled": True,
                        "threshold": 0.95,  # tighter than default -> 0.9 now fails
                        "granularity": ind.GRANULARITY_WA_ONLY,
                    }
                }
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["candidate_count"] == 1


def test_candidates_persists_thresholds_used(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)
    _mock_evaluation_input(monkeypatch, [])
    from connect_labs.mopup.core import indicators as ind

    custom_configs = {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.4, "granularity": ind.GRANULARITY_WA_ONLY}}
    client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps({"indicator_configs": custom_configs}),
        content_type="application/json",
    )
    assert runs[1].thresholds["indicator_configs"] == custom_configs


def test_candidates_fetch_failure_is_502(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)
    import connect_labs.mopup.views as views_module

    def boom(opp_id, wards, request=None):
        raise RuntimeError("CCHQ auth expired")

    monkeypatch.setattr(views_module, "build_evaluation_input", boom)
    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 502


def test_candidates_rejects_malformed_body(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)
    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data="not json",
        content_type="application/json",
    )
    assert resp.status_code == 400


# --- MopupAnalysisView -------------------------------------------------------


def test_analysis_view_requires_login(client):
    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code in (302, 401, 403)


def test_analysis_view_404s_for_missing_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 999}))
    assert resp.status_code == 404


def test_analysis_view_renders_saved_thresholds(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    from connect_labs.mopup.core import indicators as ind

    custom = {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.42, "granularity": ind.GRANULARITY_WA_ONLY}}
    _seed_run(runs, thresholds={"indicator_configs": custom})
    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 200
    assert b"0.42" in resp.content


def test_analysis_view_falls_back_to_defaults_when_no_thresholds_saved(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)
    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 200
    from connect_labs.mopup.core import indicators as ind

    assert str(ind.DEFAULT_INDICATOR_CONFIGS[ind.EVC_SHORTFALL]["threshold"]).encode() in resp.content
