"""The stale-run sweep: what it picks up, what it leaves alone, when it gives up.

The sweep exists because nothing self-heals today — a run whose worker was
killed sits at "running" forever and only a human noticing the error banner
recovers it. Its whole value is in the eligibility rules, so those are what
these tests pin: resume a job that genuinely died, never one that's still
working or that a human stopped, and stop retrying a run that keeps failing.
"""

from datetime import datetime, timedelta
from unittest import mock

import pytest

from connect_labs.workflow import tasks
from connect_labs.workflow.job_state import JOB_STALE_SECONDS
from connect_labs.workflow.resumable_runs import is_resumable, resume_handler_for


def _iso(age_seconds):
    return (datetime.now() - timedelta(seconds=age_seconds)).isoformat()


def _run(active_job, *, run_id=13364, resume_attempts=None):
    run = mock.Mock()
    run.id = run_id
    state = {"window_start": "2026-08-13", "window_end": "2026-08-13"}
    if active_job is not None:
        state["active_job"] = active_job
    if resume_attempts is not None:
        state["resume_attempts"] = resume_attempts
    run.data = {"state": state}
    return run


# ------------------------------------------------------------------- registry


def test_only_audited_handlers_are_resumable():
    assert is_resumable("weekly_dual_track_audit") is True
    assert is_resumable("muac_picture_audit") is False
    assert is_resumable(None) is False


def test_the_registry_resolves_to_a_real_callable():
    from connect_labs.workflow.audit_generation import resume_batch_run

    assert resume_handler_for("weekly_dual_track_audit") is resume_batch_run
    assert resume_handler_for("performance_review") is None


# ---------------------------------------------------------------- eligibility


def test_a_job_with_a_dead_heartbeat_is_resumed():
    eligible, reason = tasks._resume_eligibility(
        _run({"status": "running", "updated_at": _iso(JOB_STALE_SECONDS + 600)})
    )
    assert eligible is True
    assert "no heartbeat" in reason


def test_a_job_that_is_still_ticking_is_left_alone():
    """A dual-track batch legitimately runs for hours; staleness is measured
    from the heartbeat, not from how long it has been going."""
    eligible, reason = tasks._resume_eligibility(
        _run({"status": "running", "started_at": _iso(4 * 3600), "updated_at": _iso(30)})
    )
    assert eligible is False
    assert reason == "job is still ticking"


def test_a_failed_job_is_resumed():
    assert tasks._resume_eligibility(_run({"status": "failed", "updated_at": _iso(30)}))[0] is True


def test_a_completed_job_is_not_resumed():
    assert tasks._resume_eligibility(_run({"status": "completed", "updated_at": _iso(30)}))[0] is False


def test_a_cancelled_job_is_not_resumed():
    """A human stopped this one; restarting it behind their back is the one
    thing the sweep must never do."""
    eligible, reason = tasks._resume_eligibility(_run({"status": "cancelled", "updated_at": _iso(30)}))
    assert eligible is False
    assert "cancelled" in reason


def test_a_run_that_never_started_a_job_is_not_resumed():
    assert tasks._resume_eligibility(_run(None))[0] is False


def test_the_attempt_budget_stops_the_retrying():
    stale = {"status": "running", "updated_at": _iso(JOB_STALE_SECONDS + 600)}
    assert tasks._resume_eligibility(_run(stale, resume_attempts=tasks.MAX_RESUME_ATTEMPTS - 1))[0] is True
    assert tasks._resume_eligibility(_run(stale, resume_attempts=tasks.MAX_RESUME_ATTEMPTS))[0] is False


def test_an_exhausted_run_is_skipped_without_re_reading_its_age():
    eligible, reason = tasks._resume_eligibility(
        _run({"status": "running", "updated_at": _iso(JOB_STALE_SECONDS + 600), "resume_exhausted": True})
    )
    assert eligible is False
    assert "exhausted" in reason


# --------------------------------------------------------------------- sweep


@pytest.fixture
def _schedule(db, django_user_model):
    from connect_labs.labs.models import WorkflowSchedule

    owner = django_user_model.objects.create(username="sweeper")
    return WorkflowSchedule.objects.create(
        definition_id=12705, program_id=217, owner=owner, cadence="daily", hour=1, enabled=True
    )


def _patch_sweep(monkeypatch, definition_template, run):
    da = mock.Mock()
    definition = mock.Mock()
    definition.template_type = definition_template
    da.get_definition.return_value = definition
    da.list_runs.return_value = [run] if run else []
    monkeypatch.setattr(tasks, "get_valid_access_token", lambda _owner: "tok")
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **_kw: da)
    dispatch = mock.Mock()
    monkeypatch.setattr(tasks.resume_stale_workflow_run, "delay", dispatch)
    return da, dispatch


