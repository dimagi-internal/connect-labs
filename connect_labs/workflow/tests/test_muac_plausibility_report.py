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
