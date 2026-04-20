from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from commcare_connect.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from commcare_connect.labs.models import UserConnectToken
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_returns_current_token_when_fresh():
    user = User.objects.create(username="alice")
    UserConnectToken.objects.create(
        user=user,
        access_token="fresh",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert get_valid_access_token(user) == "fresh"


@pytest.mark.django_db
def test_raises_when_no_token():
    user = User.objects.create(username="bob")
    with pytest.raises(ConnectTokenError, match="No Connect OAuth token"):
        get_valid_access_token(user)


@pytest.mark.django_db
@patch("commcare_connect.labs.connect_tokens.httpx.post")
def test_refreshes_when_expired(mock_post, settings):
    settings.CONNECT_OAUTH_CLIENT_ID = "test-client"
    mock_post.return_value = MagicMock(
        ok=True,
        json=lambda: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    )
    user = User.objects.create(username="carol")
    UserConnectToken.objects.create(
        user=user,
        access_token="expired",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    assert get_valid_access_token(user) == "new-access"
    user.connect_token.refresh_from_db()
    assert user.connect_token.access_token == "new-access"
    assert user.connect_token.refresh_token == "new-refresh"
    assert not user.connect_token.is_expired


@pytest.mark.django_db
def test_raises_when_expired_and_no_refresh_token():
    user = User.objects.create(username="dave")
    UserConnectToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(ConnectTokenError, match="User must log in again"):
        get_valid_access_token(user)


@pytest.mark.django_db
@patch("commcare_connect.labs.connect_tokens.httpx.post")
def test_raises_when_refresh_exchange_fails(mock_post, settings):
    settings.CONNECT_OAUTH_CLIENT_ID = "test-client"
    mock_post.return_value = MagicMock(ok=False, status_code=400, text="bad")
    user = User.objects.create(username="erin")
    UserConnectToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(ConnectTokenError, match="refresh-token exchange failed"):
        get_valid_access_token(user)
