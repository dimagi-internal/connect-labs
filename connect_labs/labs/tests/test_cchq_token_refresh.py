from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.integrations.commcare.cchq_tokens import (
    CCHQReLoginRequired,
    CCHQTokenError,
    get_valid_cchq_access_token,
)
from connect_labs.labs.models import UserCCHQToken
from connect_labs.users.models import User


@pytest.mark.django_db
def test_returns_current_token_when_fresh():
    user = User.objects.create(username="alice")
    UserCCHQToken.objects.create(
        user=user,
        access_token="fresh",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert get_valid_cchq_access_token(user) == "fresh"


@pytest.mark.django_db
def test_raises_when_no_token():
    user = User.objects.create(username="bob")
    with pytest.raises(CCHQTokenError, match="No CommCare HQ OAuth token"):
        get_valid_cchq_access_token(user)


@pytest.mark.django_db
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_refreshes_when_expired(mock_post, settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    )
    user = User.objects.create(username="carol")
    UserCCHQToken.objects.create(
        user=user,
        access_token="expired",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    assert get_valid_cchq_access_token(user) == "new-access"
    user.cchq_token.refresh_from_db()
    assert user.cchq_token.access_token == "new-access"
    assert user.cchq_token.refresh_token == "new-refresh"
    assert not user.cchq_token.is_expired


@pytest.mark.django_db
def test_raises_when_expired_and_no_refresh_token():
    user = User.objects.create(username="dave")
    UserCCHQToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(CCHQTokenError, match="re-authorize"):
        get_valid_cchq_access_token(user)


@pytest.mark.django_db
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_raises_re_login_required_on_400(mock_post, settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.return_value = MagicMock(status_code=400, text='{"error": "invalid_grant"}')
    user = User.objects.create(username="erin")
    UserCCHQToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(CCHQReLoginRequired, match="Re-authorize"):
        get_valid_cchq_access_token(user)


@pytest.mark.django_db
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_raises_re_login_required_on_401(mock_post, settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.return_value = MagicMock(status_code=401, text='{"error": "invalid_client"}')
    user = User.objects.create(username="frank")
    UserCCHQToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(CCHQReLoginRequired):
        get_valid_cchq_access_token(user)


@pytest.mark.django_db
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_raises_generic_error_on_other_failure(mock_post, settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.return_value = MagicMock(status_code=503, text="upstream down")
    user = User.objects.create(username="gina")
    UserCCHQToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(CCHQTokenError, match="refresh-token exchange failed"):
        get_valid_cchq_access_token(user)


@pytest.mark.django_db
def test_raises_when_client_credentials_missing(settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = ""
    settings.COMMCARE_OAUTH_CLIENT_SECRET = ""
    user = User.objects.create(username="henry")
    UserCCHQToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(CCHQTokenError, match="not configured"):
        get_valid_cchq_access_token(user)
