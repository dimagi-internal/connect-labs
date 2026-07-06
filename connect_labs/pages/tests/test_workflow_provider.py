from unittest.mock import MagicMock, patch

from connect_labs.pages.providers.workflow import WorkflowCardProvider


def _definition_record():
    rec = MagicMock()
    rec.opportunity_ids = [42]
    rec.opportunity_id = 42
    rec.name = "Weekly Performance Review"
    rec.description = "Review each worker weekly"
    rec.data = {
        "card": {
            "card_type": "summary",
            "title": "Weekly Review",
            "metrics": [{"label": "Cadence", "value": "Weekly"}],
        },
    }
    return rec


def _request():
    request = MagicMock()
    request.session = {"labs_oauth": {"access_token": "tok"}}
    return request


@patch("connect_labs.pages.providers.workflow.get_org_data")
@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_true_when_definition_opp_in_org_data(mock_wda, mock_org):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    mock_org.return_value = {"opportunities": [{"id": 42}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 7}) is True


@patch("connect_labs.pages.providers.workflow.get_org_data")
@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_false_when_opp_absent(mock_wda, mock_org):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    mock_org.return_value = {"opportunities": [{"id": 1}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 7}) is False


@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_get_card_data_emits_workflow_runs(mock_wda):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    run_a = MagicMock(id=5060, created_at="2026-07-01", status="in_progress")
    run_b = MagicMock(id=5061, created_at="2026-07-08", status="completed")
    mock_wda.return_value.list_runs.return_value = [run_a, run_b]

    prov = WorkflowCardProvider()
    d = prov.get_card_data(_request(), {"definition_id": 5049, "opportunity_id": 1973}, {}).to_dict()

    assert d["card_type"] == "workflow_runs"
    assert d["title"] == "Weekly Performance Review"
    runs = d["data"]["runs"]
    assert [r["id"] for r in runs] == [5061, 5060]  # newest first
    assert runs[0]["status"] == "completed"
    assert runs[0]["created"] == "2026-07-08"
    assert runs[0]["open_url"] == "/labs/workflow/5049/run/?run_id=5061&opportunity_id=1973"
    assert "description" not in runs[0]  # no description/period in this card
    assert d["data"]["run_count"] == 2
    assert mock_wda.call_args.kwargs["opportunity_id"] == 1973


@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_get_card_data_caps_runs_at_8(mock_wda):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    mock_wda.return_value.list_runs.return_value = [
        MagicMock(id=i, created_at=f"2026-07-{i:02d}", status="in_progress") for i in range(1, 13)
    ]
    prov = WorkflowCardProvider()
    d = prov.get_card_data(_request(), {"definition_id": 5049, "opportunity_id": 1973}, {}).to_dict()
    assert len(d["data"]["runs"]) == 8
    assert d["data"]["run_count"] == 12
    assert d["data"]["runs"][0]["id"] == 12  # newest first


@patch("connect_labs.pages.providers.workflow.get_org_data")
@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_falls_back_to_singular_opportunity_id_for_single_opp(mock_wda, mock_org):
    rec = _definition_record()
    rec.opportunity_ids = []
    rec.opportunity_id = 42
    mock_wda.return_value.get_definition.return_value = rec
    mock_org.return_value = {"opportunities": [{"id": 42}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 7}) is True


@patch("connect_labs.pages.providers.workflow.get_org_data")
@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_false_when_no_definition_id(mock_wda, mock_org):
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {}) is False


@patch("connect_labs.pages.providers.workflow.get_org_data")
@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_via_target_opportunity_without_loading_definition(mock_wda, mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 5049, "opportunity_id": 1973}) is True
    # opp-in-target path must NOT need to read the (opp-scoped) definition
    mock_wda.return_value.get_definition.assert_not_called()


@patch("connect_labs.pages.providers.workflow.get_org_data")
@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_false_when_target_opportunity_absent(mock_wda, mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 5049, "opportunity_id": 9999}) is False


@patch("connect_labs.pages.providers.workflow.WorkflowDataAccess")
def test_get_card_data_scopes_reads_by_target_opportunity(mock_wda):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    mock_wda.return_value.list_runs.return_value = []
    prov = WorkflowCardProvider()
    prov.get_card_data(_request(), {"definition_id": 5049, "opportunity_id": 1973}, {})
    # the definition + runs reads are scoped by the card's opportunity
    assert mock_wda.call_args.kwargs["opportunity_id"] == 1973
