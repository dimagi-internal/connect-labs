"""Tests for BaseCoverageView.check_commcare_oauth().

Exists because this used to duplicate a raw, no-refresh expires_at check —
reporting a merely time-expired but refreshable CCHQ token as disconnected
and blocking the coverage map landing page with "please authorize" even
though a working refresh token was sitting right there.
"""

from unittest.mock import patch

from django.test import RequestFactory

from connect_labs.coverage.views import BaseCoverageView


def _make_view(session: dict):
    request = RequestFactory().get("/coverage/map/")
    request.session = session
    view = BaseCoverageView()
    view.request = request
    return view


class TestCheckCommcareOauth:
    def test_delegates_to_shared_refresh_first_helper(self):
        view = _make_view(session={"commcare_oauth": {"access_token": "expired", "expires_at": 0}})

        with patch("connect_labs.coverage.views.is_cchq_oauth_active", return_value=True) as mock_active:
            assert view.check_commcare_oauth() is True

        mock_active.assert_called_once_with(view.request)

    def test_returns_false_when_helper_reports_inactive(self):
        view = _make_view(session={"commcare_oauth": {}})

        with patch("connect_labs.coverage.views.is_cchq_oauth_active", return_value=False):
            assert view.check_commcare_oauth() is False
