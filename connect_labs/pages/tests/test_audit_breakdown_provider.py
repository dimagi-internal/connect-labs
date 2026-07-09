from unittest.mock import MagicMock, patch

from connect_labs.pages.providers.audit_breakdown import AuditBreakdownCardProvider


def _request(token="tok"):
    request = MagicMock()
    request.session = {"labs_oauth": {"access_token": token}}
    return request


@patch("connect_labs.pages.providers.audit_breakdown.get_org_data")
def test_entitled_true_when_all_opps_in_org_data(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973, "name": "EHA"}, {"id": 1976, "name": "Two"}]}
    prov = AuditBreakdownCardProvider()
    assert prov.entitled(_request(), {"workflow_run_id": 6128, "opportunity_ids": [1973, 1976]}) is True


@patch("connect_labs.pages.providers.audit_breakdown.get_org_data")
def test_entitled_false_when_any_opp_absent(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973}]}
    prov = AuditBreakdownCardProvider()
    assert prov.entitled(_request(), {"workflow_run_id": 6128, "opportunity_ids": [1973, 9999]}) is False


@patch("connect_labs.pages.providers.audit_breakdown.get_org_data")
def test_entitled_false_when_no_opp(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973}]}
    prov = AuditBreakdownCardProvider()
    assert prov.entitled(_request(), {"workflow_run_id": 6128}) is False


@patch("connect_labs.pages.providers.audit_breakdown.get_org_data")
def test_get_card_data_passes_run_and_opps_through(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973, "name": "EHA-PRE-RCT"}]}
    prov = AuditBreakdownCardProvider()

    payload = prov.get_card_data(_request(), {"workflow_run_id": 6128, "opportunity_ids": [1973]}, {})
    d = payload.to_dict()

    assert d["card_type"] == "flw_audit_breakdown"
    assert d["data"]["workflow_run_id"] == 6128
    assert d["data"]["opportunity_ids"] == [1973]
    assert d["data"]["opp_names"]["1973"] == "EHA-PRE-RCT"


@patch("connect_labs.pages.providers.audit_breakdown.get_org_data")
def test_accepts_singular_opportunity_id(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1973, "name": "EHA"}]}
    prov = AuditBreakdownCardProvider()
    assert prov.entitled(_request(), {"workflow_run_id": 6128, "opportunity_id": 1973}) is True
    d = prov.get_card_data(_request(), {"workflow_run_id": 6128, "opportunity_id": 1973}, {}).to_dict()
    assert d["data"]["opportunity_ids"] == [1973]
