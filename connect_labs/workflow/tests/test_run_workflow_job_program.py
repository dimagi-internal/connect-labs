"""run_workflow_job must scope its run/state reads by program when dispatched
with program_id (program-owned workflow, no owning opportunity), and thread the
resolved scope into job_config so the handler sees program_id."""

from unittest import mock


def test_run_workflow_job_scopes_by_program(monkeypatch):
    from connect_labs.workflow import tasks as m

    seen_scopes = []

    def wda_factory(*_a, request=None, access_token=None, opportunity_id=None, program_id=None, **_k):
        seen_scopes.append({"opportunity_id": opportunity_id, "program_id": program_id})
        inst = mock.Mock()
        inst.get_run.return_value = mock.Mock(data={"state": {}}, definition_id=5110)
        return inst

    captured = {}

    def fake_handler(job_config, access_token, progress_callback):
        captured["job_config"] = dict(job_config)
        return {"successful": 1, "failed": 0}

    monkeypatch.setitem(m.JOB_HANDLERS, "test_prog_job", fake_handler)
    monkeypatch.setattr(m, "set_task_progress", lambda *a, **k: None)

    # WorkflowDataAccess is imported lazily inside the state helpers — patch at source.
    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", wda_factory):
        m.run_workflow_job.apply(
            kwargs={
                "job_config": {"job_type": "test_prog_job", "records": [{"id": 1}]},
                "access_token": "t",
                "run_id": 5112,
                "program_id": 176,
            }
        ).get()

    # Every state-scoped DAO was program-scoped, no owning opp.
    assert seen_scopes, "expected at least one WorkflowDataAccess construction"
    assert all(s == {"opportunity_id": None, "program_id": 176} for s in seen_scopes)
    # The handler received the program scope on job_config.
    assert captured["job_config"]["program_id"] == 176
    assert captured["job_config"].get("opportunity_id") is None


def test_run_workflow_job_opp_scope_unchanged(monkeypatch):
    from connect_labs.workflow import tasks as m

    seen_scopes = []

    def wda_factory(*_a, request=None, access_token=None, opportunity_id=None, program_id=None, **_k):
        seen_scopes.append({"opportunity_id": opportunity_id, "program_id": program_id})
        inst = mock.Mock()
        inst.get_run.return_value = mock.Mock(data={"state": {}}, definition_id=5110)
        return inst

    captured = {}

    def fake_handler(job_config, access_token, progress_callback):
        captured["job_config"] = dict(job_config)
        return {"successful": 1, "failed": 0}

    monkeypatch.setitem(m.JOB_HANDLERS, "test_opp_job", fake_handler)
    monkeypatch.setattr(m, "set_task_progress", lambda *a, **k: None)

    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", wda_factory):
        m.run_workflow_job.apply(
            kwargs={
                "job_config": {"job_type": "test_opp_job", "records": [{"id": 1}]},
                "access_token": "t",
                "run_id": 4864,
                "opportunity_id": 1973,
            }
        ).get()

    assert all(s == {"opportunity_id": 1973, "program_id": None} for s in seen_scopes)
    assert captured["job_config"]["opportunity_id"] == 1973
    assert captured["job_config"].get("program_id") is None
