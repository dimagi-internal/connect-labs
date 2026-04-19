import json

import pytest
from django.urls import reverse

from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


def _post(client, raw_token: str | None):
    headers = {}
    if raw_token is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {raw_token}"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return client.post(
        reverse("mcp:endpoint"),
        data=body,
        content_type="application/json",
        **headers,
    )


@pytest.mark.django_db
def test_missing_header_returns_401(client):
    resp = _post(client, None)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"
    assert "Bearer" in resp["WWW-Authenticate"]


@pytest.mark.django_db
def test_invalid_token_returns_401(client):
    resp = _post(client, "garbage-token")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_valid_token_returns_200(client):
    user = User.objects.create(username="mcp-auth-test")
    _, raw = MCPAccessToken.create_token(user, name="t")
    resp = _post(client, raw)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_revoked_token_returns_401(client):
    user = User.objects.create(username="revoked-test")
    token, raw = MCPAccessToken.create_token(user, name="t")
    token.is_active = False
    token.save()
    resp = _post(client, raw)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_valid_token_updates_last_used(client):
    from django.utils import timezone

    user = User.objects.create(username="last-used-test")
    token, raw = MCPAccessToken.create_token(user, name="t")
    assert token.last_used_at is None
    before = timezone.now()
    resp = _post(client, raw)
    assert resp.status_code == 200
    token.refresh_from_db()
    assert token.last_used_at is not None
    assert token.last_used_at >= before
