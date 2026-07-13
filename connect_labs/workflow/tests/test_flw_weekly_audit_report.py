from datetime import datetime, timedelta, timezone
from unittest import mock

from connect_labs.workflow.templates.flw_weekly_audit_report import run_default


def _visit_row(opp_id, username, time_start, form_display_name="Health Service Delivery", **overrides):
    row = {
        "opportunity_id": opp_id,
        "username": username,
        "form_display_name": form_display_name,
        "time_start": time_start,
        "time_end": time_start,
        "muac_cm": "15.0",
        "childs_dob": "2024-01-01",
        "age_months": "24",
        "age_days": "730",
        "childs_gender": "male",
        "hh_case_id": f"hh-{username}",
        "child_case_id": f"child-{username}-{time_start}",
        "normalized_lat": "12.0",
        "normalized_lon": "9.0",
        "current_accuracy": "5.0",
        "accuracy_minimum": "25",
    }
    row.update(overrides)
    return row


def _make_definition(opportunity_ids, program_id=176):
    d = mock.Mock()
    d.id = 999
    d.opportunity_ids = opportunity_ids
    d.opportunity_id = None
    d.program_id = program_id
    return d


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_splits_by_opportunity_and_completes_each_run(MockWDA):
    definition = _make_definition([1001, 1002])

    in_window = "2026-07-06T08:00:00Z"  # a Monday
    hsd_rows = [
        _visit_row(1001, "alice", in_window),
        _visit_row(1001, "alice", "2026-07-06T09:00:00Z", time_end="2026-07-06T09:00:00Z"),
        _visit_row(1002, "bob", in_window),
    ]
    approved_rows = [_visit_row(1001, "alice", in_window)]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": hsd_rows},
        "approved_visits": {"rows": approved_rows},
    }

    opp_instances = {}

    def _wda_factory(*, access_token, opportunity_id=None, program_id=None):
        if program_id is not None:
            return fetch_instance
        inst = mock.Mock()
        run = mock.Mock()
        run.id = f"run-{opportunity_id}"
        inst.create_run.return_value = run
        opp_instances[opportunity_id] = inst
        return inst

    MockWDA.side_effect = _wda_factory

    result = run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 6, tzinfo=timezone.utc), datetime(2026, 7, 13, tzinfo=timezone.utc)),
    )

    assert set(result["opportunities"].keys()) == {"1001", "1002"}
    assert result["period_start"] == "2026-07-06"
    assert result["period_end"] == "2026-07-12"

    # opp 1001 got a run created + completed with 1 FLW (alice, 2 visits)
    opp_instances[1001].create_run.assert_called_once()
    call_kwargs = opp_instances[1001].create_run.call_args.kwargs
    assert call_kwargs["period_start"] == "2026-07-06"
    assert call_kwargs["period_end"] == "2026-07-12"

    opp_instances[1001].complete_run.assert_called_once()
    snapshot_1001 = opp_instances[1001].complete_run.call_args.args[1]
    flws_1001 = snapshot_1001["state"]["flw_audit_report"]["flws"]
    assert len(flws_1001) == 1
    assert flws_1001[0]["username"] == "alice"
    assert flws_1001[0]["total_service_delivery_forms"] == 2
    assert flws_1001[0]["total_approved_visits"] == 1

    # opp 1002 got its own separate run with only bob
    opp_instances[1002].complete_run.assert_called_once()
    snapshot_1002 = opp_instances[1002].complete_run.call_args.args[1]
    flws_1002 = snapshot_1002["state"]["flw_audit_report"]["flws"]
    assert len(flws_1002) == 1
    assert flws_1002[0]["username"] == "bob"
    assert flws_1002[0]["total_approved_visits"] == 0  # bob has no approved_visits rows


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_excludes_rows_outside_window(MockWDA):
    definition = _make_definition([1001])

    hsd_rows = [
        _visit_row(1001, "alice", "2026-07-06T08:00:00Z"),  # in window
        _visit_row(1001, "alice", "2026-06-29T08:00:00Z"),  # before window -> excluded
        _visit_row(1001, "alice", "2026-07-13T08:00:00Z"),  # on/after window end -> excluded (half-open)
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}, "approved_visits": {"rows": []}}

    opp_instance = mock.Mock()
    run = mock.Mock()
    run.id = "run-1001"
    opp_instance.create_run.return_value = run

    def _wda_factory(*, access_token, opportunity_id=None, program_id=None):
        return fetch_instance if program_id is not None else opp_instance

    MockWDA.side_effect = _wda_factory

    run_default(
        definition=definition,
        access_token="tok",
        window=(datetime(2026, 7, 6, tzinfo=timezone.utc), datetime(2026, 7, 13, tzinfo=timezone.utc)),
    )

    snapshot = opp_instance.complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_audit_report"]["flws"]
    assert flws[0]["total_service_delivery_forms"] == 1


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_excludes_non_hsd_forms(MockWDA):
    definition = _make_definition([1001])

    hsd_rows = [
        _visit_row(1001, "alice", "2026-07-06T08:00:00Z"),
        _visit_row(1001, "alice", "2026-07-06T09:00:00Z", form_display_name="No Children Found"),
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}, "approved_visits": {"rows": []}}

    opp_instance = mock.Mock()
    run = mock.Mock()
    run.id = "run-1001"
    opp_instance.create_run.return_value = run

    def _wda_factory(*, access_token, opportunity_id=None, program_id=None):
        return fetch_instance if program_id is not None else opp_instance

    MockWDA.side_effect = _wda_factory

    run_default(
        definition=definition,
        access_token="tok",
        window=(datetime(2026, 7, 6, tzinfo=timezone.utc), datetime(2026, 7, 13, tzinfo=timezone.utc)),
    )

    snapshot = opp_instance.complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_audit_report"]["flws"]
    assert flws[0]["total_service_delivery_forms"] == 1


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_defaults_window_to_last_full_week(MockWDA):
    """No window kwarg -> resolves to the most recent Mon 00:00 UTC - Sun 24:00 UTC span."""
    definition = _make_definition([1001])
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": []}, "approved_visits": {"rows": []}}
    opp_instance = mock.Mock()
    run = mock.Mock()
    run.id = "run-1001"
    opp_instance.create_run.return_value = run

    def _wda_factory(*, access_token, opportunity_id=None, program_id=None):
        return fetch_instance if program_id is not None else opp_instance

    MockWDA.side_effect = _wda_factory

    result = run_default(definition=definition, access_token="tok")

    period_start = datetime.fromisoformat(result["period_start"])
    period_end = datetime.fromisoformat(result["period_end"])
    assert period_start.weekday() == 0  # Monday
    assert period_end.weekday() == 6  # Sunday
    assert (period_end - period_start) == timedelta(days=6)
