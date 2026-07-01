from unittest.mock import MagicMock, patch

from connect_labs.pages.providers.audit import AuditCardProvider


def _request_with_opps(opp_ids, token="tok"):
    request = MagicMock()
    request.session = {"labs_oauth": {"access_token": token}}
    return request


@patch("connect_labs.pages.providers.audit.get_org_data")
def test_entitled_true_when_opp_in_org_data(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 42}]}
    prov = AuditCardProvider()
    assert prov.entitled(_request_with_opps([42]), {"opportunity_id": 42}) is True


@patch("connect_labs.pages.providers.audit.get_org_data")
def test_entitled_false_when_opp_absent(mock_org):
    mock_org.return_value = {"opportunities": [{"id": 1}]}
    prov = AuditCardProvider()
    assert prov.entitled(_request_with_opps([1]), {"opportunity_id": 999}) is False


@patch("connect_labs.pages.providers.audit.AuditDataAccess")
def test_get_card_data_builds_payload_with_counts_and_cta(mock_ada_cls):
    mock_ada_cls.return_value.get_visit_ids_for_audit.return_value = [1, 2, 3, 4, 5]
    prov = AuditCardProvider()
    request = _request_with_opps([42])

    payload = prov.get_card_data(request, {"opportunity_id": 42, "opportunity_name": "Opp A"}, {})
    d = payload.to_dict()

    assert d["card_type"] == "audit_summary"
    assert d["title"] == "Opp A"
    assert any(m["value"] == 5 for m in d["metrics"])
    assert d["cta"]["url"].startswith("/audit/")
    assert "opportunity_id=42" in d["cta"]["url"]
