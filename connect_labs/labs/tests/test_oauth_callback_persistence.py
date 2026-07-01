"""Verifies labs_oauth_callback persists a UserConnectToken row on successful login."""
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.models import UserConnectToken
from connect_labs.users.models import User


def _make_token_response():
    """Return a mock httpx response for the token exchange endpoint."""
    mock = MagicMock()
    mock.json.return_value = {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    mock.raise_for_status = MagicMock()
    return mock


def _make_userinfo_response():
    """Return a mock httpx response for the OIDC userinfo endpoint."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"email": "testuser@example.com"}
    return mock


_PROFILE_DATA = {
    "id": 42,
    "username": "testuser",
    "email": "testuser@example.com",
    "first_name": "Test",
    "last_name": "User",
}

_ORG_DATA = {
    "organizations": [],
    "programs": [],
    "opportunities": [],
    "user": {"email": "testuser@example.com", "commcare_username": "testuser"},
}


@pytest.mark.django_db
def test_callback_persists_connect_token():
    """After a successful OAuth callback, a UserConnectToken row is upserted for the user."""
    factory = RequestFactory()
    request = factory.get("/labs/callback/", {"state": "test-state", "code": "auth-code-123"})
    request.session = {
        "oauth_state": "test-state",
        "oauth_code_verifier": "pkce-verifier-value",
        "oauth_next": "/labs/overview/",
    }

    with (
        patch("httpx.post", return_value=_make_token_response()),
        patch("httpx.get", return_value=_make_userinfo_response()),
        patch(
            "connect_labs.labs.integrations.connect.oauth_views.introspect_token",
            return_value=_PROFILE_DATA,
        ),
        patch(
            "connect_labs.labs.integrations.connect.oauth_views.fetch_user_organization_data",
            return_value=_ORG_DATA,
        ),
        patch("connect_labs.labs.integrations.connect.oauth_views.login"),
        patch("connect_labs.labs.integrations.connect.oauth_views.messages"),
    ):
        from connect_labs.labs.integrations.connect.oauth_views import labs_oauth_callback

        response = labs_oauth_callback(request)

    assert response.status_code == 302

    # A UserConnectToken row must exist for the user created by the callback.
    user = User.objects.get(username="testuser")
    assert UserConnectToken.objects.filter(
        user=user
    ).exists(), "Expected a UserConnectToken to be upserted for the authenticated user"
    token = UserConnectToken.objects.get(user=user)
    assert token.access_token == "test-access-token"
    assert token.refresh_token == "test-refresh-token"
    assert token.expires_at is not None


@pytest.mark.django_db
def test_callback_upserts_token_on_repeated_login():
    """Repeated callbacks update the existing UserConnectToken rather than creating a second one."""
    # Pre-create the user to simulate a returning user.
    existing_user = User.objects.create(username="testuser2", email="testuser2@example.com")

    profile_data = {**_PROFILE_DATA, "username": "testuser2", "email": "testuser2@example.com"}
    org_data = {**_ORG_DATA, "user": {"email": "testuser2@example.com", "commcare_username": "testuser2"}}

    def _run_callback(access_token, refresh_token):
        factory = RequestFactory()
        request = factory.get("/labs/callback/", {"state": "state-abc", "code": "code-xyz"})
        request.session = {
            "oauth_state": "state-abc",
            "oauth_code_verifier": "verifier-xyz",
            "oauth_next": "/labs/overview/",
        }

        token_response = MagicMock()
        token_response.json.return_value = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 7200,
        }
        token_response.raise_for_status = MagicMock()

        userinfo_response = MagicMock()
        userinfo_response.status_code = 200
        userinfo_response.json.return_value = {"email": profile_data["email"]}

        with (
            patch("httpx.post", return_value=token_response),
            patch("httpx.get", return_value=userinfo_response),
            patch(
                "connect_labs.labs.integrations.connect.oauth_views.introspect_token",
                return_value=profile_data,
            ),
            patch(
                "connect_labs.labs.integrations.connect.oauth_views.fetch_user_organization_data",
                return_value=org_data,
            ),
            patch("connect_labs.labs.integrations.connect.oauth_views.login"),
            patch("connect_labs.labs.integrations.connect.oauth_views.messages"),
        ):
            from connect_labs.labs.integrations.connect.oauth_views import labs_oauth_callback

            labs_oauth_callback(request)

    _run_callback("first-access-token", "first-refresh-token")
    _run_callback("second-access-token", "second-refresh-token")

    # Only one row should exist (OneToOne upsert).
    assert UserConnectToken.objects.filter(user=existing_user).count() == 1
    token = UserConnectToken.objects.get(user=existing_user)
    assert token.access_token == "second-access-token"
    assert token.refresh_token == "second-refresh-token"
