"""Tests for funder_dashboard API views."""
import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from commcare_connect.funder_dashboard.api_views import api_funds_list


def _mock_request(method="GET", body=None, user_authenticated=True):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get("/funder/api/funds/")
    else:
        request = factory.post(
            "/funder/api/funds/",
            data=json.dumps(body or {}),
            content_type="application/json",
        )
    request.user = MagicMock(is_authenticated=user_authenticated, username="testuser")
    request.labs_context = {"organization_id": 42}
    request.session = {"labs_oauth": {"access_token": "tok", "expires_at": 9999999999}}
    return request


@patch("commcare_connect.funder_dashboard.api_views.FunderDashboardDataAccess")
class TestApiFundsList:
    def test_get_returns_funds(self, MockDA):
        mock_fund = MagicMock()
        mock_fund.id = 1
        mock_fund.data = {"name": "Test Fund", "status": "active"}
        MockDA.return_value.get_funds.return_value = [mock_fund]

        request = _mock_request("GET")
        response = api_funds_list(request)
        data = json.loads(response.content)

        assert response.status_code == 200
        assert len(data["funds"]) == 1

    def test_post_creates_fund(self, MockDA):
        mock_record = MagicMock()
        mock_record.id = 100
        mock_record.data = {"name": "New Fund"}
        MockDA.return_value.create_fund.return_value = mock_record

        request = _mock_request("POST", body={"name": "New Fund", "status": "active"})
        response = api_funds_list(request)

        assert response.status_code == 201
