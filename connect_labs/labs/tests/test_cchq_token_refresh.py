from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.utils import timezone

from connect_labs.labs.integrations.commcare.cchq_tokens import (
    CCHQReLoginRequired,
    CCHQTokenError,
    get_valid_cchq_access_token,
)
from connect_labs.labs.models import UserCCHQToken
from connect_labs.users.models import User

# Every test below that drives a retry path patches this too, so the
# _REFRESH_RETRY_BACKOFF_SECONDS * attempt sleeps between attempts don't
# actually slow the suite down.
_NO_SLEEP = patch("connect_labs.labs.integrations.commcare.cchq_tokens.time.sleep")


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
    """A dead refresh_token is never retried -- one call, straight to the
    re-login error, no wasted round trips or delay."""
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
    assert mock_post.call_count == 1


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
    assert mock_post.call_count == 1


@pytest.mark.django_db
@_NO_SLEEP
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_raises_generic_error_on_other_failure(mock_post, mock_sleep, settings):
    """A 503 on every attempt exhausts all retries, then raises -- proving
    the retry loop doesn't retry forever."""
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
    assert mock_post.call_count == 3


@pytest.mark.django_db
@_NO_SLEEP
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_recovers_from_transient_5xx_on_retry(mock_post, mock_sleep, settings):
    """The exact 2026-09-02 incident this retry exists for: CommCare HQ blips
    once (a 502) and succeeds on the very next attempt -- the caller should
    never see an error, and the refreshed token should still be persisted."""
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.side_effect = [
        MagicMock(status_code=502, text="bad gateway"),
        MagicMock(
            status_code=200,
            json=lambda: {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        ),
    ]
    user = User.objects.create(username="hank")
    UserCCHQToken.objects.create(
        user=user,
        access_token="expired",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    assert get_valid_cchq_access_token(user) == "new-access"
    assert mock_post.call_count == 2
    user.cchq_token.refresh_from_db()
    assert user.cchq_token.access_token == "new-access"


@pytest.mark.django_db
@_NO_SLEEP
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_recovers_from_network_error_on_retry(mock_post, mock_sleep, settings):
    """A connection-level failure (timeout, reset, DNS blip) is retried the
    same as a 5xx, not treated as an immediate re-login case."""
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.side_effect = [
        httpx.ConnectTimeout("timed out"),
        MagicMock(
            status_code=200,
            json=lambda: {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        ),
    ]
    user = User.objects.create(username="ivy")
    UserCCHQToken.objects.create(
        user=user,
        access_token="expired",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    assert get_valid_cchq_access_token(user) == "new-access"
    assert mock_post.call_count == 2


@pytest.mark.django_db
@_NO_SLEEP
@patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post")
def test_raises_after_exhausting_retries_on_network_error(mock_post, mock_sleep, settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"
    mock_post.side_effect = httpx.ConnectTimeout("timed out")
    user = User.objects.create(username="jill")
    UserCCHQToken.objects.create(
        user=user,
        access_token="expired",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with pytest.raises(CCHQTokenError, match="refresh-token exchange failed after retries"):
        get_valid_cchq_access_token(user)
    assert mock_post.call_count == 3


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
