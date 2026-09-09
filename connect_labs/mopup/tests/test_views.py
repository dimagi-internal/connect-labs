"""View tests for the mop-up Phase 1 setup flow — auth gate, request
validation, and the JSON envelopes, with the data-access/work-area layers
mocked out (mirrors microplans/tests/test_views.py's style)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
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


def test_candidates_returns_per_indicator_trigger_counts(client, django_user_model, monkeypatch):
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
                "approved_hsd_count": 1,  # fails EVC shortfall (default threshold 0.5)
                "approved_ncf_count": 0,
                "approved_inaccessible_count": 0,
                "deworming_given": 1,  # passes deworming (default threshold 0.7)
                "muac_given": 1,
                "vaccination_given": 1,
            }
        ],
    )
    from connect_labs.mopup.core import indicators as ind

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps(
            {
                "indicator_configs": {
                    ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY},
                    ind.DEWORMING: {"enabled": True, "threshold": 0.7, "granularity": ind.GRANULARITY_WA_ONLY},
                }
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    counts = resp.json()["per_indicator_counts"]
    assert counts[ind.EVC_SHORTFALL] == 1
    assert counts[ind.DEWORMING] == 0
    # Every known indicator key is present even at zero, so the UI can render
    # a count column for every row without a KeyError.
    assert set(counts) == set(ind.ALL_INDICATORS)


def test_candidates_returns_map_features_for_the_map(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    boundary = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
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
                "boundary": boundary,
            }
        ],
    )
    from connect_labs.mopup.core import indicators as ind

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps(
            {
                "indicator_configs": {
                    ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY},
                }
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    features = resp.json()["map_features"]["features"]
    assert len(features) == 1
    assert features[0]["geometry"] == boundary
    assert features[0]["properties"]["included"] is True
    assert features[0]["properties"]["first_indicator"] == ind.EVC_SHORTFALL


def test_candidates_includes_gap_candidates_already_stored_on_the_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    gap_boundary = {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]}
    run.data["planning_gap_features"] = [
        {
            "type": "Feature",
            "geometry": gap_boundary,
            "properties": {
                "cluster": "mopup-x-gap-C0",
                "ward": "Sabon Gari",
                "lga": "Rano",
                "state": "Kano",
                "building_count": 4,
                "expected_visit_count": 9,
            },
        }
    ]
    _mock_ready_data(monkeypatch, run, [])

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert len(body["gap_candidates"]) == 1
    assert body["gap_candidates"][0]["wa_id"] == "mopup-x-gap-C0"
    assert body["gap_candidates"][0]["building_count"] == 4
    map_sources = {f["properties"]["source"] for f in body["map_features"]["features"]}
    assert "planning_gap" in map_sources


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


def test_analysis_view_embeds_ward_boundaries_and_mapbox_token(client, django_user_model, monkeypatch, settings):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    run.data["selected_wards"] = [{"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"}]

    boundary_geojson = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    fake_boundary = SimpleNamespace(source="geopode", geometry=SimpleNamespace(geojson=json.dumps(boundary_geojson)))
    monkeypatch.setattr(
        "connect_labs.microplans.core.admin_boundaries.find_ward_boundary",
        lambda state, lga, ward: fake_boundary,
    )
    settings.MAPBOX_TOKEN = "testtoken123"

    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 200
    assert b"testtoken123" in resp.content
    assert b"Sabon Gari" in resp.content
    assert b"GeoPoDe" in resp.content
    assert b"pending native Connect boundary support" in resp.content


def test_analysis_view_caption_lists_each_distinct_source_once(client, django_user_model, monkeypatch, settings):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    run.data["selected_wards"] = [
        {"ward": "Sabon Gari", "lga": "Rano", "state": "Kano"},
        {"ward": "Fagge", "lga": "Fagge", "state": "Kano"},
        {"ward": "Nassarawa", "lga": "Nassarawa", "state": "Kano"},
    ]

    boundary_geojson = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    by_ward = {
        "Sabon Gari": SimpleNamespace(
            source="geopode", geometry=SimpleNamespace(geojson=json.dumps(boundary_geojson))
        ),
        "Fagge": SimpleNamespace(source="geopode", geometry=SimpleNamespace(geojson=json.dumps(boundary_geojson))),
        "Nassarawa": SimpleNamespace(
            source="overture", geometry=SimpleNamespace(geojson=json.dumps(boundary_geojson))
        ),
    }
    monkeypatch.setattr(
        "connect_labs.microplans.core.admin_boundaries.find_ward_boundary",
        lambda state, lga, ward: by_ward.get(ward),
    )
    settings.MAPBOX_TOKEN = "testtoken123"

    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 200
    # Two distinct sources across three wards -> the caption names each once,
    # not once per ward (three wards, two sources).
    body = resp.content.decode()
    assert "Boundary source: GeoPoDe / WHO (wards + pop), Overture" in body
    assert body.count("Boundary source:") == 1


def test_analysis_view_skips_wards_with_no_boundary_match(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_run(runs)
    run.data["selected_wards"] = [{"ward": "Nowhere", "lga": "Rano", "state": "Kano"}]

    monkeypatch.setattr(
        "connect_labs.microplans.core.admin_boundaries.find_ward_boundary",
        lambda state, lga, ward: None,
    )

    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 200
    assert b"pending native Connect boundary support" not in resp.content


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


def _seed_locked_run(runs):
    from connect_labs.mopup.core.models import STATUS_LOCKED

    run = _seed_run(runs)
    run.data["status"] = STATUS_LOCKED
    run.data["candidate_work_areas"] = [{"wa_id": "wa-1"}]
    return run


def test_create_plan_dispatches_a_task_when_none_exists(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    assert run.create_plan_task_id is None

    fake_async_result = mock.Mock(id="fresh-plan-task-id")
    with mock.patch("connect_labs.mopup.tasks.create_mopup_plan.delay", return_value=fake_async_result) as delay:
        resp = client.post(
            reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
            data=json.dumps({"group_id": 7}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "pending"
    delay.assert_called_once_with(217, 1, mock.ANY, grouping=None, group_id=7)
    assert runs[1].create_plan_task_id == "fresh-plan-task-id"


def test_create_plan_polls_a_running_task(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["create_plan_task_id"] = "plan-task-in-flight"
    mock_result = mock.Mock(state="PROGRESS", info={"message": "Creating the plan…"})
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "running"
    assert body["message"] == "Creating the plan…"
    assert runs[1].create_plan_task_id == "plan-task-in-flight"


def test_create_plan_returns_the_completed_task_result_and_clears_the_task_id(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["create_plan_task_id"] = "plan-task-done"
    resp_payload = {
        "status": "ok",
        "plan_id": 42,
        "plan_status": "draft",
        "urls": {"review": "/microplans/program/217/plan/42/review/"},
    }
    mock_result = mock.Mock(state="SUCCESS", info=resp_payload)
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["plan_id"] == 42
    # A terminal state (success included) clears the task id — a LATER,
    # separate "Create mop-up plan" click must dispatch a genuinely new
    # attempt, not replay this finished one.
    assert runs[1].create_plan_task_id is None


def test_create_plan_surfaces_a_handoff_error_result_without_treating_it_as_a_celery_failure(
    client, django_user_model, monkeypatch
):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["create_plan_task_id"] = "plan-task-done"
    # create_mopup_plan catches HandoffError and returns it as a normal
    # SUCCESS-state result shaped {"status": "error", "detail": ...} — this
    # simulates exactly that (not a Celery FAILURE state).
    mock_result = mock.Mock(state="SUCCESS", info={"status": "error", "detail": "no boundary geometry"})
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json() == {"status": "error", "detail": "no boundary geometry"}
    assert runs[1].create_plan_task_id is None


def test_create_plan_surfaces_a_failed_task_and_clears_it_for_retry(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["create_plan_task_id"] = "plan-task-in-flight"
    mock_result = mock.Mock(state="FAILURE", info=RuntimeError("Connect authorization needed"))
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:create_plan", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "failed"
    assert "Connect authorization needed" in body["error"]
    assert runs[1].create_plan_task_id is None


# --- MopupPlanningGapsView ---------------------------------------------------


def test_planning_gaps_requires_login(client):
    resp = client.post(reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code in (302, 401, 403)


def test_planning_gaps_requires_locked_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)  # status is STATUS_ANALYSIS, not locked
    resp = client.post(reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 400
    assert "Lock the run" in resp.json()["detail"]


def test_planning_gaps_dispatches_a_task_with_the_given_config(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    assert run.planning_gap_task_id is None

    fake_async_result = mock.Mock(id="fresh-gap-task-id")
    with mock.patch("connect_labs.mopup.tasks.preview_planning_gaps.delay", return_value=fake_async_result) as delay:
        resp = client.post(
            reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}),
            data=json.dumps(
                {
                    "building_sources": ["Google Open Buildings"],
                    "min_confidence": 0.6,
                    "min_buildings_per_cell": 2,
                    "cell_size_m": 50.0,
                }
            ),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "pending"
    delay.assert_called_once_with(
        217,
        1,
        mock.ANY,
        mode="overture",
        building_sources=["Google Open Buildings"],
        min_confidence=0.6,
        min_buildings_per_cell=2,
        cell_size_m=50.0,
        csv_storage_key=None,
    )


def test_planning_gaps_upload_mode_requires_an_uploaded_file(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_locked_run(runs)

    resp = client.post(
        reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}),
        data=json.dumps({"mode": "upload"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "Upload a building-data file" in resp.json()["detail"]


def test_planning_gaps_upload_mode_dispatches_with_the_stored_key(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["uploaded_buildings_key"] = "mopup/uploads/run-1/abc123.csv"

    fake_async_result = mock.Mock(id="fresh-gap-task-id")
    with mock.patch("connect_labs.mopup.tasks.preview_planning_gaps.delay", return_value=fake_async_result) as delay:
        resp = client.post(
            reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}),
            data=json.dumps({"mode": "upload"}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.content
    delay.assert_called_once_with(
        217,
        1,
        mock.ANY,
        mode="upload",
        building_sources=None,
        min_confidence=None,
        min_buildings_per_cell=1,
        cell_size_m=100.0,
        csv_storage_key="mopup/uploads/run-1/abc123.csv",
    )
    assert runs[1].planning_gap_task_id == "fresh-gap-task-id"


def test_planning_gaps_polls_a_running_task(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["planning_gap_task_id"] = "gap-task-in-flight"
    mock_result = mock.Mock(state="PROGRESS", info={"message": "Checking planning gaps for Sabon Gari (1/1)…"})
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "running"
    assert runs[1].planning_gap_task_id == "gap-task-in-flight"


def test_planning_gaps_persists_the_completed_result_onto_the_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["planning_gap_task_id"] = "gap-task-done"
    gap_feature = {"type": "Feature", "properties": {"ward": "Sabon Gari"}}
    result_payload = {
        "status": "ok",
        "features": [gap_feature],
        "cells_added": 1,
        "warnings": {"Other Ward": "no ward boundary match — skipped"},
        "config": {
            "building_sources": None,
            "min_confidence": None,
            "min_buildings_per_cell": 1,
            "cell_size_m": 100.0,
        },
    }
    mock_result = mock.Mock(state="SUCCESS", info=result_payload)
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["cells_added"] == 1
    # Persisted onto the run so a later Create-plan hand-off carries it
    # forward without recomputing -- no separate "lock" step for Step 2.
    assert runs[1].planning_gap_task_id is None
    assert runs[1].planning_gap_features == [gap_feature]
    assert runs[1].planning_gap_config == result_payload["config"]
    assert runs[1].planning_gap_warnings == {"Other Ward": "no ward boundary match — skipped"}


def test_planning_gaps_surfaces_a_failed_task_and_clears_it_for_retry(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    run.data["planning_gap_task_id"] = "gap-task-in-flight"
    mock_result = mock.Mock(state="FAILURE", info=RuntimeError("CommCare HQ authorization needed"))
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: mock_result)

    resp = client.post(
        reverse("mopup:planning_gaps", kwargs={"program_id": 217, "run_id": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "failed"
    assert "CommCare HQ authorization needed" in body["error"]
    assert runs[1].planning_gap_task_id is None


# --- MopupUploadBuildingsView --------------------------------------------------


def _csv_upload(name="buildings.csv", content=None):
    from django.core.files.uploadedfile import SimpleUploadedFile

    if content is None:
        content = (
            "latitude,longitude,area_in_meters,confidence,wardname,lganame,statename\n"
            "11.09,11.33,14.35,0.66,Nafada Central,Nafada,Gombe\n"
        )
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


def test_upload_buildings_requires_login(client):
    resp = client.post(reverse("mopup:upload_buildings", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code in (302, 401, 403)


def test_upload_buildings_requires_locked_run(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_run(runs)  # not locked
    resp = client.post(
        reverse("mopup:upload_buildings", kwargs={"program_id": 217, "run_id": 1}), {"file": _csv_upload()}
    )
    assert resp.status_code == 400
    assert "Lock the run" in resp.json()["detail"]


def test_upload_buildings_rejects_non_csv_extension(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_locked_run(runs)
    resp = client.post(
        reverse("mopup:upload_buildings", kwargs={"program_id": 217, "run_id": 1}),
        {"file": _csv_upload(name="buildings.txt")},
    )
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


def test_upload_buildings_rejects_missing_required_columns(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_locked_run(runs)
    resp = client.post(
        reverse("mopup:upload_buildings", kwargs={"program_id": 217, "run_id": 1}),
        {"file": _csv_upload(content="latitude,longitude\n11.09,11.33\n")},
    )
    assert resp.status_code == 400
    assert "missing required column" in resp.json()["detail"]
    for col in ("wardname", "lganame", "statename"):
        assert col in resp.json()["detail"]


def test_upload_buildings_rejects_oversized_file(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    _seed_locked_run(runs)
    from connect_labs.mopup import views as views_module

    monkeypatch.setattr(views_module, "_MAX_UPLOAD_BYTES", 10)
    resp = client.post(
        reverse("mopup:upload_buildings", kwargs={"program_id": 217, "run_id": 1}), {"file": _csv_upload()}
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"]


def test_upload_buildings_success_persists_key_and_filename(
    client, django_user_model, monkeypatch, settings, tmp_path
):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    run = _seed_locked_run(runs)
    settings.MEDIA_ROOT = str(tmp_path)

    resp = client.post(
        reverse("mopup:upload_buildings", kwargs={"program_id": 217, "run_id": 1}),
        {"file": _csv_upload(name="rct_wards_buildings.csv")},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["filename"] == "rct_wards_buildings.csv"
    assert run.uploaded_buildings_filename == "rct_wards_buildings.csv"
    assert run.uploaded_buildings_key
    assert run.uploaded_buildings_key.startswith("mopup/uploads/run-1/")

    from django.core.files.storage import default_storage

    assert default_storage.exists(run.uploaded_buildings_key)
