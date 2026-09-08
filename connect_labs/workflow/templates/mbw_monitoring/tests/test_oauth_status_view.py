"""Tests for MBWOAuthStatusView.

Exists because this endpoint used to duplicate a raw, no-refresh expires_at
check for all three OAuth providers — a near-exact structural copy of the
bug already fixed in workflow_auth_status_api, just never applied here. A
merely time-expired but refreshable token reported as inactive forces a
needless re-authorize banner on the MBW monitoring dashboard.
"""

import json
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django.utils import timezone

from connect_labs.users.tests.factories import UserFactory
from connect_labs.workflow.templates.mbw_monitoring.views import MBWOAuthStatusView


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def user(db):
    return UserFactory()


def _make_request(rf, user, session: dict):
    request = rf.get("/mbw/api/oauth-status/")
    request.user = user
    request.session = session
    return request


class TestMBWOAuthStatusView:
    def test_expired_commcare_token_refreshes_instead_of_reporting_inactive(self, rf, user):
        session = {
            "labs_oauth": {"access_token": "connect-tok", "expires_at": timezone.now().timestamp() + 3600},
            "commcare_oauth": {
                "access_token": "expired",
                "refresh_token": "r",
                "expires_at": timezone.now().timestamp() - 10,
            },
            "ocs_oauth": {},
        }
        request = _make_request(rf, user, session)

        with patch(
            "connect_labs.workflow.templates.mbw_monitoring.views.is_cchq_oauth_active", return_value=True
        ) as mock_cchq:
            response = MBWOAuthStatusView.as_view()(request)

        mock_cchq.assert_called_once_with(request)
        assert json.loads(response.content)["commcare"]["active"] is True

    def test_all_three_providers_report_active_via_their_helpers(self, rf, user):
        session = {"labs_oauth": {}, "commcare_oauth": {}, "ocs_oauth": {}}
        request = _make_request(rf, user, session)

        with (
            patch("connect_labs.workflow.templates.mbw_monitoring.views.is_connect_oauth_active", return_value=True),
            patch("connect_labs.workflow.templates.mbw_monitoring.views.is_cchq_oauth_active", return_value=False),
            patch("connect_labs.workflow.templates.mbw_monitoring.views.is_ocs_oauth_active", return_value=True),
        ):
            response = MBWOAuthStatusView.as_view()(request)

        data = json.loads(response.content)
        assert data["connect"]["active"] is True
        assert data["commcare"]["active"] is False
        assert data["ocs"]["active"] is True
