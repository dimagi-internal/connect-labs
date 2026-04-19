from datetime import timedelta

import pytest
from django.utils import timezone

from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_create_token_returns_raw_and_persists_hash():
    user = User.objects.create(username="alice")
    token, raw = MCPAccessToken.create_token(user, name="laptop")
    assert raw
    assert len(raw) > 30  # token_urlsafe(32) → ~43 chars
    assert token.token_hash != raw


@pytest.mark.django_db
def test_verify_returns_token_for_valid_raw():
    user = User.objects.create(username="bob")
    _, raw = MCPAccessToken.create_token(user, name="cli")
    verified = MCPAccessToken.verify(raw)
    assert verified is not None
    assert verified.user == user


@pytest.mark.django_db
def test_verify_returns_none_for_unknown():
    assert MCPAccessToken.verify("not-a-real-token") is None


@pytest.mark.django_db
def test_verify_returns_none_for_inactive():
    user = User.objects.create(username="carol")
    token, raw = MCPAccessToken.create_token(user, name="revoked")
    token.is_active = False
    token.save()
    assert MCPAccessToken.verify(raw) is None


@pytest.mark.django_db
def test_verify_returns_none_for_expired():
    user = User.objects.create(username="dave")
    token, raw = MCPAccessToken.create_token(user, name="old", ttl_days=1)
    token.expires_at = timezone.now() - timedelta(days=2)
    token.save()
    assert MCPAccessToken.verify(raw) is None


@pytest.mark.django_db
def test_touch_updates_last_used_at():
    user = User.objects.create(username="erin")
    token, _ = MCPAccessToken.create_token(user, name="t")
    assert token.last_used_at is None
    token.touch()
    token.refresh_from_db()
    assert token.last_used_at is not None
