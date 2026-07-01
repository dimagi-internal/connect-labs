"""Tests for the generic "run a workflow in default mode" seam.

Covers:
- `run_default_for_definition` raises for a template that doesn't opt into
  default-run.
- The `weekly_dual_track_audit` creator's `run_default` hook: creates a run +
  fires the batch job when none exists for the window; idempotent (reuses an
  existing run for the same window, doesn't re-fire).
- `resolve_window("last_week", today)` (mirrors the render's calculateDateRange).

The program-wide fan-out (formerly on the `audit_par` report) now lives on the
`program_audit_creator` template — see test_program_audit_creator.py.
- The generic management command + API endpoint wrappers call the dispatcher.
"""

import json
from datetime import date
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.test import RequestFactory


def _run(run_id, window_start):
    r = mock.Mock()
    r.id = run_id
    r.data = {"state": {"window_start": window_start}} if window_start else {"state": {}}
    return r


def _creator_def(opp_id=1973, def_id=42):
    """A `weekly_dual_track_audit` creator definition (mock proxy)."""
    d = mock.Mock()
    d.template_type = "weekly_dual_track_audit"
    d.id = def_id
    d.opportunity_id = opp_id
    d.opportunity_ids = [opp_id]
    d.data = {
        "config": {
            "templateType": "weekly_dual_track_audit",
            "audit_batch": {
                "track_a": {"tag": "muac", "sample_percentage": 100},
                "track_b": {"tag": "rest", "sample_percentage": 10},
            },
        }
    }
    return d


# ── Dispatcher: unsupported template ─────────────────────────────────────────


def test_run_default_for_definition_raises_when_unsupported():
    from connect_labs.workflow.templates import run_default_for_definition

    d = mock.Mock()
    d.template_type = "not_a_real_template"
    d.id = 7
    d.data = {"config": {}}

    with pytest.raises(ValueError):
        run_default_for_definition(d, access_token="t")


# ── Creator run_default: create + fire, and idempotency ──────────────────────


