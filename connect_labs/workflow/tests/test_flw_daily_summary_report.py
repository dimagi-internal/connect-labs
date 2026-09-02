from datetime import datetime, timezone
from unittest import mock

from connect_labs.workflow.templates.flw_daily_summary_report import run_default

FETCH_CCHQ_CASES_PATH = "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.fetch_cchq_cases_as_visit_dicts"


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
        "wa_case_id": None,
        "commcare_userid": None,
    }
    row.update(overrides)
    return row


def _wa_case_row(case_id, owner_id, closed=False, ward=None):
    """Shape normalize_cchq_case_to_visit_dict produces -- only the keys
    run_default actually reads (form_json.case.owner_id / .closed / .properties.ward)."""
    properties = {"ward": ward} if ward is not None else {}
    return {
        "form_json": {"case": {"case_id": case_id, "owner_id": owner_id, "closed": closed, "properties": properties}}
    }


def _worker(username, name=None, **overrides):
    worker = {"username": username, "name": name or username, "visit_count": 0, "last_active": None}
    worker.update(overrides)
    return worker


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


def _flw_by_username(flws):
    return {f["username"]: f for f in flws}


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_splits_by_opportunity_and_completes_each_run(MockWDA, mock_fetch_cchq):
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
    fetch_instance.get_workers.return_value = []
    mock_fetch_cchq.return_value = []

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
    assert result["errors"] == []

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


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_surfaces_per_opp_pipeline_fetch_error(MockWDA, mock_fetch_cchq):
    """get_pipeline_data's per-opp loop never raises -- a query failure for
    one (opp, alias) becomes an empty row list plus metadata.per_opp[opp]
    ["error"], not an exception. That must not be silently treated as "0
    real rows"; it has to show up in result["errors"] the same as a
    roster/work-area fetch failure, or a genuine data gap (e.g. opp 2154's
    approved_visits on 2026-09-01) reports a confident, wrong 0% instead of
    a visible failure."""
    definition = _make_definition([1001, 1002])

    in_window = "2026-07-20T08:00:00Z"
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {
            "rows": [_hsd_row(1002, "bob", in_window)],
            "metadata": {"per_opp": {"1001": {"error": "connection reset"}, "1002": {"row_count": 1}}},
        },
        "approved_visits": {"rows": []},
    }
    fetch_instance.get_workers.return_value = []
    mock_fetch_cchq.return_value = []

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    # The run still completes for both opps -- this is a visibility fix, not
    # a hard failure -- but opp 1001's error is now in errors, not silent.
    assert set(result["opportunities"].keys()) == {"1001", "1002"}
    assert len(result["errors"]) == 1
    assert "hsd_visits unavailable for opp 1001: connection reset" in result["errors"][0]


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_surfaces_raw_fetch_anomaly(MockWDA, mock_fetch_cchq):
    """A short-read the guard caught and rejected (kept stale-but-larger
    cached data instead) doesn't fail the fetch -- metadata.error is absent
    -- but it's still worth a visible warning, not silence."""
    definition = _make_definition([1001])

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {
            "rows": [],
            "metadata": {"per_opp": {"1001": {"raw_fetch_anomaly": {"prior_count": 500, "new_count": 12}}}},
        },
        "approved_visits": {"rows": []},
    }
    fetch_instance.get_workers.return_value = []
    mock_fetch_cchq.return_value = []

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    assert len(result["errors"]) == 1
    assert "hsd_visits short-read anomaly for opp 1001" in result["errors"][0]


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_excludes_rows_outside_window(MockWDA, mock_fetch_cchq):
    definition = _make_definition([1001])

    hsd_rows = [
        _hsd_row(1001, "alice", "2026-07-20T08:00:00Z"),  # in window
        _hsd_row(1001, "alice", "2026-07-19T08:00:00Z"),  # before window -> excluded
        _hsd_row(1001, "alice", "2026-07-20T23:00:00Z"),  # on/after window end -> excluded (half-open)
    ]

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": hsd_rows}, "approved_visits": {"rows": []}}
    fetch_instance.get_workers.return_value = []
    mock_fetch_cchq.return_value = []

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


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_uses_muac_photo_and_dw_dosage_date_time_fields(MockWDA, mock_fetch_cchq):
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
    fetch_instance.get_workers.return_value = []
    mock_fetch_cchq.return_value = []

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


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_defaults_window_to_yesterday_wat(MockWDA, mock_fetch_cchq):
    """No window kwarg -> resolves to yesterday's Africa/Lagos (UTC+1) calendar day."""
    from datetime import timedelta

    definition = _make_definition([1001])
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": []}, "approved_visits": {"rows": []}}
    fetch_instance.get_workers.return_value = []
    mock_fetch_cchq.return_value = []

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(definition=definition, access_token="tok")

    report_date = datetime.fromisoformat(result["date"]).date()
    today_wat = (datetime.now(timezone.utc) + timedelta(hours=1)).date()
    assert report_date == today_wat - timedelta(days=1)


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_includes_roster_only_flw_with_zero_activity(MockWDA, mock_fetch_cchq):
    """An FLW on the roster with no hsd/approved rows this day still gets a
    row, with the day's indicators all zero and their roster name attached."""
    definition = _make_definition([1001])

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": []}, "approved_visits": {"rows": []}}
    fetch_instance.get_workers.return_value = [_worker("quiet_carl", name="Carl Quiet")]
    mock_fetch_cchq.return_value = []

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    fetch_instance.get_workers.assert_called_once_with(1001)
    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert flws["quiet_carl"]["name"] == "Carl Quiet"
    assert flws["quiet_carl"]["total_health_service_delivery_visits"] == 0
    assert flws["quiet_carl"]["total_households_registered"] == 0


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_attaches_suspended_flag_from_roster(MockWDA, mock_fetch_cchq):
    definition = _make_definition([1001])

    in_window = "2026-07-20T08:00:00Z"
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": [_hsd_row(1001, "flagged_flo", in_window)]},
        "approved_visits": {"rows": []},
    }
    fetch_instance.get_workers.return_value = [
        _worker("flagged_flo", suspended=True, suspension_date="2026-07-15T00:00:00Z"),
        _worker("active_ade", suspended=False),
    ]
    mock_fetch_cchq.return_value = []

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert flws["flagged_flo"]["suspended"] is True
    assert flws["flagged_flo"]["suspension_date"] == "2026-07-15T00:00:00Z"
    # False is a real value, not "missing" -- must round-trip, not get dropped.
    assert flws["active_ade"]["suspended"] is False


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_computes_work_areas_left_via_commcare_userid_join(MockWDA, mock_fetch_cchq):
    """owner_id on an open work-area case joins to a visit's commcare_userid,
    not directly to the Connect username -- and the mapping is built from
    EVERY approved_visits row returned, not just today's window, so an FLW
    quiet today can still resolve via an older visit."""
    definition = _make_definition([1001])

    approved_rows = [
        # Historical visit (outside today's window) -- still usable for the mapping.
        _approved_row(1001, "moji", "2026-07-01T08:00:00Z", commcare_userid="cchq-moji-uuid"),
    ]
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": []},
        "approved_visits": {"rows": approved_rows},
    }
    fetch_instance.get_workers.return_value = [_worker("moji")]
    mock_fetch_cchq.return_value = [
        _wa_case_row("wa-1", "cchq-moji-uuid", closed=False),
        _wa_case_row("wa-2", "cchq-moji-uuid", closed=False),
        _wa_case_row("wa-3", "cchq-moji-uuid", closed=True),  # closed -- not "left"
        _wa_case_row("wa-4", "cchq-someone-else", closed=False),  # different owner
    ]

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
        cchq_access_token="cchq-tok",
    )

    mock_fetch_cchq.assert_called_once()
    assert mock_fetch_cchq.call_args.kwargs["cchq_access_token"] == "cchq-tok"

    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert flws["moji"]["work_areas_left"] == 2


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_omits_work_areas_left_when_commcare_userid_unresolved(MockWDA, mock_fetch_cchq):
    """A roster FLW who's never appeared in approved_visits (so no
    commcare_userid mapping exists) gets no work_areas_left key at all --
    not a misleading 0."""
    definition = _make_definition([1001])

    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {"hsd_visits": {"rows": []}, "approved_visits": {"rows": []}}
    fetch_instance.get_workers.return_value = [_worker("never_visited")]
    mock_fetch_cchq.return_value = [_wa_case_row("wa-1", "some-owner", closed=False)]

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
        cchq_access_token="cchq-tok",
    )

    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert "work_areas_left" not in flws["never_visited"]


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_computes_wards_via_wa_case_id_join(MockWDA, mock_fetch_cchq):
    """wards is the set of wards an FLW has an APPROVED VISIT in, joined via
    each approved_visits row's wa_case_id -> work-area case's own case_id ->
    case.properties.ward -- not the wards of every work area they own.
    Built from EVERY approved_visits row returned (all-time), same as
    work_areas_left's commcare_userid mapping. Sorted, deduped, and a work
    area visited twice must not duplicate its ward."""
    definition = _make_definition([1001])

    approved_rows = [
        _approved_row(1001, "moji", "2026-07-01T08:00:00Z", wa_case_id="wa-1", child_case_id="c1"),
        _approved_row(1001, "moji", "2026-07-02T08:00:00Z", wa_case_id="wa-1", child_case_id="c2"),  # dup ward
        _approved_row(1001, "moji", "2026-07-03T08:00:00Z", wa_case_id="wa-2", child_case_id="c3"),
        # No wa_case_id at all -- must not blow up, just contributes nothing.
        _approved_row(1001, "moji", "2026-07-04T08:00:00Z", wa_case_id=None, child_case_id="c4"),
        # wa_case_id that doesn't match any fetched work-area case.
        _approved_row(1001, "moji", "2026-07-05T08:00:00Z", wa_case_id="wa-missing", child_case_id="c5"),
    ]
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": []},
        "approved_visits": {"rows": approved_rows},
    }
    fetch_instance.get_workers.return_value = [_worker("moji")]
    mock_fetch_cchq.return_value = [
        _wa_case_row("wa-1", "some-owner", closed=True, ward="Ward Alpha"),
        _wa_case_row("wa-2", "some-owner", closed=False, ward="Ward Beta"),
        _wa_case_row("wa-3", "some-owner", closed=False),  # no ward property -- ignored
    ]

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
        cchq_access_token="cchq-tok",
    )

    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert flws["moji"]["wards"] == ["Ward Alpha", "Ward Beta"]


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_omits_wards_when_none_resolve(MockWDA, mock_fetch_cchq):
    """No approved visit resolves to a ward (no wa_case_id anywhere) -- the
    FLW gets no "wards" key at all, not an empty list."""
    definition = _make_definition([1001])

    approved_rows = [_approved_row(1001, "moji", "2026-07-01T08:00:00Z", wa_case_id=None)]
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": []},
        "approved_visits": {"rows": approved_rows},
    }
    fetch_instance.get_workers.return_value = [_worker("moji")]
    mock_fetch_cchq.return_value = [_wa_case_row("wa-1", "some-owner", closed=False, ward="Ward Alpha")]

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
        cchq_access_token="cchq-tok",
    )

    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert "wards" not in flws["moji"]


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_degrades_gracefully_when_work_area_fetch_fails(MockWDA, mock_fetch_cchq):
    """No cchq_access_token (or any other fetch failure) must not fail the
    run -- work_areas_left is just absent from every FLW dict."""
    definition = _make_definition([1001])

    approved_rows = [_approved_row(1001, "moji", "2026-07-01T08:00:00Z", commcare_userid="cchq-moji-uuid")]
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": []},
        "approved_visits": {"rows": approved_rows},
    }
    fetch_instance.get_workers.return_value = [_worker("moji")]
    mock_fetch_cchq.side_effect = Exception("CCHQHeadlessError: no token available")

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    assert result["opportunities"]["1001"]["status"] == "ready"
    flws = _flw_by_username(opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"])
    assert "work_areas_left" not in flws["moji"]
    # Not silent: this is exactly what run_scheduled_workflow (tasks.py) reads
    # into the schedule's last_error, surfacing the gap on the admin schedules
    # page as an amber note under an otherwise-green "OK" instead of nothing.
    assert len(result["errors"]) == 1
    assert "work_areas_left/wards unavailable for opp 1001" in result["errors"][0]


@mock.patch(FETCH_CCHQ_CASES_PATH)
@mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess")
def test_run_default_degrades_gracefully_when_roster_fetch_fails(MockWDA, mock_fetch_cchq):
    """A roster fetch failure must not fail the run -- it just falls back to
    only the FLWs found in this day's activity, same as before this feature."""
    definition = _make_definition([1001])

    in_window = "2026-07-20T08:00:00Z"
    fetch_instance = mock.Mock()
    fetch_instance.get_pipeline_data.return_value = {
        "hsd_visits": {"rows": [_hsd_row(1001, "alice", in_window)]},
        "approved_visits": {"rows": []},
    }
    fetch_instance.get_workers.side_effect = Exception("network error")
    mock_fetch_cchq.return_value = []

    opp_instances = {}
    MockWDA.side_effect = _wda_factory(fetch_instance, opp_instances)

    result = run_default(
        definition=definition,
        access_token="tok",
        request=None,
        window=(datetime(2026, 7, 19, 23, tzinfo=timezone.utc), datetime(2026, 7, 20, 23, tzinfo=timezone.utc)),
    )

    assert result["opportunities"]["1001"]["status"] == "ready"
    flws = opp_instances[1001].complete_run.call_args.args[1]["state"]["flw_daily_summary"]["flws"]
    assert [f["username"] for f in flws] == ["alice"]
    assert "suspended" not in flws[0]
    assert len(result["errors"]) == 1
    assert "worker roster unavailable for opp 1001" in result["errors"][0]
