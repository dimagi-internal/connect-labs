"""run_workflow_job's init write must stamp its own fresh updated_at.

_update_job_state MERGES onto whatever active_job a run already holds
({**current_job, **job_state_updates}) -- so a run whose PREVIOUS job's
heartbeat went stale hours ago would have a brand-new job inherit that stale
updated_at if the init write didn't overwrite it, and
connect_labs.workflow.views.active_job_age_seconds prefers updated_at over
started_at. Without this reset, every re-run on an old run would report an
immediate false zombie on its very first poll."""

from unittest import mock


def test_run_workflow_job_init_resets_updated_at_even_with_a_stale_prior_heartbeat(monkeypatch):
    from connect_labs.workflow import tasks as m

    # Simulate a run whose PREVIOUS job's heartbeat is hours old.
    prior_state = {"active_job": {"status": "completed", "updated_at": "2020-01-01T00:00:00"}}
    written_active_jobs = []

    def wda_factory(*_a, request=None, access_token=None, opportunity_id=None, program_id=None, **_k):
        inst = mock.Mock()
        inst.get_run.return_value = mock.Mock(data={"state": dict(prior_state)}, definition_id=5110)

        def fake_update_run_state(_run_id, updates):
            written_active_jobs.append(updates.get("active_job"))

        inst.update_run_state.side_effect = fake_update_run_state
        return inst

    def fake_handler(job_config, access_token, progress_callback):
        return {"successful": 1, "failed": 0}

    monkeypatch.setitem(m.JOB_HANDLERS, "test_heartbeat_job", fake_handler)
    monkeypatch.setattr(m, "set_task_progress", lambda *a, **k: None)

    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", wda_factory):
        m.run_workflow_job.apply(
            kwargs={
                "job_config": {"job_type": "test_heartbeat_job", "records": [{"id": 1}]},
                "access_token": "t",
                "run_id": 5112,
                "opportunity_id": 101,
            }
        ).get()

    # The FIRST write is the init write -- must carry a fresh updated_at, not
    # the 2020 leftover from prior_state.
    assert written_active_jobs, "expected at least one active_job write"
    init_write = written_active_jobs[0]
    assert init_write["status"] == "running"
    assert init_write.get("updated_at") is not None
    assert init_write["updated_at"] != "2020-01-01T00:00:00"
    # Both stamped fresh, moments apart -- same year at minimum rules out a
    # leftover value while tolerating the microsecond gap between the two
    # separate datetime.now() calls.
    assert init_write["updated_at"][:4] == init_write["started_at"][:4]
