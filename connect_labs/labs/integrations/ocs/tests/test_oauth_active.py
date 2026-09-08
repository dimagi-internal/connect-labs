"""Tests for is_ocs_oauth_active() — see the identical CCHQ/Connect helpers
(is_cchq_oauth_active / is_connect_oauth_active) for why every "is <provider>
connected" badge/gate should use a refresh-first check like this instead of a
raw expires_at check with no refresh attempt.
"""

from unittest.mock import MagicMock, patch

from django.utils import timezone

from connect_labs.labs.integrations.ocs.api_client import is_ocs_oauth_active


def _fake_request(oauth: dict):
    request = MagicMock()
    request.session = {"ocs_oauth": oauth} if oauth else {}
    return request


class TestIsOcsOauthActive:
    def test_active_when_not_expired(self):
        request = _fake_request({"access_token": "tok", "expires_at": timezone.now().timestamp() + 3600})
        assert is_ocs_oauth_active(request) is True

    def test_refreshes_and_reports_active_when_expired(self):
        request = _fake_request(
            {"access_token": "expired", "refresh_token": "r", "expires_at": timezone.now().timestamp() - 10}
        )
        with patch(
            "connect_labs.labs.integrations.ocs.api_client.OCSDataAccess._refresh_token", return_value=True
        ) as mock_refresh:
            assert is_ocs_oauth_active(request) is True
        mock_refresh.assert_called_once()

    def test_inactive_when_refresh_fails(self):
        request = _fake_request(
            {"access_token": "expired", "refresh_token": "r", "expires_at": timezone.now().timestamp() - 10}
        )
        with patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess._refresh_token", return_value=False):
            assert is_ocs_oauth_active(request) is False

    def test_inactive_when_no_token_at_all(self):
        request = _fake_request({})
        assert is_ocs_oauth_active(request) is False
