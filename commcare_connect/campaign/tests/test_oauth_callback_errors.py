"""Error-path coverage for the CommCare OAuth callback.

`test_oauth_views` covers the happy paths + bad-state + reuse-by-email. These cover
the failure branches that turn external problems into friendly errors WITHOUT
creating a session — important because a half-failed login that still logs the user
in would be a security hole.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.test import override_settings
from django.urls import reverse

from commcare_connect.campaign.models import CampaignUser

OAUTH_SETTINGS = dict(
    CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"],
    COMMCARE_OAUTH_CLIENT_ID="cid",
    COMMCARE_OAUTH_CLIENT_SECRET="sec",
    COMMCARE_HQ_URL="https://hq.example",
)


def _prime_pkce(client):
    s = client.session
    s["campaign_oauth_state"] = "STATE"
    s["campaign_oauth_code_verifier"] = "VERIFIER"
    s.save()


def _token_resp(status_code=200, payload=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = payload or {"access_token": "AT", "expires_in": 3600, "token_type": "Bearer"}
    return m


def _callback(client):
    return client.get(reverse("campaign:oauth_callback"), {"code": "C", "state": "STATE"})


@pytest.mark.django_db
@override_settings(**OAUTH_SETTINGS)
def test_token_exchange_rejected_returns_403_no_session(client):
    _prime_pkce(client)
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client:
        Client.return_value.__enter__.return_value.post.return_value = _token_resp(status_code=401)
        resp = _callback(client)
    assert resp.status_code == 403
    assert "campaign_oauth" not in client.session


@pytest.mark.django_db
@override_settings(**OAUTH_SETTINGS)
def test_token_exchange_network_error_returns_502(client):
    _prime_pkce(client)
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client:
        Client.return_value.__enter__.return_value.post.side_effect = httpx.RequestError("boom")
        resp = _callback(client)
    assert resp.status_code == 502
    assert "campaign_oauth" not in client.session


@pytest.mark.django_db
@override_settings(**OAUTH_SETTINGS)
def test_identity_failure_returns_403_no_session(client):
    from commcare_connect.campaign.auth.identity import IdentityError

    _prime_pkce(client)
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client, patch(
        "commcare_connect.campaign.auth.oauth_views.fetch_identity", side_effect=IdentityError("nope")
    ):
        Client.return_value.__enter__.return_value.post.return_value = _token_resp()
        resp = _callback(client)
    assert resp.status_code == 403
    assert "campaign_oauth" not in client.session


@pytest.mark.django_db
@override_settings(**OAUTH_SETTINGS)
def test_identity_without_username_returns_403(client):
    _prime_pkce(client)
    ident = {"username": "", "email": "x@dimagi.com", "name": "X", "domains": []}
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client, patch(
        "commcare_connect.campaign.auth.oauth_views.fetch_identity", return_value=ident
    ):
        Client.return_value.__enter__.return_value.post.return_value = _token_resp()
        resp = _callback(client)
    assert resp.status_code == 403
    assert "campaign_oauth" not in client.session


@pytest.mark.django_db
@override_settings(**OAUTH_SETTINGS)
def test_inactive_whitelist_row_is_hard_denied(client):
    # A deliberately-deactivated, non-bootstrap-domain user must be denied even though
    # a token + identity were obtained — resolve_campaign_user returns None for them.
    CampaignUser.objects.create(
        commcare_username="x@other.org",
        email="x@other.org",
        name="X",
        role="reporting_user",
        status=CampaignUser.Status.INACTIVE,
    )
    _prime_pkce(client)
    ident = {"username": "x@other.org", "email": "x@other.org", "name": "X", "domains": []}
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client, patch(
        "commcare_connect.campaign.auth.oauth_views.fetch_identity", return_value=ident
    ):
        Client.return_value.__enter__.return_value.post.return_value = _token_resp()
        resp = _callback(client)
    assert resp.status_code == 403
    assert "campaign_oauth" not in client.session
