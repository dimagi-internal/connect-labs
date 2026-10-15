"""WA Revisit R2's pages and endpoints: its own home and setup, run creation
with the R1 opportunity, and the per-run views evaluating R2's rules instead
of R1's indicators. Same fakes as test_views.py."""

from __future__ import annotations

import pytest
from django.urls import reverse

from connect_labs.mopup.core import r2
from connect_labs.mopup.core.models import STATUS_ANALYSIS, MopupRunRecord
from connect_labs.mopup.tests.test_views import _login, _make_fake_run_da, _mock_ready_data

pytestmark = pytest.mark.django_db

OPPS = [
    {"id": 2156, "name": "CHC - NG - ISODAF - RCT", "program": 217, "is_active": True},
    {"id": 2291, "name": "CHC - NG - ISODAF - R1", "program": 217, "is_active": True},
]


@pytest.fixture(autouse=True)
def _program_opps(monkeypatch):
    monkeypatch.setattr("connect_labs.mopup.views._program_opportunities", lambda request, program_id: OPPS)
    monkeypatch.setattr("connect_labs.mopup.views.fetch_connect_implementation_areas", lambda *a, **k: [])


def _run(run_id, *, round_, target=2156, r1=None, status=STATUS_ANALYSIS, thresholds=None):
    return MopupRunRecord(
        {
            "id": run_id,
            "experiment": "217",
            "type": "mopup_run",
            "opportunity_id": None,
            "program_id": 217,
            "data": {
                "status": status,
                "name": f"run {run_id}",
                "round": round_,
                "r1_opportunity_id": r1,
                "target_opportunity_id": target,
                "selected_wards": [],
                "thresholds": thresholds or {},
                "candidate_work_areas": [],
                "created_at": f"2026-10-0{run_id}T00:00:00+00:00",
            },
        }
    )


def _wa(wa_id, **overrides):
    row = {
        "wa_id": wa_id,
        "wa_name": wa_id,
        "ward": "Kushi",
        "lga": "Shongom",
        "state": "Gombe",
        "flw_username": "flw-1",
        "owner_username": "flw-1",
        "lat": None,
        "lon": None,
        "status": "VISITED",
        "connect_status": "VISITED",
        "building_count": 3,
        "expected_visit_count": 6,
        "approved_hsd_count": 2,
        "approved_ncf_count": 0,
        "connect_inaccessible": 0,
        "deworming_given": 0,
        "muac_given": 0,
        "vaccination_given": 0,
        "rejected_hsd_count": 0,
        "rejected_ncf_count": 0,
        "owner_suspended": 0,
        "in_r1": 0,
    }
    row.update(overrides)
    return row


