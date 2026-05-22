"""Tests for LabsOAuthSessionMiddleware.

Asserts the contract introduced by gh#198 follow-up: Django's auth state and
session.labs_oauth never disagree by the time a view runs. The middleware
should be a no-op for healthy sessions, transparently refresh the token via
the UserConnectToken refresh-token path when expired, and tear down Django
auth when it can't.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.utils import timezone

from commcare_connect.labs.connect_tokens import ConnectReLoginRequired
from commcare_connect.labs.models import UserConnectToken
from commcare_connect.labs.oauth_session import LabsOAuthSessionMiddleware
from commcare_connect.users.models import User


def _make_request(path: str, user, session_data: dict | None = None):
    factory = RequestFactory()
    request = factory.get(path)
    # SessionMiddleware lets us write to request.session.
    SessionMiddleware(lambda r: None).process_request(request)
    if session_data:
        request.session.update(session_data)
        request.session.save()
    request.user = user
    return request


def _run(request):
    mw = LabsOAuthSessionMiddleware(lambda r: HttpResponse())
    return mw(request)


def _future_ts(hours=1):
    return (timezone.now() + timedelta(hours=hours)).timestamp()


def _past_ts(hours=1):
    return (timezone.now() - timedelta(hours=hours)).timestamp()


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True)
def test_fresh_session_token_is_no_op():
    user = User.objects.create(username="alice")
    request = _make_request(
        "/audit/",
        user,
        {"labs_oauth": {"access_token": "fresh", "expires_at": _future_ts()}},
    )

    _run(request)

    assert request.user.is_authenticated
    assert request.session["labs_oauth"]["access_token"] == "fresh"


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True)
def test_expired_session_token_refreshed_from_fresh_db_token():
    """Session expired but UserConnectToken in DB is still good — no HTTP call needed."""
    user = User.objects.create(username="bob")
    UserConnectToken.objects.create(
        user=user,
        access_token="db-fresh",
        refresh_token="r",
        expires_at=timezone.now() + timedelta(hours=2),
    )
    request = _make_request(
        "/solicitations/",
        user,
        {
            "labs_oauth": {
                "access_token": "session-stale",
                "expires_at": _past_ts(),
                "organization_data": {"orgs": ["preserved"]},
            }
        },
    )

    _run(request)

    assert request.user.is_authenticated
    assert request.session["labs_oauth"]["access_token"] == "db-fresh"
    assert request.session["labs_oauth"]["expires_at"] > timezone.now().timestamp()
    # Unrelated session payload survives the refresh.
    assert request.session["labs_oauth"]["organization_data"] == {"orgs": ["preserved"]}


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True, CONNECT_OAUTH_CLIENT_ID="test")
@patch("commcare_connect.labs.connect_tokens.httpx.post")
def test_expired_session_and_db_token_triggers_refresh_call(mock_post):
    """Session expired AND DB token expired but refresh_token works — HTTP call fires."""
    mock_post.return_value = MagicMock(
        is_success=True,
        json=lambda: {"access_token": "minted", "refresh_token": "next", "expires_in": 3600},
    )
    user = User.objects.create(username="carol")
    UserConnectToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="still-good",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    request = _make_request(
        "/audit/",
        user,
        {"labs_oauth": {"access_token": "old", "expires_at": _past_ts()}},
    )

    _run(request)

    assert mock_post.called
    assert request.user.is_authenticated
    assert request.session["labs_oauth"]["access_token"] == "minted"


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True, CONNECT_OAUTH_CLIENT_ID="test")
@patch("commcare_connect.labs.connect_tokens._exchange_refresh_token")
def test_refresh_failure_logs_user_out(mock_exchange):
    mock_exchange.side_effect = ConnectReLoginRequired("refresh rejected by provider")
    user = User.objects.create(username="dave")
    UserConnectToken.objects.create(
        user=user,
        access_token="old",
        refresh_token="revoked",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    request = _make_request(
        "/solicitations/",
        user,
        {"labs_oauth": {"access_token": "old", "expires_at": _past_ts()}},
    )

    _run(request)

    assert not request.user.is_authenticated


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True)
def test_no_user_connect_token_logs_user_out():
    """Authenticated Django session but no DB token (e.g., legacy session) → logout."""
    user = User.objects.create(username="eve")
    request = _make_request(
        "/solicitations/",
        user,
        {"labs_oauth": {"access_token": "stale", "expires_at": _past_ts()}},
    )

    _run(request)

    assert not request.user.is_authenticated


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True)
def test_missing_labs_oauth_payload_logs_user_out():
    """Django session has user but no labs_oauth shape at all → logout."""
    user = User.objects.create(username="frank")
    request = _make_request("/audit/", user, session_data=None)

    _run(request)

    assert not request.user.is_authenticated


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True)
def test_anonymous_user_is_no_op():
    """Anonymous requests aren't touched — login mixins handle them."""
    from django.contrib.auth.models import AnonymousUser

    request = _make_request("/solicitations/", AnonymousUser())

    _run(request)

    assert not request.user.is_authenticated  # still anon, no error raised


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=True)
@pytest.mark.parametrize(
    "skip_path",
    [
        "/labs/login/",
        "/labs/callback/",
        "/labs/logout/",
        "/labs/test-auth/",
        "/labs/commcare/initiate/",
        "/labs/ocs/callback/",
        "/mcp/",
        "/admin/users/",
    ],
)
def test_oauth_flow_paths_are_skipped(skip_path):
    """Mid-OAuth-flow URLs must NOT log the user out, even with a broken session.

    The OAuth callback path is the *one* where you legitimately arrive with no
    labs_oauth yet — running the logout-on-missing-payload branch there would
    immediately undo the login that's about to happen.
    """
    user = User.objects.create(username=f"u{skip_path.replace('/', '_')}")
    request = _make_request(skip_path, user, session_data=None)  # no labs_oauth

    _run(request)

    assert request.user.is_authenticated  # untouched


@pytest.mark.django_db
@override_settings(IS_LABS_ENVIRONMENT=False)
def test_disabled_outside_labs_environment():
    """Non-labs deploys (the prod connect server itself) must not run this middleware."""
    user = User.objects.create(username="non-labs")
    request = _make_request("/audit/", user, session_data=None)  # no labs_oauth

    _run(request)

    assert request.user.is_authenticated