def test_creator_run_default_creates_and_fires_job(monkeypatch):
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []  # no existing run for the window
        wda.create_run.return_value = _run(1234, None)
        return wda

    monkeypatch.setattr(g, "WorkflowDataAccess", make_wda)
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 5}
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = run_default_for_definition(_creator_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    assert result == {"run_id": 1234, "created": True, "sessions_created": 5}
    assert fake_job.apply.call_count == 1

    # Full 4-arg job contract (job_config, access_token, run_id, opportunity_id),
    # confirmed against run_workflow_job's signature.
    kw = fake_job.apply.call_args.kwargs["kwargs"]
    assert kw["run_id"] == 1234
    assert kw["opportunity_id"] == 1973
    assert kw["access_token"] == "t"
    assert kw["job_config"]["job_type"] == "weekly_dual_track_audit_create"
    assert kw["job_config"]["window_start"] == "2026-06-21"
    # Sampling comes from the definition's config defaults.
    assert kw["job_config"]["muac_sample_percentage"] == 100
    assert kw["job_config"]["other_sample_percentage"] == 10


def test_creator_run_default_is_idempotent_per_window(monkeypatch):
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    wda = mock.Mock()
    wda.list_runs.return_value = [_run(500, "2026-06-21")]
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = run_default_for_definition(_creator_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    assert result == {"run_id": 500, "created": False, "sessions_created": 0}
    wda.create_run.assert_not_called()
    fake_job.apply.assert_not_called()


def test_creator_run_default_defaults_window_to_last_week(monkeypatch):
    """With no explicit window, the hook resolves last_week and creates a run."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    captured = {}

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []

        def _create(def_id, opp_id, ws, we, initial_state=None):
            captured["window"] = (ws, we)
            return _run(9, None)

        wda.create_run.side_effect = _create
        return wda

    monkeypatch.setattr(g, "WorkflowDataAccess", make_wda)
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 0}
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    run_default_for_definition(_creator_def(), access_token="t")

    ws, we = captured["window"]
    assert ws < we  # a concrete resolved window, not empty


# ── resolve_window ───────────────────────────────────────────────────────────


def test_resolve_window_last_week():
    from connect_labs.workflow.audit_generation import resolve_window

    # Wed 2026-07-01 → previous full Sun–Sat = 2026-06-21 .. 2026-06-27.
    start, end = resolve_window("last_week", date(2026, 7, 1))
    assert start == "2026-06-21"
    assert end == "2026-06-27"


def test_resolve_window_unknown_preset_raises():
    from connect_labs.workflow.audit_generation import resolve_window

    with pytest.raises(ValueError):
        resolve_window("not_a_preset", date(2026, 7, 1))


# ── Management command: run_workflow_default ──────────────────────────────────


def test_management_command_runs_default(monkeypatch):
    from connect_labs.workflow.management.commands import run_workflow_default as cmd

    creator = mock.Mock()
    wda = mock.Mock()
    wda.get_definition.return_value = creator
    monkeypatch.setattr(cmd, "WorkflowDataAccess", mock.Mock(return_value=wda))

    captured = {}

    def fake_dispatch(defn, *, access_token, **kw):
        captured["defn"] = defn
        captured["access_token"] = access_token
        captured.update(kw)
        return {"run_id": 5, "created": True, "sessions_created": 2}

    monkeypatch.setattr(cmd, "run_default_for_definition", fake_dispatch)

    out = StringIO()
    call_command(
        "run_workflow_default",
        "--definition",
        "42",
        "--opportunity",
        "1973",
        "--token",
        "svc-tok",
        stdout=out,
    )

    assert captured["defn"] is creator
    assert captured["access_token"] == "svc-tok"
    assert '"run_id": 5' in out.getvalue()


def test_management_command_forwards_window_preset(monkeypatch):
    from connect_labs.workflow.management.commands import run_workflow_default as cmd

    wda = mock.Mock()
    wda.get_definition.return_value = mock.Mock()
    monkeypatch.setattr(cmd, "WorkflowDataAccess", mock.Mock(return_value=wda))

    captured = {}

    def fake_dispatch(defn, *, access_token, **kw):
        captured.update(kw)
        return {}

    monkeypatch.setattr(cmd, "run_default_for_definition", fake_dispatch)

    call_command(
        "run_workflow_default",
        "--definition",
        "42",
        "--opportunity",
        "1973",
        "--window",
        "last_week",
        "--token",
        "t",
        stdout=StringIO(),
    )

    ws, we = captured["window"]
    assert ws < we


# ── API endpoint: run_default_api ─────────────────────────────────────────────


def _api_req(body=None):
    rf = RequestFactory()
    req = rf.post(
        "/labs/workflow/api/42/run-default/",
        data=json.dumps(body or {}),
        content_type="application/json",
    )
    req.session = {"labs_oauth": {"access_token": "tok"}}
    req.labs_context = {"opportunity_id": 1973}
    req.user = mock.Mock(is_authenticated=True, is_staff=True, username="jj")
    return req


def test_api_run_default_returns_result(monkeypatch):
    from connect_labs.workflow import templates as templates_pkg
    from connect_labs.workflow import views as m

    definition = mock.Mock()
    wda = mock.Mock()
    wda.get_definition.return_value = definition
    monkeypatch.setattr(m, "WorkflowDataAccess", mock.Mock(return_value=wda))
    monkeypatch.setattr(
        templates_pkg,
        "run_default_for_definition",
        mock.Mock(return_value={"per_opp": {1973: {"run_id": 9, "created": True, "sessions_created": 4}}}),
    )

    resp = m.run_default_api(_api_req(), 42)

    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert payload["per_opp"]["1973"]["run_id"] == 9


def test_api_run_default_400_when_unsupported(monkeypatch):
    from connect_labs.workflow import templates as templates_pkg
    from connect_labs.workflow import views as m

    definition = mock.Mock()
    wda = mock.Mock()
    wda.get_definition.return_value = definition
    monkeypatch.setattr(m, "WorkflowDataAccess", mock.Mock(return_value=wda))

    def raise_unsupported(*a, **k):
        raise ValueError("Workflow 42 (template 'audit_with_ai_review') does not support default-run.")

    monkeypatch.setattr(templates_pkg, "run_default_for_definition", raise_unsupported)

    resp = m.run_default_api(_api_req(), 42)
    assert resp.status_code == 400
    assert "default-run" in json.loads(resp.content)["error"]


def test_api_run_default_404_when_definition_missing(monkeypatch):
    from connect_labs.workflow import views as m

    wda = mock.Mock()
    wda.get_definition.return_value = None
    monkeypatch.setattr(m, "WorkflowDataAccess", mock.Mock(return_value=wda))

    resp = m.run_default_api(_api_req(), 42)
    assert resp.status_code == 404
