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
