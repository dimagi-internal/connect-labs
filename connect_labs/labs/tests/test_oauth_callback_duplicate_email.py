"""A Connect sign-in whose email already belongs to another Labs user gets a clear
refusal, not a 500 from the unique_user_email constraint."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.models import UserConnectToken
from connect_labs.users.models import User


def _request():
    request = RequestFactory().get("/labs/callback/", {"state": "s", "code": "c"})
    request.session = {"oauth_state": "s", "oauth_code_verifier": "v", "oauth_next": "/labs/overview/"}
    return request


def _token():
    mock = MagicMock()
    mock.json.return_value = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    return mock


def _userinfo(email):
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"email": email}
    return mock


def _callback(profile):
    with (
        patch("httpx.post", return_value=_token()),
        patch("httpx.get", return_value=_userinfo(profile["email"])),
        patch("connect_labs.labs.integrations.connect.oauth_views.introspect_token", return_value=dict(profile)),
        patch(
            "connect_labs.labs.integrations.connect.oauth_views.fetch_user_organization_data",
            return_value={"organizations": [], "programs": [], "opportunities": [], "user": {}},
        ),
        patch("connect_labs.labs.integrations.connect.oauth_views.login") as login,
        patch("connect_labs.labs.integrations.connect.oauth_views.messages") as messages,
    ):
        from connect_labs.labs.integrations.connect.oauth_views import labs_oauth_callback

        request = _request()
        response = labs_oauth_callback(request)
    return request, response, login, messages


@pytest.mark.django_db
def test_email_held_by_another_username_is_refused_clearly():
    holder = User.objects.create(username="person@example.com", email="person@example.com")

    request, response, login, messages = _callback({"id": 1, "username": "person", "email": "person@example.com"})

    assert response.status_code == 302
    assert response.url.endswith("/labs/login/")
    login.assert_not_called()
    assert not User.objects.filter(username="person").exists()
    assert not UserConnectToken.objects.exists()
    assert "labs_oauth" not in request.session
    text = messages.error.call_args.args[1]
    assert "person@example.com" in text and "different Labs account" in text
    holder.refresh_from_db()
    assert holder.username == "person@example.com"


@pytest.mark.django_db
def test_email_match_is_case_insensitive():
    User.objects.create(username="other", email="Person@Example.com")

    _, response, login, messages = _callback({"id": 1, "username": "person", "email": "person@example.com"})

    login.assert_not_called()
    messages.error.assert_called_once()


@pytest.mark.django_db
def test_returning_user_with_own_email_still_signs_in():
    User.objects.create(username="person", email="person@example.com")

    _, response, login, messages = _callback({"id": 1, "username": "person", "email": "person@example.com"})

    login.assert_called_once()
    messages.error.assert_not_called()
