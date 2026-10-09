from unittest import mock

from connect_labs.workflow.templates import get_template
from connect_labs.workflow.templates.muac_plausibility import STATE_KEY, run_default

WDA_PATH = "connect_labs.workflow.data_access.WorkflowDataAccess"


def _definition(opportunity_ids=(2154, 2304), program_id=217):
    d = mock.Mock()
    d.id = 501
    d.opportunity_ids = list(opportunity_ids)
    d.opportunity_id = None
    d.program_id = program_id
    d.data = {"config": {"utc_offset_hours": 1}}
    return d


def _row(opp, username, muac, age="30"):
    return {
        "opportunity_id": opp,
        "username": username,
        "ward": "Medu",
        "form_name": "Health Service Delivery",
        "time_start": "2026-09-01T09:00:00Z",
        "muac_cm": muac,
        "age_months": age,
        "childs_dob": None,
        "sex": "female",
        # A visit row carries the child's name; it must not reach the snapshot.
        "entity_name": "SYNTHETIC CHILD",
    }


def _run(rows, per_opp=None):
    wda = mock.Mock()
    wda.get_pipeline_data.return_value = {
        "muac_visits": {"rows": rows, "metadata": {"per_opp": per_opp or {}}},
    }
    wda.get_workers.side_effect = lambda opp: [{"username": "flw1", "name": "Synthetic FLW"}]
    run = mock.Mock()
    run.id = 77
    wda.create_run.return_value = run
    with mock.patch(WDA_PATH, return_value=wda) as factory:
        result = run_default(definition=_definition(), access_token="t")
    return result, wda, factory


def test_run_saves_one_program_owned_run_of_counts():
    rows = [_row(2154, "flw1", "15.0"), _row(2154, "flw1", "8.0"), _row(2304, "flw1", "150")]
    result, wda, factory = _run(rows)

    assert all(call.kwargs.get("program_id") == 217 for call in factory.call_args_list)
    assert wda.create_run.call_args.kwargs["program_id"] == 217
    snapshot = wda.complete_run.call_args.args[1]
    state = snapshot["state"][STATE_KEY]
    columns = state["cells"]["columns"]
    cells = [dict(zip(columns, r)) for r in state["cells"]["rows"]]
    assert {c["opportunity_id"] for c in cells} == {2154, 2304}
    assert sum(c["tier_a_low"] for c in cells) == 1
    assert sum(c["unit_error"] for c in cells) == 1
    assert state["flw_names"]["2154"] == {"flw1": "Synthetic FLW"}
    assert "SYNTHETIC CHILD" not in repr(snapshot)
    assert result == {"run_id": 77, "cells": 2, "visits_read": 3, "errors": []}


def test_a_failed_read_is_reported_not_counted_as_zero():
    result, wda, _ = _run([], per_opp={"2304": {"error": "boom"}})
    assert result["errors"] == ["visits unavailable for opportunity 2304: boom"]
    state = wda.complete_run.call_args.args[1]["state"][STATE_KEY]
    assert state["errors"] == result["errors"]


def test_template_is_registered_and_schedulable():
    template = get_template("muac_plausibility")
    assert template["multi_opp"] and template["supports_saved_runs"]
    assert template["run_default"] is run_default
    assert "function WorkflowUI" in template["render_code"]


def _job_wda(run_completed=False):
    wda = mock.Mock()
    run = mock.Mock()
    run.is_completed = run_completed
    run.definition_id = 501
    wda.get_run.return_value = run
    wda.get_definition.return_value = _definition()
    wda.get_pipeline_data.return_value = {
        "muac_visits": {"rows": [_row(2154, "flw1", "8.0")], "metadata": {"per_opp": {}}},
    }
    wda.get_workers.return_value = []
    return wda


def test_compute_job_fills_the_run_it_was_started_from():
    from connect_labs.workflow.job_handlers.muac_plausibility import muac_plausibility_compute

    wda = _job_wda()
    with mock.patch(WDA_PATH, return_value=wda) as factory:
        result = muac_plausibility_compute({"run_id": 99, "program_id": 217}, "t")

    assert all(call.kwargs.get("program_id") == 217 for call in factory.call_args_list)
    run_id, new_state = wda.update_run_state.call_args.args
    assert run_id == 99
    cells = new_state[STATE_KEY]["cells"]
    assert sum(dict(zip(cells["columns"], r))["tier_a_low"] for r in cells["rows"]) == 1
    # It fills the existing run; it neither creates nor completes one.
    wda.create_run.assert_not_called()
    wda.complete_run.assert_not_called()
    assert result["successful"] == 1 and result["visits_read"] == 1
    assert "rows" not in result


def test_compute_job_refuses_a_completed_run():
    import pytest

    from connect_labs.workflow.job_handlers.muac_plausibility import muac_plausibility_compute

    with mock.patch(WDA_PATH, return_value=_job_wda(run_completed=True)):
        with pytest.raises(ValueError, match="completed"):
            muac_plausibility_compute({"run_id": 99, "program_id": 217}, "t")


def test_compute_job_is_registered():
    from connect_labs.workflow.tasks import JOB_HANDLERS

    assert "muac_plausibility_compute" in JOB_HANDLERS
