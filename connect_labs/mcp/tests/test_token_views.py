"""Tests for the self-service MCP token UI at /labs/mcp/tokens/."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.snippets import build_mcp_json_snippet
from connect_labs.users.models import User


@pytest.fixture
def alice(client, db):
    user = User.objects.create(username="alice")
    client.force_login(user)
    return user


@pytest.fixture
def bob(db):
    return User.objects.create(username="bob")


@pytest.mark.django_db
def test_index_requires_login(client):
    resp = client.get(reverse("labs:mcp_tokens_index"))
    assert resp.status_code == 302
    assert "/labs/login/" in resp["Location"]


@pytest.mark.django_db
def test_index_lists_only_own_tokens(client, alice, bob):
    MCPAccessToken.create_token(alice, name="alice-laptop")
    MCPAccessToken.create_token(bob, name="bob-laptop")

    resp = client.get(reverse("labs:mcp_tokens_index"))
    assert resp.status_code == 200
    assert b"alice-laptop" in resp.content
    assert b"bob-laptop" not in resp.content


@pytest.mark.django_db
def test_index_hides_inactive_tokens(client, alice):
    MCPAccessToken.create_token(alice, name="active-one")
    revoked, _ = MCPAccessToken.create_token(alice, name="revoked-one")
    revoked.is_active = False
    revoked.save()

    resp = client.get(reverse("labs:mcp_tokens_index"))
    assert b"active-one" in resp.content
    assert b"revoked-one" not in resp.content


@pytest.mark.django_db
def test_create_renders_raw_token_and_snippet(client, alice):
    resp = client.post(
        reverse("labs:mcp_tokens_create"),
        {"name": "my-laptop", "ttl_days": "30"},
    )
    assert resp.status_code == 200
    token = MCPAccessToken.objects.get(user=alice, name="my-laptop")
    assert token.expires_at is not None

    # The raw token is exposed exactly once via the response context.
    raw = resp.context["raw_token"]
    assert raw
    # Raw token round-trips through verify().
    assert MCPAccessToken.verify(raw) == token
    # Snippet is the same shape the management command emits.
    assert resp.context["mcp_json_snippet"] == build_mcp_json_snippet(raw)
    assert raw.encode() in resp.content


@pytest.mark.django_db
def test_create_zero_ttl_means_no_expiry(client, alice):
    client.post(
        reverse("labs:mcp_tokens_create"),
        {"name": "forever", "ttl_days": "0"},
    )
    token = MCPAccessToken.objects.get(user=alice, name="forever")
    assert token.expires_at is None


@pytest.mark.django_db
def test_create_default_ttl_when_blank(client, alice):
    client.post(
        reverse("labs:mcp_tokens_create"),
        {"name": "default-ttl", "ttl_days": ""},
    )
    token = MCPAccessToken.objects.get(user=alice, name="default-ttl")
    assert token.expires_at is not None
    # Should be ~90 days out — give a generous window for clock drift in CI.
    delta = token.expires_at - timezone.now()
    assert timedelta(days=89) <= delta <= timedelta(days=91)


@pytest.mark.django_db
def test_create_rejects_blank_name(client, alice):
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "  "})
    assert resp.status_code == 302
    assert MCPAccessToken.objects.filter(user=alice).count() == 0


@pytest.mark.django_db
def test_create_requires_post(client, alice):
    resp = client.get(reverse("labs:mcp_tokens_create"))
    assert resp.status_code == 405


@pytest.mark.django_db
def test_revoke_own_token(client, alice):
    token, raw = MCPAccessToken.create_token(alice, name="kill-me")
    resp = client.post(reverse("labs:mcp_tokens_revoke", args=[token.pk]))
    assert resp.status_code == 302
    token.refresh_from_db()
    assert token.is_active is False
    # In-flight calls 401 immediately.
    assert MCPAccessToken.verify(raw) is None


@pytest.mark.django_db
def test_revoke_other_users_token_is_404(client, alice, bob):
    bob_token, _ = MCPAccessToken.create_token(bob, name="bob-token")
    resp = client.post(reverse("labs:mcp_tokens_revoke", args=[bob_token.pk]))
    assert resp.status_code == 404
    bob_token.refresh_from_db()
    assert bob_token.is_active is True


@pytest.mark.django_db
def test_revoke_requires_post(client, alice):
    token, _ = MCPAccessToken.create_token(alice, name="x")
    resp = client.get(reverse("labs:mcp_tokens_revoke", args=[token.pk]))
    assert resp.status_code == 405
    token.refresh_from_db()
    assert token.is_active is True


@pytest.mark.django_db
def test_rotate_revokes_old_and_creates_new(client, alice):
    old, old_raw = MCPAccessToken.create_token(alice, name="laptop")

    resp = client.post(reverse("labs:mcp_tokens_rotate", args=[old.pk]))
    assert resp.status_code == 200

    old.refresh_from_db()
    assert old.is_active is False
    assert MCPAccessToken.verify(old_raw) is None

    new_raw = resp.context["raw_token"]
    new_token = MCPAccessToken.verify(new_raw)
    assert new_token is not None
    assert new_token.user == alice
    assert new_token.name == "laptop"
    assert new_token.pk != old.pk


@pytest.mark.django_db
def test_rotate_other_users_token_is_404(client, alice, bob):
    bob_token, _ = MCPAccessToken.create_token(bob, name="bob-token")
    resp = client.post(reverse("labs:mcp_tokens_rotate", args=[bob_token.pk]))
    assert resp.status_code == 404
    bob_token.refresh_from_db()
    assert bob_token.is_active is True
    assert MCPAccessToken.objects.filter(user=alice).count() == 0
