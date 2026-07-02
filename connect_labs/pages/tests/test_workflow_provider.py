from unittest.mock import MagicMock, patch

from connect_labs.pages.providers.workflow import WorkflowCardProvider


def _definition_record():
    rec = MagicMock()
    rec.opportunity_ids = [42]
    rec.opportunity_id = 42
    rec.name = "Weekly Performance Review"
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
def test_get_card_data_reads_declared_card_block_and_builds_cta(mock_wda):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    prov = WorkflowCardProvider()
    payload = prov.get_card_data(_request(), {"definition_id": 7}, {})
    d = payload.to_dict()
    assert d["title"] == "Weekly Review"
    assert d["card_type"] == "summary"
    assert d["metrics"] == [{"label": "Cadence", "value": "Weekly"}]
    assert d["cta"]["url"] == "/labs/workflow/7/run/"


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
def test_get_card_data_scopes_definition_read_by_target_opportunity(mock_wda):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    prov = WorkflowCardProvider()
    prov.get_card_data(_request(), {"definition_id": 5049, "opportunity_id": 1973}, {})
    # the definition read is scoped by the card's opportunity
    assert mock_wda.call_args.kwargs["opportunity_id"] == 1973
