from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

from commcare_connect.campaign.models import CampaignUser
from commcare_connect.users.models import User

TOKEN = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "token_type": "Bearer"}


def _token_resp():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = TOKEN
    return m


def _prime_pkce(client):
    """Seed session as if /initiate/ ran (state + verifier)."""
    s = client.session
    s["campaign_oauth_state"] = "STATE"
    s["campaign_oauth_code_verifier"] = "VERIFIER"
    s.save()


@pytest.mark.django_db
def test_initiate_redirects_to_commcare(client):
    with override_settings(COMMCARE_OAUTH_CLIENT_ID="cid", COMMCARE_HQ_URL="https://hq.example"):
        resp = client.get(reverse("campaign:oauth_initiate"))
    assert resp.status_code == 302
    assert resp.url.startswith("https://hq.example/oauth/authorize/")
    assert "code_challenge=" in resp.url


@pytest.mark.django_db
@override_settings(
    CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"],
    COMMCARE_OAUTH_CLIENT_ID="cid",
    COMMCARE_OAUTH_CLIENT_SECRET="sec",
    COMMCARE_HQ_URL="https://hq.example",
)
def test_callback_provisions_dimagi_admin(client):
    _prime_pkce(client)
    ident = {"username": "a@dimagi.com", "email": "a@dimagi.com", "name": "A", "domains": []}
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client, patch(
        "commcare_connect.campaign.auth.oauth_views.fetch_identity", return_value=ident
    ):
        Client.return_value.__enter__.return_value.post.return_value = _token_resp()
        resp = client.get(reverse("campaign:oauth_callback"), {"code": "C", "state": "STATE"})
    assert resp.status_code == 302
    assert resp.url == reverse("campaign:app")
    assert User.objects.filter(username="a@dimagi.com").exists()
    assert CampaignUser.objects.get(commcare_username="a@dimagi.com").role == "campaign_admin"
    assert client.session["campaign_oauth"]["access_token"] == "AT"


@pytest.mark.django_db
@override_settings(
    CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"],
    COMMCARE_OAUTH_CLIENT_ID="cid",
    COMMCARE_OAUTH_CLIENT_SECRET="sec",
    COMMCARE_HQ_URL="https://hq.example",
)
def test_callback_denies_unlisted_user(client):
    _prime_pkce(client)
    ident = {"username": "x@other.org", "email": "x@other.org", "name": "X", "domains": []}
    with patch("commcare_connect.campaign.auth.oauth_views.httpx.Client") as Client, patch(
        "commcare_connect.campaign.auth.oauth_views.fetch_identity", return_value=ident
    ):
        Client.return_value.__enter__.return_value.post.return_value = _token_resp()
        resp = client.get(reverse("campaign:oauth_callback"), {"code": "C", "state": "STATE"})
    assert resp.status_code == 403
    assert "campaign_oauth" not in client.session


@pytest.mark.django_db
def test_callback_rejects_bad_state(client):
    _prime_pkce(client)
    resp = client.get(reverse("campaign:oauth_callback"), {"code": "C", "state": "WRONG"})
    assert resp.status_code == 400
    assert "campaign_oauth" not in client.session
