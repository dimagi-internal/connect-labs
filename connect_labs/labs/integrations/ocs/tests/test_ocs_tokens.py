"""A user's OCS token without a browser: stored, mirrored, refreshed, forgotten."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from connect_labs.labs.integrations.ocs import ocs_tokens
from connect_labs.labs.integrations.ocs.api_client import OCSDataAccess
from connect_labs.labs.models import UserOCSToken

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="manager", password="p")


@pytest.fixture(autouse=True)
def ocs_settings(settings):
    settings.OCS_OAUTH_CLIENT_ID = "cid"
    settings.OCS_OAUTH_CLIENT_SECRET = "sec"
    settings.OCS_URL = "https://ocs.test"


def _row(user, *, expires_in=3600, access="a1", refresh="r1"):
    return UserOCSToken.objects.create(
        user=user,
        access_token=access,
        refresh_token=refresh,
        expires_at=timezone.now() + timedelta(seconds=expires_in),
    )


def _refreshed(access="a2", refresh="r2"):
    response = MagicMock(status_code=200)
    response.json.return_value = {"access_token": access, "refresh_token": refresh, "expires_in": 3600}
    return response


def test_a_live_stored_token_is_used_as_is(user):
    _row(user)
    assert ocs_tokens.get_valid_ocs_access_token(user) == "a1"


def test_an_expired_stored_token_is_refreshed_and_the_rotation_kept(user):
    _row(user, expires_in=-10)
    with patch("httpx.post", return_value=_refreshed()) as post:
        assert ocs_tokens.get_valid_ocs_access_token(user) == "a2"
    assert post.call_args.kwargs["data"]["refresh_token"] == "r1"
    row = UserOCSToken.objects.get(user=user)
    assert (row.access_token, row.refresh_token) == ("a2", "r2")


def test_a_refused_refresh_means_connect_again(user):
    _row(user, expires_in=-10)
    with patch("httpx.post", return_value=MagicMock(status_code=400, text="invalid_grant")):
        with pytest.raises(ocs_tokens.OCSReLoginRequired, match="/labs/ocs/initiate/"):
            ocs_tokens.get_valid_ocs_access_token(user)


def test_no_stored_token_says_where_to_connect(user):
    with pytest.raises(ocs_tokens.OCSTokenError, match="/labs/ocs/initiate/"):
        ocs_tokens.get_valid_ocs_access_token(user)


def test_a_client_for_a_user_with_no_request_uses_their_stored_token(user):
    _row(user)
    client = OCSDataAccess(user=user)
    assert client.access_token == "a1"
    assert client.check_token_valid() is True


def test_a_session_token_with_no_row_becomes_the_row(user):
    """Someone who connected OCS before rows existed can still be acted for headlessly."""
    request = RequestFactory().get("/")
    request.user = user
    expires = (timezone.now() + timedelta(hours=1)).timestamp()
    request.session = {"ocs_oauth": {"access_token": "sess", "refresh_token": "sr", "expires_at": expires}}
    OCSDataAccess(request)
    row = UserOCSToken.objects.get(user=user)
    assert (row.access_token, row.refresh_token) == ("sess", "sr")


def test_a_refresh_from_the_session_writes_the_row_and_the_session(user):
    _row(user, expires_in=-10, access="old", refresh="r1")
    request = RequestFactory().get("/")
    request.user = user
    request.session = {"ocs_oauth": {"access_token": "old", "refresh_token": "r1", "expires_at": 0}}
    with patch("httpx.post", return_value=_refreshed()):
        assert OCSDataAccess(request).check_token_valid() is True
    assert request.session["ocs_oauth"]["access_token"] == "a2"
    assert UserOCSToken.objects.get(user=user).access_token == "a2"


def test_disconnecting_forgets_the_stored_token(user, client):
    _row(user)
    client.force_login(user)
    client.get("/labs/ocs/logout/")
    assert not UserOCSToken.objects.filter(user=user).exists()