@pytest.mark.django_db
def test_sweep_dispatches_and_claims_the_attempt_first(_schedule, monkeypatch):
    """The claim has to be written BEFORE dispatch: the resumed job's first
    heartbeat lands after dispatch, so an overlapping tick would otherwise see
    the same eligible run and fire a second resume into it."""
    run = _run({"status": "running", "updated_at": _iso(JOB_STALE_SECONDS + 600)})
    da, dispatch = _patch_sweep(monkeypatch, "weekly_dual_track_audit", run)

    result = tasks.sweep_stale_workflow_runs()

    assert result == {"checked": 1, "dispatched": 1}
    da.update_run_state.assert_called_once_with(13364, {"resume_attempts": 1})
    dispatch.assert_called_once_with(_schedule.pk, 13364, 1)


@pytest.mark.django_db
def test_sweep_ignores_templates_with_no_resumable_handler(_schedule, monkeypatch):
    """A handler that isn't idempotent would duplicate its own work on a
    re-fire, so unregistered templates are never touched."""
    run = _run({"status": "running", "updated_at": _iso(JOB_STALE_SECONDS + 600)})
    _, dispatch = _patch_sweep(monkeypatch, "muac_picture_audit", run)

    assert tasks.sweep_stale_workflow_runs() == {"checked": 0, "dispatched": 0}
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_sweep_ignores_disabled_schedules(_schedule, monkeypatch):
    _schedule.enabled = False
    _schedule.save(update_fields=["enabled"])
    _, dispatch = _patch_sweep(
        monkeypatch, "weekly_dual_track_audit", _run({"status": "running", "updated_at": _iso(99999)})
    )

    assert tasks.sweep_stale_workflow_runs() == {"checked": 0, "dispatched": 0}
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_one_broken_schedule_does_not_stop_the_sweep(_schedule, django_user_model, monkeypatch):
    """Each schedule is checked in its own try/except — a deleted definition or
    an expired token on one must not stop every other run being recovered."""
    from connect_labs.labs.models import WorkflowSchedule

    healthy = WorkflowSchedule.objects.create(
        definition_id=999,
        program_id=217,
        owner=django_user_model.objects.create(username="other"),
        cadence="daily",
        hour=1,
        enabled=True,
    )
    run = _run({"status": "running", "updated_at": _iso(JOB_STALE_SECONDS + 600)})
    _, dispatch = _patch_sweep(monkeypatch, "weekly_dual_track_audit", run)

    def _token(owner):
        if owner.username == "sweeper":
            raise RuntimeError("token refresh failed")
        return "tok"

    monkeypatch.setattr(tasks, "get_valid_access_token", _token)

    assert tasks.sweep_stale_workflow_runs()["dispatched"] == 1
    assert dispatch.call_args[0][0] == healthy.pk


@pytest.mark.django_db
def test_the_last_failed_attempt_marks_the_run_terminal(_schedule, monkeypatch):
    """Otherwise the run reads as "stale, running" forever: skipped by the
    budget check, but still looking live to whoever opens the page."""
    da = mock.Mock()
    definition = mock.Mock()
    definition.template_type = "weekly_dual_track_audit"
    da.get_definition.return_value = definition
    da.get_run.return_value = _run({"status": "running", "updated_at": _iso(99999)})
    monkeypatch.setattr(tasks, "get_valid_access_token", lambda _owner: "tok")
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **_kw: da)
    monkeypatch.setattr(
        "connect_labs.workflow.resumable_runs.resume_handler_for",
        lambda _t: mock.Mock(side_effect=RuntimeError("upstream 500")),
    )
    marked = mock.Mock()
    monkeypatch.setattr(tasks, "_update_job_state", marked)

    result = tasks.resume_stale_workflow_run(_schedule.pk, 13364, tasks.MAX_RESUME_ATTEMPTS)

    assert result["status"] == "failed"
    job_state = marked.call_args[0][3]
    assert job_state["status"] == "failed"
    assert job_state["resume_exhausted"] is True
    assert "Exhausted 3 automatic resume attempts" in job_state["error"]


@pytest.mark.django_db
def test_an_earlier_failed_attempt_leaves_the_budget_open(_schedule, monkeypatch):
    da = mock.Mock()
    definition = mock.Mock()
    definition.template_type = "weekly_dual_track_audit"
    da.get_definition.return_value = definition
    da.get_run.return_value = _run({"status": "running", "updated_at": _iso(99999)})
    monkeypatch.setattr(tasks, "get_valid_access_token", lambda _owner: "tok")
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **_kw: da)
    monkeypatch.setattr(
        "connect_labs.workflow.resumable_runs.resume_handler_for",
        lambda _t: mock.Mock(side_effect=RuntimeError("upstream 500")),
    )
    marked = mock.Mock()
    monkeypatch.setattr(tasks, "_update_job_state", marked)

    tasks.resume_stale_workflow_run(_schedule.pk, 13364, 1)

    marked.assert_not_called()