def test_r2_home_lists_only_r2_runs_and_r1_home_hides_them(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r1"), 2: _run(2, round_="r2", r1=2291)})

    r2_home = client.get(reverse("mopup:r2_program_home", kwargs={"program_id": 217}))
    assert r2_home.status_code == 200
    assert [r["id"] for r in r2_home.context["runs"]] == [2]
    assert r2_home.context["runs"][0]["r1_opportunity_name"] == "CHC - NG - ISODAF - R1"
    assert b"WA Revisit R2" in r2_home.content
    assert r2_home.context["setup_url"] == reverse("mopup:r2_setup", kwargs={"program_id": 217})

    r1_home = client.get(reverse("mopup:program_home", kwargs={"program_id": 217}))
    assert [r["id"] for r in r1_home.context["runs"]] == [1]


def test_r2_setup_offers_the_r1_picker_with_program_217_defaults(client, django_user_model):
    _login(client, django_user_model)
    resp = client.get(reverse("mopup:r2_setup", kwargs={"program_id": 217}))
    assert resp.status_code == 200
    assert b'id="r1-opp-select"' in resp.content
    assert resp.context["round"] == "r2"
    assert resp.context["r1_defaults"]["2156"] == 2291


def test_r1_setup_has_no_r1_picker(client, django_user_model):
    _login(client, django_user_model)
    resp = client.get(reverse("mopup:setup", kwargs={"program_id": 217}))
    assert b'id="r1-opp-select"' not in resp.content


def test_create_run_stores_round_and_r1_opportunity(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data={"opportunity_id": 2156, "wards": [], "round": "r2", "r1_opportunity_id": 2291},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    run = next(iter(runs.values()))
    assert run.is_r2 and run.r1_opportunity_id == 2291
    assert run.name == "WA Revisit R2"


def test_create_run_defaults_to_r1(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch)
    client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data={"opportunity_id": 2156, "wards": []},
        content_type="application/json",
    )
    run = next(iter(runs.values()))
    assert run.round == "r1" and run.r1_opportunity_id is None


def test_create_run_rejects_r1_opportunity_equal_to_the_reviewed_one(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data={"opportunity_id": 2156, "round": "r2", "r1_opportunity_id": 2156},
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_candidates_evaluates_r2_rules_and_leaves_out_r1(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r2", r1=2291)})
    rows = [
        _wa("suspended", owner_suspended=1),
        _wa("rejected", rejected_ncf_count=1),
        _wa("inaccessible", connect_status="INACCESSIBLE"),
        _wa("already-in-r1", owner_suspended=1, in_r1=1),
        _wa("clean"),
    ]
    _mock_ready_data(monkeypatch, runs[1], rows)
    # Celery's result carries R2's sources next to the rows.
    import celery.result

    celery.result.AsyncResult("x").info["r2_sources"] = {
        "suspended_workers": 3,
        "r1_opportunity_id": 2291,
        "r1_work_areas": 1640,
    }

    resp = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}), content_type="application/json"
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert {c["wa_id"] for c in body["candidates"]} == {"suspended", "rejected", "inaccessible"}
    assert body["per_indicator_counts"] == {r2.SUSPENDED_FLW: 1, r2.REJECTED_VISIT: 1, r2.MICROPLAN_INACCESSIBLE: 1}
    assert body["extra_counts"] == {r2.EXCLUDED_IN_R1: 1, "r1_matched": 1}
    assert body["r2_sources"]["r1_work_areas"] == 1640
    # Saved as R2 settings, not R1's indicator set.
    assert set(runs[1].thresholds["indicator_configs"]) == set(r2.R2_INDICATORS)
    assert runs[1].thresholds["global_config"] == {"exclude_r1": True}


def test_candidates_rejected_rule_off_by_default_for_eha(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r2", target=2155, r1=2305)})
    _mock_ready_data(monkeypatch, runs[1], [_wa("rejected", rejected_hsd_count=2)])
    body = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}), content_type="application/json"
    ).json()
    assert body["candidates"] == []


def test_candidates_honours_turning_off_the_r1_exclusion(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r2", r1=2291)})
    _mock_ready_data(monkeypatch, runs[1], [_wa("already-in-r1", owner_suspended=1, in_r1=1)])
    body = client.post(
        reverse("mopup:candidates", kwargs={"program_id": 217, "run_id": 1}),
        data={"global_config": {"exclude_r1": False}},
        content_type="application/json",
    ).json()
    assert [c["wa_id"] for c in body["candidates"]] == ["already-in-r1"]


def test_lock_freezes_the_r2_candidates(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    runs = _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r2", r1=2291)})
    _mock_ready_data(monkeypatch, runs[1], [_wa("suspended", owner_suspended=1), _wa("clean")])
    resp = client.post(reverse("mopup:lock", kwargs={"program_id": 217, "run_id": 1}), content_type="application/json")
    assert resp.status_code == 200, resp.content
    assert [c["wa_id"] for c in runs[1].candidate_work_areas] == ["suspended"]


def test_analysis_page_renders_r2_rules(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r2", r1=2291)})
    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert resp.status_code == 200
    assert resp.context["indicator_defs"] == r2.R2_INDICATOR_DEFS
    assert resp.context["indicator_configs"][r2.REJECTED_VISIT]["enabled"] is True  # ISODAF
    assert resp.context["global_config"] == {"exclude_r1": True}
    assert resp.context["planning_gap_config"] == {"mode": "skip"}
    assert b"WA Revisit R2" in resp.content
    assert b"CHC - NG - ISODAF - R1" in resp.content
    assert b"isR2: true" in resp.content


def test_analysis_page_for_an_r1_run_is_unchanged(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch, runs={1: _run(1, round_="r1")})
    resp = client.get(reverse("mopup:analysis", kwargs={"program_id": 217, "run_id": 1}))
    assert [d["key"] for d in resp.context["indicator_defs"]][0] == "evc_shortfall"
    assert b"isR2: false" in resp.content


def test_microplans_hub_links_to_wa_revisit_r2(client, django_user_model):
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:hub"))
    assert resp.status_code == 200
    assert b"/wa-revisit/r2/program/" in resp.content


def test_create_run_rejects_an_r1_opportunity_outside_the_program(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_run_da(monkeypatch)
    resp = client.post(
        reverse("mopup:create_run", kwargs={"program_id": 217}),
        data={"opportunity_id": 2156, "round": "r2", "r1_opportunity_id": 9999},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert b"not in this program" in resp.content
