"""A repeated OAuth callback after a successful one must not show an error.

A double-click on Authorize, a refresh or a browser retry sends the callback twice.
The first consumes the state; the second used to land the user on "Invalid
authentication state" while they were in fact connected. A replay of the SAME state
now redirects quietly; any other state is still rejected.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.integrations.commcare.oauth_views import labs_commcare_callback
from connect_labs.labs.integrations.connect.oauth_views import labs_oauth_callback
from connect_labs.users.models import User

CONNECT = "connect_labs.labs.integrations.connect.oauth_views"
COMMCARE = "connect_labs.labs.integrations.commcare.oauth_views"


def _token_response():
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600}
    mock.raise_for_status = MagicMock()
    return mock


def _request(path, state, session, user=None):
    request = RequestFactory().get(path, {"state": state, "code": "code-1"})
    request.session = session
    if user is not None:
        request.user = user
    return request


@pytest.mark.django_db
def test_connect_callback_replay_after_success_redirects_quietly():
    session = {"oauth_state": "s1", "oauth_code_verifier": "v", "oauth_next": "/labs/workflow/"}
    userinfo = MagicMock(status_code=200)
    userinfo.json.return_value = {}
    profile = {"id": 1, "username": "replayer", "email": "r@example.com"}

    with (
        patch("httpx.post", return_value=_token_response()),
        patch("httpx.get", return_value=userinfo),
        patch(f"{CONNECT}.introspect_token", return_value=profile),
        patch(f"{CONNECT}.fetch_user_organization_data", return_value={"organizations": []}),
        patch(f"{CONNECT}.login"),
        patch(f"{CONNECT}.messages") as messages,
    ):
        first = labs_oauth_callback(_request("/labs/callback/", "s1", session))
        second = labs_oauth_callback(_request("/labs/callback/", "s1", session))

    assert first.url == "/labs/workflow/"
    assert second.url == "/labs/workflow/"
    messages.error.assert_not_called()


def test_connect_callback_unknown_state_still_rejected():
    session = {"oauth_completed": {"state": "s1", "next": "/x/"}, "labs_oauth": {"access_token": "t"}}
    with patch(f"{CONNECT}.messages") as messages:
        response = labs_oauth_callback(_request("/labs/callback/", "other", session))

    messages.error.assert_called_once()
    assert response.url != "/x/"


@pytest.mark.django_db
def test_commcare_callback_replay_after_success_redirects_quietly(settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "cid"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "secret"
    user = User.objects.create(username="cc-replayer")
    session = {
        "commcare_oauth_state": "s1",
        "commcare_oauth_code_verifier": "v",
        "commcare_oauth_next": "/labs/workflow/20163/run/",
    }
    client = MagicMock()
    client.__enter__.return_value.post.return_value = _token_response()

    with (
        patch(f"{COMMCARE}.httpx.Client", return_value=client),
        patch(f"{COMMCARE}.messages") as messages,
    ):
        first = labs_commcare_callback(_request("/labs/commcare/callback/", "s1", session, user))
        second = labs_commcare_callback(_request("/labs/commcare/callback/", "s1", session, user))

    assert first.url == "/labs/workflow/20163/run/"
    assert second.url == "/labs/workflow/20163/run/"
    messages.error.assert_not_called()


def test_commcare_callback_unknown_state_still_rejected():
    session = {"commcare_oauth_completed": {"state": "s1", "next": "/x/"}, "commcare_oauth": {"access_token": "t"}}
    with patch(f"{COMMCARE}.messages") as messages:
        response = labs_commcare_callback(_request("/labs/commcare/callback/", "other", session))

    messages.error.assert_called_once()
    assert response.url == "/audit/"
