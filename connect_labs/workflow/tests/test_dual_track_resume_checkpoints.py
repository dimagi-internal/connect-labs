"""Resume semantics for the dual-track batch handler and its entry point.

Covers the three things a resume has to get right, each of which was wrong:

1. It must see what EVERY opportunity in the batch already has, not just the
   first one's (an opp-scoped client cannot read another opportunity's records).
2. "Already has sessions" is not "already done" — a call killed during AI
   review must be re-entered so those sessions get finished.
3. It must not fire alongside a job that is still alive, because the
   already-done check is a read-then-act with no lock.
"""

from unittest import mock

import pytest

from connect_labs.audit.run_checkpoints import completed_call_keys, session_is_complete
from connect_labs.workflow import audit_generation


def _session(session_id, opportunity_id, tag, *, ai_reviewer=True, ai_complete=True, clusters=None, dup_done=None):
    s = mock.Mock()
    s.id = session_id
    s.data = {
        "opportunity_id": opportunity_id,
        "tag": tag,
        "criteria": {"start_date": "2026-06-22", "end_date": "2026-06-28"},
        "has_ai_reviewer": ai_reviewer,
        "ai_review_complete": ai_complete,
        "visit_clusters": clusters or [],
    }
    if dup_done is not None:
        s.data["visit_cluster_dup_detection_complete"] = dup_done
    return s


# ---------------------------------------------------------------- completeness


def test_a_session_still_mid_ai_review_is_not_complete():
    assert session_is_complete(_session(1, 101, "muac", ai_complete=False)) is False


def test_a_session_with_no_ai_reviewer_is_complete_without_the_flag():
    """A track with no classifiers never gets ai_review_complete written —
    holding it open would mean re-entering that call on every single resume,
    forever."""
    assert session_is_complete(_session(1, 101, "rest", ai_reviewer=False, ai_complete=False)) is True


def test_clustered_session_awaits_duplicate_detection():
    clustered = _session(1, 101, "muac", clusters=[{"visit_ids": [1, 2]}], dup_done=False)
    assert session_is_complete(clustered) is False
    clustered.data["visit_cluster_dup_detection_complete"] = True
    assert session_is_complete(clustered) is True


def test_a_call_is_complete_only_when_all_of_its_sessions_are():
    """Per-FLW granularity means one call produces many sessions; one
    unfinished FLW keeps the whole call outstanding."""
    keys = completed_call_keys([_session(1, 101, "muac"), _session(2, 101, "muac", ai_complete=False)])
    assert keys == set()


# ------------------------------------------------------------------- handler


def _fake_definition(opportunity_ids):
    d = mock.Mock()
    track_a = {"tag": "muac", "sample_percentage": 100}
    track_b = {"tag": "rest", "sample_percentage": 10}
    d.data = {
        "opportunity_ids": opportunity_ids,
        "config": {
            "audit_batch": {
                "track_a": track_a,
                "track_b": track_b,
                "per_opp": {
                    str(o): {"muac_image_paths": ["form.muac"], "rest_image_paths": []} for o in opportunity_ids
                },
                "opp_names": {str(o): f"Opp {o}" for o in opportunity_ids},
            }
        },
    }
    return d


def _run_handler(existing_sessions, opportunity_ids=(101, 102)):
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = mock.Mock()
    run.definition_id = 42
    run.data = {"state": {"window_start": "2026-06-22", "window_end": "2026-06-28"}}

    def _eager(*_a, **_k):
        eager = mock.Mock()
        eager.result = {"sessions": [{"id": 5000}]}
        return eager

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
        mock.patch.object(h, "_sessions_for_run", return_value=existing_sessions),
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition(list(opportunity_ids))
        rac.apply.side_effect = _eager
        result = h.weekly_dual_track_audit_create({"run_id": 555, "program_id": 217}, access_token="tok")

    called = [
        (c.kwargs["kwargs"]["opportunities"][0]["id"], c.kwargs["kwargs"]["criteria"]["tag"])
        for c in rac.apply.call_args_list
    ]
    return result, called


def test_completed_work_in_a_later_opportunity_is_skipped():
    """The whole point of reading every opportunity: opp 102 finished before
    the kill, so a resume must not redo it. Under the opp[0]-scoped read this
    was invisible and got re-run every time."""
    _, called = _run_handler([_session(1, 102, "muac")])

    assert called == [(101, "muac")]


def test_a_call_killed_mid_review_is_re_entered():
    """Sessions exist for opp 101 but its review never finished — that call
    must run again so run_audit_creation's per-session resume can finish it."""
    _, called = _run_handler([_session(1, 101, "muac", ai_complete=False)])

    assert (101, "muac") in called


