"""Tests for LabsOverviewView's OAuth status badges.

Exists because the main Labs landing page ("Connect/CommCare HQ/OCS:
Authorized" badges) used to each duplicate a raw, no-refresh expires_at
check — reporting a merely time-expired but refreshable token as
disconnected. This is the page users actually look at to answer "am I
connected right now", so it's the highest-value place to apply the same
refresh-first treatment already used by workflow_auth_status_api.
"""

from unittest.mock import patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.views import LabsOverviewView
from connect_labs.users.tests.factories import UserFactory


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def user(db):
    return UserFactory()


def _make_view(rf, user, session: dict):
    request = rf.get("/labs/overview/")
    request.user = user
    request.session = session
    request.labs_context = {}
    view = LabsOverviewView()
    view.request = request
    view.kwargs = {}
    return view


class TestLabsOverviewOauthBadges:
    def test_badges_use_the_refresh_first_helpers_not_a_raw_expiry_check(self, rf, user):
        view = _make_view(rf, user, session={"labs_oauth": {}, "commcare_oauth": {}, "ocs_oauth": {}})

        with (
            patch("connect_labs.labs.views.is_connect_oauth_active", return_value=True) as mock_connect,
            patch("connect_labs.labs.views.is_cchq_oauth_active", return_value=False) as mock_cchq,
            patch("connect_labs.labs.views.is_ocs_oauth_active", return_value=True) as mock_ocs,
        ):
            context = view.get_context_data()

        mock_connect.assert_called_once_with(view.request)
        mock_cchq.assert_called_once_with(view.request)
        mock_ocs.assert_called_once_with(view.request)
        assert context["connect_oauth_active"] is True
        assert context["commcare_oauth_active"] is False
        assert context["ocs_oauth_active"] is True
