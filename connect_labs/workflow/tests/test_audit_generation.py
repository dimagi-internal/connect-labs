"""Tests for the generic "run a workflow in default mode" seam.

Covers:
- `run_default_for_definition` raises for a template that doesn't opt into
  default-run.
- The `weekly_dual_track_audit` creator's `run_default` hook: always creates a
  fresh run and fires the batch job (no reuse — see
  `test_creator_run_default_always_creates_no_reuse`); the window it resolves
  follows the schedule's cadence (`window_preset_for_cadence`); pinned
  sampling / visit-clustering config rides through to job_config
  (`sample_overrides_for` / `clustering_overrides_for`).
- `resolve_window` presets, including `yesterday` and `last_month`.
- The generic management command + API endpoint wrappers call the dispatcher.

The program-wide fan-out (formerly on the `audit_par` report) now lives on the
`program_audit_creator` template — see test_program_audit_creator.py.
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


def _creator_def(opp_id=1973, def_id=42, program_id=None):
    """A `weekly_dual_track_audit` creator definition (mock proxy).

    ``program_id`` defaults to None (opp-owned) and must be set explicitly —
    a bare `mock.Mock()` attribute is truthy, so leaving it unset would make
    every opp-owned test silently look program-owned to `program_id_of`.
    """
    d = mock.Mock()
    d.template_type = "weekly_dual_track_audit"
    d.id = def_id
    d.opportunity_id = opp_id
    d.opportunity_ids = [opp_id]
    d.program_id = program_id
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
    fake_job.apply.return_value.successful.return_value = True
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = run_default_for_definition(_creator_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    assert result == {"run_id": 1234, "sessions_created": 5, "status": "ready"}
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


def test_creator_run_default_always_creates_no_reuse(monkeypatch):
    """Firing is an execution: it always creates a fresh run and fires the batch,
    even when a run for that window already exists. There is no reuse/dedup."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    wda = mock.Mock()
    # An existing run for this window is present — and deliberately ignored.
    wda.list_runs.return_value = [_run(500, "2026-06-21")]
    wda.create_run.return_value = _run(777, None)
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 4}
    fake_job.apply.return_value.successful.return_value = True
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = run_default_for_definition(_creator_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    # A brand-new run (777), not the pre-existing 500, and the batch was fired.
    assert result == {"run_id": 777, "sessions_created": 4, "status": "ready"}
    wda.create_run.assert_called_once()
    assert fake_job.apply.call_count == 1


def test_dispatch_batch_delays_and_returns_task_id(monkeypatch):
    """The parallel path creates a fresh run and DISPATCHES the batch job async
    (.delay), returning its task_id without waiting — the row polls that task."""
    from connect_labs.workflow import audit_generation as g

    wda = mock.Mock()
    wda.create_run.return_value = _run(321, None)
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    fake_job.delay.return_value.id = "celery-task-abc"
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = g.dispatch_batch(_creator_def(), "2026-06-21", "2026-06-27", access_token="t")

    assert result == {"run_id": 321, "task_id": "celery-task-abc", "status": "running"}
    wda.create_run.assert_called_once()
    fake_job.delay.assert_called_once()  # async dispatch, not eager .apply()
    fake_job.apply.assert_not_called()


def test_creator_run_default_scopes_program_owned_definition_by_program(monkeypatch):
    """A program-owned multi-opp instance (scheduled directly, not via the
    program creator's per-opp fan-out) must get a program-scoped
    WorkflowDataAccess and a program-owned run — not opportunity_ids[0]
    guessed as a stand-in opp. Regression for the bug where a scheduled
    program-owned weekly_dual_track_audit run silently created an opp-owned
    run instead, which then 404'd the job handler's own get_definition() and
    was invisible to the program-scoped "Open" link afterward."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    wda = mock.Mock()
    wda.list_runs.return_value = []
    wda.create_run.return_value = _run(9001, None)
    wda_factory = mock.Mock(return_value=wda)
    monkeypatch.setattr(g, "WorkflowDataAccess", wda_factory)
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 3}
    fake_job.apply.return_value.successful.return_value = True
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    run_default_for_definition(_creator_def(program_id=217), access_token="t", window=("2026-06-21", "2026-06-27"))

    # WorkflowDataAccess (both the run-creation client and run_workflow_job's
    # own scope) is program-scoped, not guessed from opportunity_ids[0].
    assert wda_factory.call_args.kwargs.get("program_id") == 217
    assert "opportunity_id" not in wda_factory.call_args.kwargs

    assert wda.create_run.call_args.kwargs.get("program_id") == 217
    assert "opportunity_id" not in wda.create_run.call_args.kwargs

    kw = fake_job.apply.call_args.kwargs["kwargs"]
    assert kw.get("program_id") == 217
    assert "opportunity_id" not in kw
    assert kw["job_config"]["program_id"] == 217
    assert "opportunity_id" not in kw["job_config"]


def test_dispatch_batch_scopes_program_owned_definition_by_program(monkeypatch):
    """Same program-scoping fix applies to the async dispatch path used by
    the program creator's fan-out and any future direct caller."""
    from connect_labs.workflow import audit_generation as g

    wda = mock.Mock()
    wda.create_run.return_value = _run(321, None)
    wda_factory = mock.Mock(return_value=wda)
    monkeypatch.setattr(g, "WorkflowDataAccess", wda_factory)
    fake_job = mock.Mock()
    fake_job.delay.return_value.id = "celery-task-def"
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = g.dispatch_batch(_creator_def(program_id=217), "2026-06-21", "2026-06-27", access_token="t")

    assert result == {"run_id": 321, "task_id": "celery-task-def", "status": "running"}
    assert wda_factory.call_args.kwargs.get("program_id") == 217
    assert fake_job.delay.call_args.kwargs.get("program_id") == 217
    assert "opportunity_id" not in fake_job.delay.call_args.kwargs


def test_creator_run_default_reports_failed_status(monkeypatch):
    """A batch job that errors is reported as status='failed' (drives per-opp recovery)."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    wda = mock.Mock()
    wda.list_runs.return_value = []
    wda.create_run.return_value = _run(801, None)
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    fake_job.apply.return_value.successful.return_value = False
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    result = run_default_for_definition(_creator_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    assert result == {"run_id": 801, "sessions_created": 0, "status": "failed"}


def test_creator_run_default_defaults_window_to_last_week(monkeypatch):
    """With no explicit window, the hook resolves last_week and creates a run."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    captured = {}

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []

        def _create(def_id, *, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None):
            captured["window"] = (period_start, period_end)
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


def test_resolve_window_yesterday():
    from connect_labs.workflow.audit_generation import resolve_window

    start, end = resolve_window("yesterday", date(2026, 7, 2))
    assert start == "2026-07-01"
    assert end == "2026-07-01"


def test_resolve_window_last_month():
    from connect_labs.workflow.audit_generation import resolve_window

    start, end = resolve_window("last_month", date(2026, 7, 15))
    assert start == "2026-06-01"
    assert end == "2026-06-30"


def test_resolve_window_today():
    from connect_labs.workflow.audit_generation import resolve_window

    start, end = resolve_window("today", date(2026, 7, 2))
    assert start == "2026-07-02"
    assert end == "2026-07-02"


# ── window_preset_for_cadence ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cadence,expected_preset",
    [
        (None, "last_week"),
        ("daily", "yesterday"),
        ("weekdays", "yesterday"),
        ("weekly", "last_week"),
        ("monthly", "last_month"),
        ("fortnightly", "last_week"),  # unrecognized non-None cadence: warn + fall back
    ],
)
def test_window_preset_for_cadence(cadence, expected_preset):
    from connect_labs.workflow.audit_generation import window_preset_for_cadence

    assert window_preset_for_cadence(cadence) == expected_preset


