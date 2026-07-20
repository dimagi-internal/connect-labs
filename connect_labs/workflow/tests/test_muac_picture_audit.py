from unittest import mock

import pytest


def _fake_run(username="nm1"):
    run = mock.Mock()
    run.username = username
    return run


def _job_config(**overrides):
    base = {
        "run_id": 555,
        "opportunity_id": None,
        "program_id": 176,
        "opportunities": [{"id": 1973, "name": "EHA"}, {"id": 1976, "name": "JHF"}],
        "criteria": {
            "audit_type": "date_range",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "days_of_week": [5],
            "granularity": "combined",
            "title": "January Fridays",
            "tag": "muac",
        },
        "visit_ids": [1, 2, 3],
        "flw_visit_ids": {"alice": [1, 2], "bob": [3]},
        "image_audits": [{"image_path": "form.muac", "reviewers": []}],
        "context_fields": None,
    }
    base.update(overrides)
    return base


def test_handler_scopes_data_access_by_program_id_for_program_owned_runs():
    """Mirrors the weekly_dual_track_audit fix: a program-owned run has no
    opportunity_id — job_config.program_id (injected by run_workflow_job) must
    be threaded into WorkflowDataAccess, or get_run() 404s against the Labs
    Record API."""
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    run = _fake_run()
    eager = mock.Mock()
    eager.result = {"sessions": [{"id": 1}, {"id": 2}]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        rac.apply.return_value = eager

        h.muac_picture_audit_create(_job_config(), access_token="tok")

    WDA.assert_called_once_with(access_token="tok", opportunity_id=None, program_id=176)


def test_handler_calls_run_audit_creation_once_with_job_config_payload():
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    run = _fake_run()
    eager = mock.Mock()
    eager.result = {"sessions": [{"id": 1}, {"id": 2}, {"id": 3}]}

    job_config = _job_config()
    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        WDA.return_value.get_run.return_value = run
        rac.apply.return_value = eager

        result = h.muac_picture_audit_create(job_config, access_token="tok")

    rac.apply.assert_called_once()
    kwargs = rac.apply.call_args.kwargs["kwargs"]
    assert kwargs["access_token"] == "tok"
    assert kwargs["username"] == "nm1"
    assert kwargs["opportunities"] == job_config["opportunities"]
    assert kwargs["criteria"] == job_config["criteria"]
    assert kwargs["visit_ids"] == [1, 2, 3]
    assert kwargs["flw_visit_ids"] == {"alice": [1, 2], "bob": [3]}
    assert kwargs["workflow_run_id"] == 555
    assert kwargs["image_audits"] == job_config["image_audits"]
    assert kwargs["context_fields"] is None
    # granularity/per_opp handling lives entirely inside run_audit_creation —
    # this handler never branches on it or loops per-opportunity.
    assert rac.apply.call_count == 1

    assert result["sessions_created"] == 3


def test_handler_falls_back_to_job_config_username_when_run_has_none():
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    run = _fake_run(username=None)
    eager = mock.Mock()
    eager.result = {"sessions": []}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        WDA.return_value.get_run.return_value = run
        rac.apply.return_value = eager

        h.muac_picture_audit_create(_job_config(username="from_payload"), access_token="tok")

    assert rac.apply.call_args.kwargs["kwargs"]["username"] == "from_payload"


def test_handler_persists_last_batch_summary_onto_run_state():
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    run = _fake_run()
    eager = mock.Mock()
    eager.result = {"sessions": [{"id": 1}]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        rac.apply.return_value = eager

        h.muac_picture_audit_create(_job_config(), access_token="tok")

        wda.update_run_state.assert_called_once()
        run_id_arg, state_arg = wda.update_run_state.call_args[0]
        assert run_id_arg == 555
        assert state_arg["last_batch"]["sessions_created"] == 1
        assert state_arg["last_batch"]["opportunity_ids"] == [1973, 1976]
        assert state_arg["last_batch"]["title"] == "January Fridays"
        assert state_arg["last_batch"]["tag"] == "muac"


def test_handler_relays_progress_via_registry_not_apply_kwargs():
    """The progress callback must go through register_relay/get_relay, not a
    direct progress_callback kwarg into .apply() — Celery's eager path
    serializes kwargs and a closure there breaks audit creation entirely."""
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    run = _fake_run()
    eager = mock.Mock()
    eager.result = {"sessions": []}
    seen_relay = {}

    def fake_register_relay(run_id, callback):
        seen_relay["run_id"] = run_id
        seen_relay["callback"] = callback

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
        mock.patch.object(h, "register_relay", side_effect=fake_register_relay) as reg,
        mock.patch.object(h, "pop_relay") as pop,
    ):
        WDA.return_value.get_run.return_value = run
        rac.apply.return_value = eager

        h.muac_picture_audit_create(_job_config(), access_token="tok", progress_callback=lambda *a, **k: None)

    reg.assert_called_once()
    assert seen_relay["run_id"] == 555
    pop.assert_called_once_with(555)
    assert "progress_callback" not in rac.apply.call_args.kwargs["kwargs"]


def test_raises_when_run_id_missing():
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    with pytest.raises(ValueError, match="requires run_id"):
        h.muac_picture_audit_create(_job_config(run_id=None), access_token="tok")


def test_raises_when_no_opportunities():
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    with pytest.raises(ValueError, match="requires at least one opportunity"):
        h.muac_picture_audit_create(_job_config(opportunities=[]), access_token="tok")


def test_raises_when_run_not_found():
    from connect_labs.workflow.job_handlers import muac_picture_audit as h

    with mock.patch.object(h, "WorkflowDataAccess") as WDA:
        WDA.return_value.get_run.return_value = None
        with pytest.raises(ValueError, match="run 555 not found"):
            h.muac_picture_audit_create(_job_config(), access_token="tok")
