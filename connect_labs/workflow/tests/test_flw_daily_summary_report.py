from datetime import datetime, timezone
from unittest import mock

from connect_labs.workflow.templates.flw_daily_summary_report import run_default


def _hsd_row(opp_id, username, time_start, form_display_name="Health Service Delivery", **overrides):
    row = {
        "opportunity_id": opp_id,
        "username": username,
        "form_display_name": form_display_name,
        "time_start": time_start,
        "hh_case_id": f"hh-{username}",
        "child_case_id": f"child-{username}-{time_start}",
    }
    row.update(overrides)
    return row


def _approved_row(opp_id, username, time_start, form_display_name="Health Service Delivery", **overrides):
    row = {
        "opportunity_id": opp_id,
        "username": username,
        "form_display_name": form_display_name,
        "time_start": time_start,
        "child_case_id": f"child-{username}-{time_start}",
        "childs_dob": "2024-01-01",
        "muac_cm": None,
        "muac_photo": None,
        "dw_dosage_date_time": None,
    }
    row.update(overrides)
    return row


def _make_definition(opportunity_ids, program_id=217):
    d = mock.Mock()
    d.id = 999
    d.opportunity_ids = opportunity_ids
    d.opportunity_id = None
    d.program_id = program_id
    return d


def _wda_factory(fetch_instance, opp_instances):
    def _factory(*, access_token, opportunity_id=None, program_id=None):
        if program_id is not None:
            return fetch_instance
        inst = mock.Mock()
        run = mock.Mock()
        run.id = f"run-{opportunity_id}"
        inst.create_run.return_value = run
        opp_instances[opportunity_id] = inst
        return inst

    return _factory


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_splits_by_opportunity_and_completes_each_run(MockWDA):
    definition = _make_definition([1001, 1002])

    in_window = "2026-07-20T08:00:00Z"
    hsd_rows = [
        _hsd_row(1001, "alice", in_window, hh_case_id="hh-alice-1"),
        _hsd_row(1001, "alice", "2026-07-20T09:00:00Z", hh_case_id="hh-alice-2"),
        _hsd_row(1002, "bob", in_window),
    ]
    approved_rows = [
        _approved_row(1001, "alice", in_window, childs_dob="2024-07-20"),  # ~12mo old
        _approved_row(1002, "bob", in_window, childs_dob="2024-07-20"),
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": hsd_rows},
        "approved_visits": {"rows": approved_rows},
    }

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    assert set(result["opportunities"].keys()) == {"1001", "1002"}
    assert result["date"] == "2026-07-20"

    opp_instances[1001].create_run.assert_called_once()
    call_kwargs = opp_instances[1001].create_run.call_args.kwargs
    assert call_kwargs["period_start"] == "2026-07-20"
    assert call_kwargs["period_end"] == "2026-07-20"

    opp_instances[1001].complete_run.assert_called_once()
    snapshot_1001 = opp_instances[1001].complete_run.call_args.args[1]
    flws_1001 = snapshot_1001["state"]["flw_daily_summary"]["flws"]
    assert len(flws_1001) == 1
    assert flws_1001[0]["username"] == "alice"
    assert flws_1001[0]["total_health_service_delivery_visits"] == 2
    assert flws_1001[0]["total_households_registered"] == 2
    assert flws_1001[0]["total_approved_health_service_delivery_visits"] == 1
    assert flws_1001[0]["total_children_deworming_eligible"] == 1
    # No muac_photo/dw_dosage_date_time on this row -- photo/dosage indicators are 0.
    assert flws_1001[0]["total_children_deworming_photo_taken"] == 0

    opp_instances[1002].complete_run.assert_called_once()
    snapshot_1002 = opp_instances[1002].complete_run.call_args.args[1]
    flws_1002 = snapshot_1002["state"]["flw_daily_summary"]["flws"]
    assert len(flws_1002) == 1
    assert flws_1002[0]["username"] == "bob"


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_excludes_rows_outside_window(MockWDA):
    definition = _make_definition([1001])

    hsd_rows = [
        _hsd_row(1001, "alice", "2026-07-20T08:00:00Z"),  # in window
        _hsd_row(1001, "alice", "2026-07-19T08:00:00Z"),  # before window -> excluded
        _hsd_row(1001, "alice", "2026-07-20T23:00:00Z"),  # on/after window end -> excluded (half-open)
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}, "approved_visits": {"rows": []}}

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    snapshot = opp_instances[1001].complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_daily_summary"]["flws"]
    assert flws[0]["total_health_service_delivery_visits"] == 1


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_uses_muac_photo_and_dw_dosage_date_time_fields(MockWDA):
    """Full path: an approved_visits row carrying real muac_photo/dw_dosage_date_time
    values should flip both indicators to 1 for the matching child."""
    definition = _make_definition([1001])

    time_start = "2026-07-20T08:00:00Z"
    approved_rows = [
        _approved_row(
            1001,
            "alice",
            time_start,
            child_case_id="c1",
            childs_dob="2024-07-20",
            muac_photo="muac_photo_1.jpg",
            dw_dosage_date_time="2026-07-20T08:05:00.000000Z",
        )
    ]
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": []},
        "approved_visits": {"rows": approved_rows},
    }

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    snapshot = opp_instances[1001].complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_daily_summary"]["flws"]
    assert flws[0]["total_children_muac_measured"] == 1
    assert flws[0]["total_children_deworming_photo_taken"] == 1


@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_defaults_window_to_yesterday_wat(MockWDA):
    """No window kwarg -> resolves to yesterday's Africa/Lagos (UTC+1) calendar day."""
    from datetime import timedelta

    definition = _make_definition([1001])
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": []}, "approved_visits": {"rows": []}}

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(definition=definition, access_token="tok")

    report_date = datetime.fromisoformat(result["date"]).date()
    today_wat = (datetime.now(timezone.utc) + timedelta(hours=1)).date()
    assert report_date == today_wat - timedelta(days=1)
