"""Tests for the program audit-batch generation seam (Tasks 4 & 5).

Covers:
- `resolve_program_audit_instances` (Phase-1 explicit mapping)
- `generate_program_audit_batches` idempotency (reuse a run whose
  state.window_start matches) and per-opp fan-out (one run + job per opp).
- `resolve_window("last_week", today)` (mirrors the render's calculateDateRange).
- The thin management command + API endpoint wrappers call the callable.
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


# ── Task 4: resolve_program_audit_instances ──────────────────────────────────


def test_resolve_program_audit_instances_returns_mapping():
    from commcare_connect.workflow import audit_generation as g

    mapping = [{"opportunity_id": 1973, "definition_id": 42}]
    out = g.resolve_program_audit_instances(176, access_token="t", mapping=mapping)
    assert out == mapping
    # Defensive: no mapping → empty list (nothing to fan out to).
    assert g.resolve_program_audit_instances(176, access_token="t") == []


# ── Task 4: generate_program_audit_batches ───────────────────────────────────


def test_generate_reuses_existing_run_for_window(monkeypatch):
    from commcare_connect.workflow import audit_generation as g

    wda = mock.Mock()
    wda.list_runs.return_value = [_run(500, "2026-06-21")]
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = g.generate_program_audit_batches(
        176,
        "2026-06-21",
        "2026-06-27",
        access_token="t",
        mapping=[{"opportunity_id": 1973, "definition_id": 42}],
    )

    assert result["per_opp"][1973]["created"] is False
    assert result["per_opp"][1973]["run_id"] == 500
    wda.create_run.assert_not_called()
    fake_job.apply.assert_not_called()


def test_generate_creates_run_and_fires_job_per_opp(monkeypatch):
    from commcare_connect.workflow import audit_generation as g

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []  # no existing run for the window
        wda.create_run.return_value = _run(1000 + opportunity_id, None)
        return wda

    monkeypatch.setattr(g, "WorkflowDataAccess", make_wda)
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 3}
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = g.generate_program_audit_batches(
        176,
        "2026-06-21",
        "2026-06-27",
        access_token="t",
        mapping=[
            {"opportunity_id": 1973, "definition_id": 42},
            {"opportunity_id": 1976, "definition_id": 43},
        ],
    )

    assert set(result["per_opp"].keys()) == {1973, 1976}
    assert all(v["run_id"] for v in result["per_opp"].values())
    assert all(v["created"] for v in result["per_opp"].values())
    assert result["per_opp"][1973]["sessions_created"] == 3
    assert fake_job.apply.call_count == 2

    # The job runner gets the full 4-arg contract (job_config, access_token,
    # run_id, opportunity_id) — confirmed against run_workflow_job's signature.
    first_kwargs = fake_job.apply.call_args_list[0].kwargs["kwargs"]
    assert first_kwargs["run_id"] == 1000 + 1973
    assert first_kwargs["opportunity_id"] == 1973
    assert first_kwargs["access_token"] == "t"
    assert first_kwargs["job_config"]["job_type"] == "weekly_dual_track_audit_create"
    assert first_kwargs["job_config"]["window_start"] == "2026-06-21"


def test_generate_passes_sample_overrides(monkeypatch):
    from commcare_connect.workflow import audit_generation as g

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []
        wda.create_run.return_value = _run(2000, None)
        return wda

    monkeypatch.setattr(g, "WorkflowDataAccess", make_wda)
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 0}
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    g.generate_program_audit_batches(
        176,
        "2026-06-21",
        "2026-06-27",
        sample_overrides={"muac_sample_percentage": 100, "other_sample_percentage": 10},
        access_token="t",
        mapping=[{"opportunity_id": 1973, "definition_id": 42}],
    )

    job_config = fake_job.apply.call_args_list[0].kwargs["kwargs"]["job_config"]
    assert job_config["muac_sample_percentage"] == 100
    assert job_config["other_sample_percentage"] == 10


# ── Task 5: resolve_window ────────────────────────────────────────────────────


def test_resolve_window_last_week():
    from commcare_connect.workflow.audit_generation import resolve_window

    # Wed 2026-07-01 → previous full Sun–Sat = 2026-06-21 .. 2026-06-27.
    start, end = resolve_window("last_week", date(2026, 7, 1))
    assert start == "2026-06-21"
    assert end == "2026-06-27"


def test_resolve_window_unknown_preset_raises():
    from commcare_connect.workflow.audit_generation import resolve_window

    with pytest.raises(ValueError):
        resolve_window("not_a_preset", date(2026, 7, 1))


# ── Task 5: management command ────────────────────────────────────────────────


def test_management_command_invokes_callable(monkeypatch):
    from commcare_connect.workflow import audit_generation as g

    captured = {}

    def fake_generate(program_id, window_start, window_end, **kwargs):
        captured.update(program_id=program_id, window_start=window_start, window_end=window_end, **kwargs)
        return {"per_opp": {1973: {"run_id": 5, "created": True, "sessions_created": 2}}}

    monkeypatch.setattr(g, "generate_program_audit_batches", fake_generate)

    out = StringIO()
    call_command(
        "generate_program_audit_batches",
        "--program",
        "176",
        "--start",
        "2026-06-21",
        "--end",
        "2026-06-27",
        "--token",
        "svc-tok",
        "--mapping",
        json.dumps([{"opportunity_id": 1973, "definition_id": 42}]),
        stdout=out,
    )

    assert captured["program_id"] == 176
    assert captured["window_start"] == "2026-06-21"
    assert captured["window_end"] == "2026-06-27"
    assert captured["access_token"] == "svc-tok"
    assert captured["mapping"] == [{"opportunity_id": 1973, "definition_id": 42}]
    assert "1973" in out.getvalue()


def test_management_command_resolves_window_preset(monkeypatch):
    from commcare_connect.workflow import audit_generation as g

    captured = {}

    def fake_generate(program_id, window_start, window_end, **kwargs):
        captured.update(window_start=window_start, window_end=window_end)
        return {"per_opp": {}}

    monkeypatch.setattr(g, "generate_program_audit_batches", fake_generate)

    out = StringIO()
    call_command(
        "generate_program_audit_batches",
        "--program",
        "176",
        "--window",
        "last_week",
        "--token",
        "svc-tok",
        stdout=out,
    )
    # A concrete window got resolved (ISO dates), not left empty.
    assert captured["window_start"] and captured["window_end"]
    assert captured["window_start"] < captured["window_end"]


# ── Task 5: API endpoint ──────────────────────────────────────────────────────


def _api_req(body):
    rf = RequestFactory()
    req = rf.post(
        "/labs/workflow/api/program/176/generate-audits/",
        data=json.dumps(body),
        content_type="application/json",
    )
    req.session = {"labs_oauth": {"access_token": "tok"}}
    req.labs_context = {"program_id": 176}
    req.user = mock.Mock(is_authenticated=True, is_staff=True, username="jj")
    return req


def test_api_endpoint_returns_per_opp_json():
    from commcare_connect.workflow import audit_generation as g
    from commcare_connect.workflow import views as m

    body = {
        "program_id": 176,
        "start": "2026-06-21",
        "end": "2026-06-27",
        "mapping": [{"opportunity_id": 1973, "definition_id": 42}],
    }
    req = _api_req(body)

    with mock.patch.object(
        g,
        "generate_program_audit_batches",
        return_value={"per_opp": {1973: {"run_id": 9, "created": True, "sessions_created": 4}}},
    ) as gen:
        resp = m.generate_program_audit_batches_api(req, 176)

    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert payload["per_opp"]["1973"]["run_id"] == 9
    # Token pulled from the labs session; program id from the URL.
    assert gen.call_args.kwargs["access_token"] == "tok"
    assert gen.call_args.args[0] == 176


def test_api_endpoint_400_on_missing_window():
    from commcare_connect.workflow import views as m

    req = _api_req({"program_id": 176})
    resp = m.generate_program_audit_batches_api(req, 176)
    assert resp.status_code == 400


def test_api_endpoint_resolves_window_preset():
    from commcare_connect.workflow import audit_generation as g
    from commcare_connect.workflow import views as m

    req = _api_req({"program_id": 176, "window": "last_week"})
    with mock.patch.object(g, "generate_program_audit_batches", return_value={"per_opp": {}}) as gen:
        resp = m.generate_program_audit_batches_api(req, 176)

    assert resp.status_code == 200
    # A concrete window was resolved and forwarded.
    assert gen.call_args.kwargs["window_start"] or gen.call_args.args[1]
