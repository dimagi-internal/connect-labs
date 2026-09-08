"""View tests for the mop-up Phase 1 setup flow — auth gate, request
validation, and the JSON envelopes, with the data-access/work-area layers
mocked out (mirrors microplans/tests/test_views.py's style)."""

from __future__ import annotations

import json
import time
from unittest import mock

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


# --- _program_opportunities ---------------------------------------------------
#
# Real bug caught in live browser verification: an earlier version assumed
# get_org_data() returned a nested organizations->programs->opportunities
# tree; production's actual shape is three FLAT top-level lists, and each
# opportunity's parent program is under the key "program" (an int, per
# OpportunityDataExportSerializer.get_program -> obj.program_id), not
# "program_id" and not a nested list. These tests exercise the real
# function against that verified shape, rather than mocking it away.


def test_program_opportunities_filters_flat_list_by_program_field(monkeypatch):
    import connect_labs.mopup.views as views_module
    from connect_labs.mopup.views import _program_opportunities

    # views.py imports get_org_data at module load time (`from ...context
    # import get_org_data`), so patch the name AS BOUND IN views.py.
    monkeypatch.setattr(
        views_module,
        "get_org_data",
        lambda request: {
            "organizations": [{"id": 359, "slug": "dimagi-chc-rct", "name": "DIMAGI-CHC-RCT"}],
            "programs": [{"id": 217, "name": "CHC - NG - RCT - Aug 2026"}],
            "opportunities": [
                {"id": 2154, "name": "CHC - NG - JHF - RCT - AUG 26", "program": 217, "is_active": True},
                {"id": 2155, "name": "CHC - NG - EHA - RCT - AUG 26", "program": 217, "is_active": True},
                {"id": 9999, "name": "Unrelated opportunity", "program": 999, "is_active": True},
            ],
        },
    )
    result = _program_opportunities(object(), 217)
    assert {opp["id"] for opp in result} == {2154, 2155}


def test_program_opportunities_returns_empty_for_unknown_program(monkeypatch):
    import connect_labs.mopup.views as views_module

    monkeypatch.setattr(views_module, "get_org_data", lambda request: {"opportunities": []})
    from connect_labs.mopup.views import _program_opportunities

    assert _program_opportunities(object(), 217) == []


# --- MopupProgramHomeView ----------------------------------------------------
#
# Real gap, surfaced by the user this session: /mopup/program/<id>/ 404'd
# (no bare-program route existed — only setup/, ward_list/, create_run/, and
# run/<id>/...), unlike microplans' ProgramWorkspaceView at the equivalent
# path. This is the fix: a landing page listing existing runs + a link into
# Phase 1 for a new one.


def test_program_home_requires_login(client):
    resp = client.get(reverse("mopup:program_home", kwargs={"program_id": 217}))
    assert resp.status_code in (302, 401, 403)


