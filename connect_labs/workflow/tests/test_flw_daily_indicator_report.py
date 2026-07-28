from datetime import datetime, timedelta, timezone
from unittest import mock

from connect_labs.workflow.templates.flw_daily_indicator_report import run_default

FETCH_CCHQ_CASES_PATH = "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.fetch_cchq_cases_as_visit_dicts"


def _visit_row(opp_id, username, time_start, form_display_name="Health Service Delivery", **overrides):
    row = {
        "opportunity_id": opp_id,
        "username": username,
        "form_display_name": form_display_name,
        "time_start": time_start,
        "time_end": time_start,
        "hh_case_id": f"hh-{username}",
        "child_case_id": f"child-{username}-{time_start}",
        "wa_caseid": "wa-1",
        "normalized_lat": "12.0",
        "normalized_lon": "9.0",
        "muac_cm": "15.0",
    }
    row.update(overrides)
    return row


def _wa_case_row(case_id, building_count):
    """Shape normalize_cchq_case_to_visit_dict produces -- only the keys
    run_default actually reads (form_json.case.case_id / .properties.building_count)."""
    return {"form_json": {"case": {"case_id": case_id, "properties": {"building_count": building_count}}}}


def _make_definition(opportunity_ids, program_id=176):
    d = mock.Mock()
    d.id = 999
    d.opportunity_ids = opportunity_ids
    d.opportunity_id = None
    d.program_id = program_id
    return d


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_splits_by_opportunity_and_completes_each_run(MockWDA, mock_fetch_cchq):
    definition = _make_definition([1001, 1002])

    in_window = "2026-07-20T08:00:00Z"
    hsd_rows = [
        _visit_row(1001, "alice", in_window),
        _visit_row(1001, "alice", "2026-07-20T09:00:00Z"),
        _visit_row(1002, "bob", in_window),
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}}

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

    def _fetch_cchq_side_effect(request, data_source, access_token, opportunity_id, *, cchq_access_token=None):
        return {1001: [_wa_case_row("wa-1", 5.0)], 1002: [_wa_case_row("wa-1", 2.0)]}[opportunity_id]

    mock_fetch_cchq.side_effect = _fetch_cchq_side_effect

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
    flws_1001 = snapshot_1001["state"]["flw_daily_indicators"]["flws"]
    assert len(flws_1001) == 1
    assert flws_1001[0]["username"] == "alice"
    assert flws_1001[0]["total_forms"] == 2
    assert flws_1001[0]["avg_forms_per_building"]["max_ratio"] == 0.4  # 2 forms / 5 buildings

    opp_instances[1002].complete_run.assert_called_once()
    snapshot_1002 = opp_instances[1002].complete_run.call_args.args[1]
    flws_1002 = snapshot_1002["state"]["flw_daily_indicators"]["flws"]
    assert len(flws_1002) == 1
    assert flws_1002[0]["username"] == "bob"
    assert flws_1002[0]["avg_forms_per_building"]["max_ratio"] == 0.5  # 1 form / 2 buildings


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_degrades_gracefully_when_cchq_fetch_fails(MockWDA, mock_fetch_cchq):
    """Building-count enrichment failing (e.g. no CCHQ token available) must not
    fail the whole run -- indicator #2's ratio is just None for that opp/day."""
    definition = _make_definition([1001])

    hsd_rows = [_visit_row(1001, "alice", "2026-07-20T08:00:00Z")]
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}}

    opp_instance = mock.Mock()
    run = mock.Mock()
    run.id = "run-1001"
    opp_instance.create_run.return_value = run

    def _wda_factory(*, access_token, opportunity_id=None, program_id=None):
        return fetch_instance if program_id is not None else opp_instance

    MockWDA.side_effect = _wda_factory
    mock_fetch_cchq.side_effect = RuntimeError("no CCHQ token stored for this user")

    run_default(
        definition=definition,
        access_token="tok",
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    snapshot = opp_instance.complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_daily_indicators"]["flws"]
    assert flws[0]["total_forms"] == 1
    assert flws[0]["avg_forms_per_building"]["max_ratio"] is None


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_excludes_rows_outside_window(MockWDA, mock_fetch_cchq):
    definition = _make_definition([1001])
    mock_fetch_cchq.return_value = []

    hsd_rows = [
        _visit_row(1001, "alice", "2026-07-20T08:00:00Z"),  # in window
        _visit_row(1001, "alice", "2026-07-19T08:00:00Z"),  # before window -> excluded
        _visit_row(1001, "alice", "2026-07-20T23:00:00Z"),  # on/after window end -> excluded (half-open)
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}}

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
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    snapshot = opp_instance.complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_daily_indicators"]["flws"]
    assert flws[0]["total_forms"] == 1


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_excludes_non_hsd_forms(MockWDA, mock_fetch_cchq):
    definition = _make_definition([1001])
    mock_fetch_cchq.return_value = []

    hsd_rows = [
        _visit_row(1001, "alice", "2026-07-20T08:00:00Z"),
        _visit_row(1001, "alice", "2026-07-20T09:00:00Z", form_display_name="No Children Found"),
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}}

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
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    snapshot = opp_instance.complete_run.call_args.args[1]
    flws = snapshot["state"]["flw_daily_indicators"]["flws"]
    assert flws[0]["total_forms"] == 1


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_defaults_window_to_yesterday_wat(MockWDA, mock_fetch_cchq):
    """No window kwarg -> resolves to yesterday's Africa/Lagos (UTC+1) calendar day."""
    definition = _make_definition([1001])
    mock_fetch_cchq.return_value = []
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": []}}
    opp_instance = mock.Mock()
    run = mock.Mock()
    run.id = "run-1001"
    opp_instance.create_run.return_value = run

    def _wda_factory(*, access_token, opportunity_id=None, program_id=None):
        return fetch_instance if program_id is not None else opp_instance

    MockWDA.side_effect = _wda_factory

    result = run_default(definition=definition, access_token="tok")

    report_date = datetime.fromisoformat(result["date"]).date()
    today_wat = (datetime.now(timezone.utc) + timedelta(hours=1)).date()
    assert report_date == today_wat - timedelta(days=1)
