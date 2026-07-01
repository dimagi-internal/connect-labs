from unittest.mock import MagicMock, patch

from commcare_connect.pages.providers.workflow import WorkflowCardProvider


def _definition_record():
    rec = MagicMock()
    rec.opportunity_ids = [42]
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


@patch("commcare_connect.pages.providers.workflow.get_org_data")
@patch("commcare_connect.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_true_when_definition_opp_in_org_data(mock_wda, mock_org):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    mock_org.return_value = {"opportunities": [{"id": 42}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 7}) is True


@patch("commcare_connect.pages.providers.workflow.get_org_data")
@patch("commcare_connect.pages.providers.workflow.WorkflowDataAccess")
def test_entitled_false_when_opp_absent(mock_wda, mock_org):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    mock_org.return_value = {"opportunities": [{"id": 1}]}
    prov = WorkflowCardProvider()
    assert prov.entitled(_request(), {"definition_id": 7}) is False


@patch("commcare_connect.pages.providers.workflow.WorkflowDataAccess")
def test_get_card_data_reads_declared_card_block_and_builds_cta(mock_wda):
    mock_wda.return_value.get_definition.return_value = _definition_record()
    prov = WorkflowCardProvider()
    payload = prov.get_card_data(_request(), {"definition_id": 7}, {})
    d = payload.to_dict()
    assert d["title"] == "Weekly Review"
    assert d["card_type"] == "summary"
    assert d["metrics"] == [{"label": "Cadence", "value": "Weekly"}]
    assert d["cta"]["url"] == "/labs/workflow/7/run/"