def test_program_home_lists_runs_with_resolved_opportunity_names(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    import connect_labs.mopup.views as views_module
    from connect_labs.mopup.core.models import STATUS_ANALYSIS, MopupRunRecord

    monkeypatch.setattr(
        views_module,
        "_program_opportunities",
        lambda request, program_id: [{"id": 2154, "name": "CHC - NG - JHF - RCT - AUG 26", "program": 217}],
    )

    class FakeDA:
        def __init__(self, program_id, *a, **k):
            pass

        def list_runs(self):
            return [
                MopupRunRecord(
                    {
                        "id": 19187,
                        "experiment": "217",
                        "type": "mopup_run",
                        "opportunity_id": None,
                        "program_id": 217,
                        "data": {
                            "status": STATUS_ANALYSIS,
                            "name": "CHC Mop-up",
                            "target_opportunity_id": 2154,
                            "created_at": "2026-09-08T06:00:00+00:00",
                        },
                    }
                )
            ]

    monkeypatch.setattr(views_module, "MopupRunDataAccess", FakeDA)
    resp = client.get(reverse("mopup:program_home", kwargs={"program_id": 217}))
    assert resp.status_code == 200
    assert b"CHC - NG - JHF - RCT - AUG 26" in resp.content
    assert b"analysis" in resp.content.lower()


def test_program_home_shows_empty_state_with_no_runs(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    import connect_labs.mopup.views as views_module

    monkeypatch.setattr(views_module, "_program_opportunities", lambda request, program_id: [])

    class FakeDA:
        def __init__(self, program_id, *a, **k):
            pass

        def list_runs(self):
            return []

    monkeypatch.setattr(views_module, "MopupRunDataAccess", FakeDA)
    resp = client.get(reverse("mopup:program_home", kwargs={"program_id": 217}))
    assert resp.status_code == 200
    assert b"No mop-up runs yet" in resp.content


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


def _mock_ward_list_tokens(monkeypatch):
    """`MopupWardListView` resolves tokens via `get_valid_access_token`/
    `get_valid_cchq_access_token` (silent-refresh helpers, imported locally
    inside the view — patch the source modules, not `views_module`) rather
    than `AnalysisPipeline(request=request)`'s un-refreshed session token.
    See the view's own docstring for the real production bug this avoids."""
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda user: "connect-token")
    monkeypatch.setattr(
        "connect_labs.labs.integrations.commcare.cchq_tokens.get_valid_cchq_access_token", lambda user: "cchq-token"
    )


def test_ward_list_returns_summarized_wards(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _mock_ward_list_tokens(monkeypatch)
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
    _mock_ward_list_tokens(monkeypatch)
    import connect_labs.mopup.views as views_module

    def boom(opportunity_id, request=None, pipeline=None):
        raise RuntimeError("CCHQ auth expired")

    monkeypatch.setattr(views_module, "list_work_areas", boom)
    resp = client.get(reverse("mopup:ward_list", kwargs={"program_id": 217}), {"opportunity_id": "2154"})
    assert resp.status_code == 502


def test_ward_list_surfaces_expired_cchq_token_as_401_not_502(client, django_user_model, monkeypatch):
    # Real bug caught in live browser verification (program 217, opportunity
    # 2154): the view previously built `AnalysisPipeline(request=request)`,
    # which reads the CCHQ token straight from the session with no silent
    # refresh — an expired token then failed fast (~500ms) inside
    # `list_work_areas`, and the view's broad `except Exception` flattened
    # that into a generic 502 "Could not load work areas.", indistinguishable
    # from a real gateway timeout. Resolving tokens up front (mirroring
    # `mopup.tasks.fetch_evaluation_data`) surfaces this as an explicit,
    # actionable 401 instead.
    _login(client, django_user_model)
    from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError

    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda user: "connect-token")

    def boom(user):
        raise CCHQTokenError("token expired and refresh failed")

    monkeypatch.setattr("connect_labs.labs.integrations.commcare.cchq_tokens.get_valid_cchq_access_token", boom)
    resp = client.get(reverse("mopup:ward_list", kwargs={"program_id": 217}), {"opportunity_id": "2154"})
    assert resp.status_code == 401
    assert "Authorization needed" in resp.json()["detail"]


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


def _mock_ready_data(monkeypatch, run, rows):
    """Simulate a run whose fetch task has already completed — the common
    case for testing evaluation/lock behavior without re-testing the
    dispatch/poll machinery itself (see test_candidates_dispatches_a_fetch_task
    and test_candidates_polls_a_running_task for that). Patches
    `celery.result.AsyncResult` at its real import site (a local import
    inside `_rows_or_progress`, same as `connect_labs.workflow.views`'
    established test convention)."""
    run.data["fetch_task_id"] = "task-done-1"
    mock_result = mock.Mock(state="SUCCESS", info={"rows": rows})
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)


def _mock_task_state(monkeypatch, run, *, state, info=None, task_id="task-in-flight-1"):
    """Simulate a run whose fetch task exists but isn't done (or failed) —
    for testing the polling/progress-passthrough path itself."""
    run.data["fetch_task_id"] = task_id
    mock_result = mock.Mock(state=state, info=info)
    monkeypatch.setattr("celery.result.AsyncResult", lambda tid: mock_result)


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
    run = _seed_run(runs)
    _mock_ready_data(
        monkeypatch,
        run,
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
    run = _seed_run(runs)
    _mock_ready_data(
        monkeypatch,
        run,
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
    run = _seed_run(runs)
    _mock_ready_data(monkeypatch, run, [])
    from connect_labs.mopup.core import indicators as ind

    custom_configs = {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.4, "granularity": ind.GRANULARITY_WA_ONLY}}
    client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps({"indicator_configs": custom_configs}),
        content_type="application/json",
    )
    assert runs[1].thresholds["indicator_configs"] == custom_configs


