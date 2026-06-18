"""
Unit tests for SolicitationCreateView.get_context_data when driven from a
micro-plan snapshot (?source_program_id=&source_group_id= or &source_plan_id=).

Uses RequestFactory to exercise get_context_data directly, avoiding the heavy
ManagerRequiredMixin + labs OAuth setup needed for a full client.get().

Both build_plan_snapshot and ProgramPlanDataAccess are monkeypatched:
- build_plan_snapshot is imported at module scope into sviews — patch sviews.build_plan_snapshot.
- ProgramPlanDataAccess is constructed inside _snapshot_from_query; patch it via its
  module so the real constructor (which requires OAuth) is never called.
"""
import json
from unittest.mock import MagicMock

from django.test import RequestFactory

import commcare_connect.microplans.core.data_access as da_mod
import commcare_connect.solicitations.views as sviews
from commcare_connect.solicitations.views import SolicitationCreateView


def _fake_snapshot(da, *, group_id=None, plan_id=None):
    return {
        "plans": [
            {
                "plan_id": 7,
                "name": "Ikorodu",
                "region": "Lagos",
                "wards": ["North"],
                "work_area_count": 3,
            }
        ],
        "source_program_id": 25,
        "source_group_id": group_id,
        "source_plan_ids": [7],
        "suggested_title": "Solicitation for Lagos Study",
        "suggested_scope": "Coverage areas drawn from plan group ...",
    }


def _make_view(url):
    req = RequestFactory().get(url)
    req.labs_context = {}
    view = SolicitationCreateView()
    view.setup(req)
    return view


def test_get_context_seeds_form_from_group(monkeypatch):
    monkeypatch.setattr(sviews, "build_plan_snapshot", _fake_snapshot)
    monkeypatch.setattr(da_mod, "ProgramPlanDataAccess", MagicMock(return_value=MagicMock()))
    view = _make_view("/solicitations/create/?source_program_id=25&source_group_id=88")
    ctx = view.get_context_data()

    assert ctx["form"].initial["title"] == "Solicitation for Lagos Study"
    assert ctx["form"].initial["scope_of_work"] == "Coverage areas drawn from plan group ..."
    assert ctx["snapshot_plans"][0]["name"] == "Ikorodu"
    assert json.loads(ctx["form"].initial["plans_json"])[0]["plan_id"] == 7
    assert ctx["form"].initial["source_program_id"] == 25
    assert ctx["form"].initial["source_group_id"] == 88
    assert json.loads(ctx["form"].initial["source_plan_ids_json"]) == [7]
    assert ctx["is_create"] is True
    assert ctx["existing_questions"] == []
    assert ctx["existing_criteria"] == []


def test_get_context_seeds_form_from_single_plan(monkeypatch):
    monkeypatch.setattr(sviews, "build_plan_snapshot", _fake_snapshot)
    monkeypatch.setattr(da_mod, "ProgramPlanDataAccess", MagicMock(return_value=MagicMock()))
    view = _make_view("/solicitations/create/?source_program_id=25&source_plan_id=7")
    ctx = view.get_context_data()

    assert ctx["form"].initial["title"] == "Solicitation for Lagos Study"
    assert ctx["snapshot_plans"][0]["name"] == "Ikorodu"
    # source_group_id is None when only source_plan_id is provided
    assert ctx["form"].initial["source_group_id"] is None
    assert ctx["form"].initial["source_program_id"] == 25


def test_get_context_no_source_params_blank_form(monkeypatch):
    # Without params, snapshot helper should NOT be called at all
    called = []
    monkeypatch.setattr(sviews, "build_plan_snapshot", lambda *a, **kw: called.append(True) or {})
    view = _make_view("/solicitations/create/")
    ctx = view.get_context_data()

    assert ctx["snapshot_plans"] == []
    assert not ctx["form"].initial.get("plans_json")
    assert not called, "build_plan_snapshot should not be called when no source params"


def test_get_context_missing_group_and_plan_id_no_snapshot(monkeypatch):
    """program_id alone (no group_id or plan_id) → blank form."""
    called = []
    monkeypatch.setattr(sviews, "build_plan_snapshot", lambda *a, **kw: called.append(True) or {})
    view = _make_view("/solicitations/create/?source_program_id=25")
    ctx = view.get_context_data()

    assert ctx["snapshot_plans"] == []
    assert not called


def test_get_context_snapshot_exception_falls_back_to_blank(monkeypatch):
    """If build_plan_snapshot raises, the view silently falls back to a blank form."""

    def _boom(da, *, group_id=None, plan_id=None):
        raise RuntimeError("network error")

    monkeypatch.setattr(sviews, "build_plan_snapshot", _boom)
    monkeypatch.setattr(da_mod, "ProgramPlanDataAccess", MagicMock(return_value=MagicMock()))
    view = _make_view("/solicitations/create/?source_program_id=25&source_group_id=88")
    ctx = view.get_context_data()

    # Should NOT raise — graceful fallback
    assert ctx["snapshot_plans"] == []
    assert not ctx["form"].initial.get("plans_json")