def test_window_preset_for_cadence_logs_on_unrecognized_cadence(caplog):
    from connect_labs.workflow.audit_generation import window_preset_for_cadence

    with caplog.at_level("WARNING"):
        window_preset_for_cadence("fortnightly")

    assert any("fortnightly" in record.message for record in caplog.records)


# ── Creator run_default: cadence-derived window + clustering overrides ──────


def _make_captured_window_wda(monkeypatch, module):
    """Patch ``module.WorkflowDataAccess`` to capture the window create_run was
    called with, returning the dict it writes into."""
    captured = {}

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []

        def _create(def_id, *, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None):
            captured["window"] = (period_start, period_end)
            return _run(9, None)

        wda.create_run.side_effect = _create
        return wda

    monkeypatch.setattr(module, "WorkflowDataAccess", make_wda)
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 0}
    fake_job.apply.return_value.successful.return_value = True
    monkeypatch.setattr(module, "run_workflow_job", fake_job)
    return captured


@pytest.mark.parametrize(
    "cadence,expected_preset",
    [
        (None, "last_week"),
        ("daily", "yesterday"),
        ("weekdays", "yesterday"),
        ("weekly", "last_week"),
        ("monthly", "last_month"),
    ],
)
def test_creator_run_default_window_follows_cadence(monkeypatch, cadence, expected_preset):
    """With no explicit window, the hook resolves the cadence-derived preset
    (daily/weekdays -> a single rolling day; weekly/no-cadence -> last_week;
    monthly -> last_month) rather than always using the fixed weekly bucket."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    captured = _make_captured_window_wda(monkeypatch, g)

    run_default_for_definition(_creator_def(), access_token="t", cadence=cadence)

    assert captured["window"] == g.resolve_window(expected_preset, date.today())


def test_creator_run_default_uses_same_day_window_when_pinned(monkeypatch):
    """A definition pinning config.audit_batch.window_mode == "same_day"
    resolves "today" instead of the cadence-derived preset -- for a schedule
    that fires late enough in the local day to see that day's own submitted
    visits, rather than waiting for a full completed prior day. This is an
    opt-in read directly off the definition, NOT a change to what "daily"
    cadence resolves to for every other schedule (program_audit_creator's
    daily schedules share window_preset_for_cadence and must be unaffected)."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    d = _creator_def()
    d.data["config"]["audit_batch"]["window_mode"] = "same_day"
    captured = _make_captured_window_wda(monkeypatch, g)

    run_default_for_definition(d, access_token="t", cadence="daily")

    assert captured["window"] == g.resolve_window("today", date.today())