def test_candidates_dispatches_a_fetch_task_when_none_exists(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    assert run.fetch_task_id is None

    fake_async_result = mock.Mock(id="fresh-task-id")
    with mock.patch("connect_labs.mopup.tasks.fetch_evaluation_data.delay", return_value=fake_async_result) as delay:
        resp = client.post(
            reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "pending"
    delay.assert_called_once_with(217, 1, mock.ANY)
    assert runs[1].fetch_task_id == "fresh-task-id"


def test_candidates_polls_a_running_task(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    _mock_task_state(monkeypatch, run, state="PROGRESS", info={"message": "Fetching visit data…"})

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "running"
    assert body["message"] == "Fetching visit data…"
    # still in flight -> not cleared, no re-dispatch on the next poll
    assert runs[1].fetch_task_id == "task-in-flight-1"


def test_candidates_surfaces_a_failed_task_and_clears_it_for_retry(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    _mock_task_state(monkeypatch, run, state="FAILURE", info=RuntimeError("CommCare HQ authorization needed"))

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "failed"
    assert "CommCare HQ authorization needed" in body["error"]
    # cleared -> the NEXT poll re-dispatches automatically, no manual retry needed
    assert runs[1].fetch_task_id is None


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


# --- MopupLockView -----------------------------------------------------------


def test_lock_requires_login(client):
    resp = client.post(reverse("mopup:lock", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code in (302, 401, 403)


def test_lock_returns_404_for_missing_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(reverse("mopup:lock", kwargs={"program_id": 217, "run_id": 999}))
    assert resp.status_code == 404


def test_lock_rejects_when_no_candidates(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    _mock_ready_data(monkeypatch, run, [])  # no work areas at all -> no candidates
    resp = client.post(
        reverse("mopup:lock", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "nothing to lock" in resp.json()["detail"]


def test_lock_freezes_candidates_and_sets_status(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    _mock_ready_data(
        monkeypatch,
        run,
        [
            {
                "wa_id": "wa-1",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "flw_username": "flw-1",
                "lat": None,
                "lon": None,
                "boundary": {"type": "Polygon", "coordinates": []},
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
    from connect_labs.mopup.core import indicators as ind

    resp = client.post(
        reverse("mopup:lock", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps(
            {
                "indicator_configs": {
                    ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY}
                }
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["locked_count"] == 1
    from connect_labs.mopup.core.models import STATUS_LOCKED

    assert body["run_status"] == STATUS_LOCKED
    assert runs[1].status == STATUS_LOCKED
    assert len(runs[1].candidate_work_areas) == 1
    assert runs[1].candidate_work_areas[0]["wa_id"] == "wa-1"


def test_lock_refuses_while_data_still_loading(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    _mock_task_state(monkeypatch, run, state="PROGRESS", info={"message": "Fetching visit data…"})

    resp = client.post(
        reverse("mopup:lock", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "running"
    from connect_labs.mopup.core.models import STATUS_ANALYSIS

    assert runs[1].status == STATUS_ANALYSIS  # not locked — data wasn't ready


# --- MopupCreatePlanView -----------------------------------------------------


def test_create_plan_requires_login(client):
    resp = client.post(reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code in (302, 401, 403)


def test_create_plan_returns_404_for_missing_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 999}))
    assert resp.status_code == 404


def test_create_plan_requires_locked_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)  # status is STATUS_ANALYSIS, not locked
    resp = client.post(reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 400
    assert "Lock the run" in resp.json()["detail"]


def test_create_plan_calls_handoff_and_returns_its_response(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    from connect_labs.mopup.core.models import STATUS_LOCKED

    run = _seed_run(runs)
    run.data["status"] = STATUS_LOCKED
    run.data["candidate_work_areas"] = [{"wa_id": "wa-1"}]

    import connect_labs.mopup.views as views_module

    calls = []

    def fake_handoff(run_arg, program_id, *, request=None, grouping=None, group_id=None, include_planning_gaps=False):
        calls.append((run_arg.id, program_id, grouping, group_id))
        return {"plan_id": 42, "plan_status": "draft", "urls": {"review": "/microplans/program/217/plan/42/review/"}}

    monkeypatch.setattr(views_module, "create_plan_from_locked_run", fake_handoff)
    resp = client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps({"group_id": 7}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["plan_id"] == 42
    assert calls == [(1, 217, None, 7)]


def test_create_plan_passes_include_planning_gaps_through(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    from connect_labs.mopup.core.models import STATUS_LOCKED

    run = _seed_run(runs)
    run.data["status"] = STATUS_LOCKED
    run.data["candidate_work_areas"] = [{"wa_id": "wa-1"}]

    import connect_labs.mopup.views as views_module

    calls = []

    def fake_handoff(run_arg, program_id, *, request=None, grouping=None, group_id=None, include_planning_gaps=False):
        calls.append(include_planning_gaps)
        return {"plan_id": 42, "plan_status": "draft", "urls": {}}

    monkeypatch.setattr(views_module, "create_plan_from_locked_run", fake_handoff)

    client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps({"include_planning_gaps": True}),
        content_type="application/json",
    )
    assert calls == [True]

    client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps({}),
        content_type="application/json",
    )
    assert calls == [True, False]


def test_create_plan_handoff_error_is_400(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    from connect_labs.mopup.core.models import STATUS_LOCKED

    run = _seed_run(runs)
    run.data["status"] = STATUS_LOCKED
    run.data["candidate_work_areas"] = [{"wa_id": "wa-1"}]

    import connect_labs.mopup.views as views_module
    from connect_labs.mopup.core.handoff import HandoffError

    def fake_handoff(*a, **k):
        raise HandoffError("no boundary geometry")

    monkeypatch.setattr(views_module, "create_plan_from_locked_run", fake_handoff)
    resp = client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no boundary geometry"