def test_sessions_created_is_the_run_total_not_this_invocations_tally():
    """A re-entered call re-reports the sessions it already had; totalling by
    id keeps the summary honest instead of double-counting them."""
    result, _ = _run_handler([_session(5000, 101, "muac", ai_complete=False), _session(1, 102, "muac")])

    # 5000 is both pre-existing AND returned by the re-entered call.
    assert result["sessions_created"] == 2


def test_the_resolved_criteria_are_persisted_before_any_work():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = mock.Mock()
    run.definition_id = 42
    run.data = {"state": {"window_start": "2026-06-22", "window_end": "2026-06-28"}}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
        mock.patch.object(h, "_sessions_for_run", return_value=[]),
        mock.patch.object(h, "_resolve_flw_cap", return_value=["flwA"]),
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition([101])
        rac.apply.side_effect = lambda *a, **k: mock.Mock(result={"sessions": []})
        h.weekly_dual_track_audit_create(
            {"run_id": 555, "program_id": 217, "pass_threshold": 85, "max_flws": 4}, access_token="tok"
        )

    first_write = wda.update_run_state.call_args_list[0][0][1]
    assert first_write["pass_threshold"] == 85
    assert first_write["max_flws"] == 4
    assert first_write["muac_sample_percentage"] == 100
    assert "last_batch" not in first_write  # written before the batch, not after it


# -------------------------------------------------------------- resume guard


def _run_with_active_job(active_job):
    run = mock.Mock()
    run.id = 13364
    run.data = {"state": {"window_start": "2026-08-13", "window_end": "2026-08-13", "active_job": active_job}}
    return run


def _iso(offset_seconds):
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(seconds=offset_seconds)).isoformat()


def test_resume_refuses_while_the_job_is_still_ticking():
    """Two concurrent invocations both read the same already-done set before
    either writes anything, so both would create the remainder — the exact
    duplication resume exists to prevent."""
    run = _run_with_active_job({"status": "running", "updated_at": _iso(60)})

    assert audit_generation.job_is_live(run) is True
    with pytest.raises(ValueError, match="live job"):
        audit_generation.resume_batch_run(mock.Mock(), run, access_token="tok")


def test_resume_proceeds_once_the_heartbeat_is_stale():
    run = _run_with_active_job({"status": "running", "updated_at": _iso(audit_generation.JOB_STALE_SECONDS + 60)})

    assert audit_generation.job_is_live(run) is False


def test_a_terminal_job_is_never_considered_live():
    assert audit_generation.job_is_live(_run_with_active_job({"status": "failed", "updated_at": _iso(5)})) is False


def test_force_overrides_a_live_job(monkeypatch):
    run = _run_with_active_job({"status": "running", "updated_at": _iso(60)})
    definition = mock.Mock()
    definition.opportunity_id = 101
    monkeypatch.setattr(audit_generation, "program_id_of", lambda _d: None)
    monkeypatch.setattr(audit_generation, "sample_overrides_for", lambda _d: {})
    monkeypatch.setattr(audit_generation, "clustering_overrides_for", lambda _d: {})
    dispatched = mock.Mock()
    dispatched.delay.return_value = mock.Mock(id="task-1")
    monkeypatch.setattr(audit_generation, "run_workflow_job", dispatched)

    result = audit_generation.resume_batch_run(definition, run, access_token="tok", force=True)

    assert result["status"] == "running"


def test_resume_replays_the_runs_own_criteria(monkeypatch):
    """A manually-started run's per-run choices live only on run state; a
    resume that dropped them would finish the run under different criteria
    than it began with."""
    run = _run_with_active_job({"status": "failed", "updated_at": _iso(5)})
    run.data["state"].update({"pass_threshold": 85, "max_flws": 4, "visit_statuses": ["approved"]})
    definition = mock.Mock()
    definition.opportunity_id = 101
    monkeypatch.setattr(audit_generation, "program_id_of", lambda _d: None)
    monkeypatch.setattr(audit_generation, "sample_overrides_for", lambda _d: {"muac_sample_percentage": 100})
    monkeypatch.setattr(audit_generation, "clustering_overrides_for", lambda _d: {})
    dispatched = mock.Mock()
    dispatched.delay.return_value = mock.Mock(id="task-1")
    monkeypatch.setattr(audit_generation, "run_workflow_job", dispatched)

    audit_generation.resume_batch_run(definition, run, access_token="tok")

    job_config = dispatched.delay.call_args.kwargs["job_config"]
    assert job_config["pass_threshold"] == 85
    assert job_config["max_flws"] == 4
    assert job_config["visit_statuses"] == ["approved"]
    assert job_config["window_start"] == "2026-08-13"