def test_creator_run_default_ignores_window_mode_when_not_same_day(monkeypatch):
    """Any value other than the exact "same_day" opt-in (absent, or some
    other string) falls back to the normal cadence-derived preset -- only an
    explicit "same_day" pin changes behavior."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    d = _creator_def()
    d.data["config"]["audit_batch"]["window_mode"] = "previous_day"
    captured = _make_captured_window_wda(monkeypatch, g)

    run_default_for_definition(d, access_token="t", cadence="daily")

    assert captured["window"] == g.resolve_window("yesterday", date.today())


def test_creator_run_default_passes_visit_clustering_criteria_overrides(monkeypatch):
    """The pinned visit_clustering config (time-gap/distance/duplicate-detection)
    rides through to job_config exactly like the sample percentages do."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    d = _creator_def()
    d.data["config"]["audit_batch"]["visit_clustering"] = {
        "enable_time_gap": True,
        "time_gap_minutes": 4,
        "enable_distance": True,
        "distance_meters": 10,
        "enable_duplicate_detection": True,
    }

    wda = mock.Mock()
    wda.list_runs.return_value = []
    wda.create_run.return_value = _run(1234, None)
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 5}
    fake_job.apply.return_value.successful.return_value = True
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    run_default_for_definition(d, access_token="t", window=("2026-06-21", "2026-06-27"))

    kw = fake_job.apply.call_args.kwargs["kwargs"]
    assert kw["job_config"]["enable_time_gap"] is True
    assert kw["job_config"]["time_gap_minutes"] == 4
    assert kw["job_config"]["enable_distance"] is True
    assert kw["job_config"]["distance_meters"] == 10
    assert kw["job_config"]["enable_duplicate_detection"] is True


