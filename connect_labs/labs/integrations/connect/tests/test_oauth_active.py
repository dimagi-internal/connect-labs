"""Tests for is_connect_oauth_active().

Exists because the main Labs overview page's "Connect authorized" badge (and
its CCHQ/OCS siblings) used to each duplicate a raw, no-refresh
session["labs_oauth"].expires_at check — reporting a merely time-expired but
refreshable Connect token as disconnected. Connect tokens have historically
lived long enough that this rarely surfaced in practice, but the code was
exactly as naive as the CCHQ side, which does show it daily due to shorter
token lifetimes. This is the shared fix, mirrored across all three providers.
"""

from unittest.mock import MagicMock, patch

from django.utils import timezone

from connect_labs.labs.integrations.connect.oauth import is_connect_oauth_active


def _fake_request(oauth: dict):
    request = MagicMock()
    request.session = {"labs_oauth": oauth} if oauth else {}
    return request


class TestIsConnectOauthActive:
    def test_active_when_not_expired(self):
        request = _fake_request({"access_token": "tok", "expires_at": timezone.now().timestamp() + 3600})
        assert is_connect_oauth_active(request) is True

    def test_refreshes_and_reports_active_when_expired(self):
        request = _fake_request(
            {"access_token": "expired", "refresh_token": "r", "expires_at": timezone.now().timestamp() - 10}
        )
        with patch(
            "connect_labs.labs.integrations.connect.oauth.refresh_connect_token", return_value=True
        ) as mock_refresh:
            assert is_connect_oauth_active(request) is True
        mock_refresh.assert_called_once_with(request)

    def test_inactive_when_refresh_fails(self):
        request = _fake_request(
            {"access_token": "expired", "refresh_token": "r", "expires_at": timezone.now().timestamp() - 10}
        )
        with patch("connect_labs.labs.integrations.connect.oauth.refresh_connect_token", return_value=False):
            assert is_connect_oauth_active(request) is False

    def test_inactive_when_no_token_at_all(self):
        request = _fake_request({})
        assert is_connect_oauth_active(request) is False