def test_creator_run_default_no_visit_clustering_omits_criteria_overrides(monkeypatch):
    """A definition with no pinned visit_clustering block at all (e.g. one
    created before this feature existed — ``_creator_def()`` has no such block)
    injects no clustering keys into job_config — the handler's own
    state-fallback governs, unchanged from before this feature."""
    from connect_labs.workflow import audit_generation as g
    from connect_labs.workflow.templates import run_default_for_definition

    wda = mock.Mock()
    wda.list_runs.return_value = []
    wda.create_run.return_value = _run(1234, None)
    monkeypatch.setattr(g, "WorkflowDataAccess", mock.Mock(return_value=wda))
    fake_job = mock.Mock()
    fake_job.apply.return_value.result = {"sessions_created": 5}
    fake_job.apply.return_value.successful.return_value = True
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    run_default_for_definition(_creator_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    kw = fake_job.apply.call_args.kwargs["kwargs"]
    for key in (
        "enable_time_gap",
        "time_gap_minutes",
        "enable_distance",
        "distance_meters",
        "enable_duplicate_detection",
    ):
        assert key not in kw["job_config"]


def test_clustering_overrides_for_forces_duplicate_detection_off_without_clustering():
    """Mirrors the render's own guard: duplicate detection has no groupings to
    check across when both clustering gates are off, so a pinned config
    carrying that nonsensical combination is corrected rather than passed
    through as-is."""
    from connect_labs.workflow.audit_generation import clustering_overrides_for

    d = _creator_def()
    d.data["config"]["audit_batch"]["visit_clustering"] = {
        "enable_time_gap": False,
        "enable_distance": False,
        "enable_duplicate_detection": True,
    }

    assert clustering_overrides_for(d)["enable_duplicate_detection"] is False


def test_clustering_overrides_for_keeps_duplicate_detection_when_a_gate_is_on():
    from connect_labs.workflow.audit_generation import clustering_overrides_for

    d = _creator_def()
    d.data["config"]["audit_batch"]["visit_clustering"] = {
        "enable_time_gap": True,
        "enable_distance": False,
        "enable_duplicate_detection": True,
    }

    assert clustering_overrides_for(d)["enable_duplicate_detection"] is True


def test_clustering_overrides_for_leaves_duplicate_detection_when_gates_are_absent():
    """A partial visit_clustering block that only sets enable_duplicate_detection
    (the gate keys are simply absent, not explicitly False) must NOT be
    corrected — an absent key means "let the handler's state-fallback decide",
    which might resolve a gate to True from a prior run's state. Only an
    EXPLICIT False on both gates is the nonsensical combination this guard
    corrects."""
    from connect_labs.workflow.audit_generation import clustering_overrides_for

    d = _creator_def()
    d.data["config"]["audit_batch"]["visit_clustering"] = {"enable_duplicate_detection": True}

    overrides = clustering_overrides_for(d)
    assert overrides["enable_duplicate_detection"] is True
    assert "enable_time_gap" not in overrides
    assert "enable_distance" not in overrides


# ── resume_batch_run: manual/sweep resume of an existing run ────────────────


def test_resume_batch_run_refires_against_the_same_run_id(monkeypatch):
    """Resume must dispatch the SAME job_type against the EXISTING run_id --
    never create a new run — and derive the window from the run's persisted
    state (set once at create_run time, so it survives even a kill on the
    very first call)."""
    from connect_labs.workflow import audit_generation as g

    wda_factory = mock.Mock()
    monkeypatch.setattr(g, "WorkflowDataAccess", wda_factory)
    fake_job = mock.Mock()
    fake_job.delay.return_value.id = "celery-task-resume"
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    run = _run(13364, "2026-08-13")
    run.data["state"]["window_end"] = "2026-08-13"

    result = g.resume_batch_run(_creator_def(), run, access_token="t")

    assert result == {"run_id": 13364, "task_id": "celery-task-resume", "status": "running"}
    wda_factory.assert_not_called()  # no new WorkflowDataAccess / run created
    fake_job.delay.assert_called_once()
    kw = fake_job.delay.call_args.kwargs
    assert kw["run_id"] == 13364
    assert kw["opportunity_id"] == 1973
    assert kw["job_config"]["run_id"] == 13364
    assert kw["job_config"]["job_type"] == "weekly_dual_track_audit_create"
    assert kw["job_config"]["window_start"] == "2026-08-13"
    assert kw["job_config"]["window_end"] == "2026-08-13"
    # Sampling/clustering overrides ride through identically to a fresh fire.
    assert kw["job_config"]["muac_sample_percentage"] == 100
    assert kw["job_config"]["other_sample_percentage"] == 10


def test_resume_batch_run_scopes_program_owned_definition_by_program(monkeypatch):
    from connect_labs.workflow import audit_generation as g

    fake_job = mock.Mock()
    fake_job.delay.return_value.id = "celery-task-resume-2"
    monkeypatch.setattr(g, "run_workflow_job", fake_job)

    run = _run(13412, "2026-08-13")
    run.data["state"]["window_end"] = "2026-08-13"

    result = g.resume_batch_run(_creator_def(program_id=217), run, access_token="t")

    assert result == {"run_id": 13412, "task_id": "celery-task-resume-2", "status": "running"}
    kw = fake_job.delay.call_args.kwargs
    assert kw.get("program_id") == 217
    assert "opportunity_id" not in kw
    assert kw["job_config"]["program_id"] == 217
    assert "opportunity_id" not in kw["job_config"]


def test_resume_batch_run_raises_when_run_has_no_window():
    """A run with no persisted window (nothing was ever fired for it) has
    nothing to resume — a fresh run is what's needed instead."""
    from connect_labs.workflow import audit_generation as g

    run = _run(999, None)

    with pytest.raises(ValueError):
        g.resume_batch_run(_creator_def(), run, access_token="t")


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
        return {"run_id": 5, "sessions_created": 2, "status": "ready"}

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
        mock.Mock(return_value={"per_opp": {1973: {"run_id": 9, "sessions_created": 4, "status": "ready"}}}),
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
