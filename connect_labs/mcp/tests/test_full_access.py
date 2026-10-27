"""A full-access MCP credential needs its person's passkey approval through canopy
(``connect_labs/mcp/full_access.py``).

canopy is stood in for at the SDK's boundary (``canopy_sdk.django.sign_offs``): what it
is asked, and what its approval says. The SDK's own tests cover signing and checking
the approval itself.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from canopy_sdk.sign_offs import Approval, SignOffError, SignOffRequest, SignOffStatus
from django.urls import reverse

from connect_labs.mcp import full_access, oauth, token_scopes
from connect_labs.mcp.models import FullAccessSelfMint, MCPAccessToken, PendingFullAccess
from connect_labs.mcp.testing import allow_self_mint, mint_token
from connect_labs.mcp.tests.test_oauth import LOOPBACK, _authorize_params, _pkce, _register
from connect_labs.users.models import User

APPROVE_URL = "https://canopy.example/w/connect/sign-offs/so_1"
CALLBACK = "http://127.0.0.1:8765/cb"
STATE = "s" * 16


@pytest.fixture
def alice(client, db):
    user = User.objects.create(username="alice", email="alice@dimagi.com")
    client.force_login(user)
    return user


class Canopy:
    """What canopy was asked, and what it answers."""

    def __init__(self, approvers="subject", refuse=None):
        self.asked = []
        self.approvers = approvers
        self.refuse = refuse
        self.state = "open"

    def request(self, **kw):
        if self.refuse:
            raise SignOffError(self.refuse, f"{self.refuse}: canopy says no", status=422)
        self.asked.append(kw)
        return SignOffRequest(
            id=f"so_{len(self.asked)}",
            approve_url=APPROVE_URL,
            expires_at="2026-10-27T12:15:00Z",
            approvers=self.approvers,
        )

    def verify(self, token, *, action_key, site_ref, subject=None):
        # The approval canopy signed names the pending mint it was asked about.
        if token != f"approval-for-{site_ref}" or action_key != full_access.ACTION_KEY:
            raise SignOffError("wrong_site_ref", "the sign-off is for a different pending action")
        return Approval(
            id="so_1",
            action_key=action_key,
            site_ref=site_ref,
            subject=subject or "",
            approver_email="owner@dimagi.com",
            approver_name="Owner",
            approved_at="",
            step_up="passkey",
            jti=f"jti-{site_ref}",
            expires_at=0,
        )

    def status(self, sign_off_id):
        pending = PendingFullAccess.objects.get(sign_off_id=sign_off_id)
        approval = f"approval-for-{pending.ref}" if self.state == "approved" else None
        return SignOffStatus(id=sign_off_id, state=self.state, expires_at="", approval=approval)


@pytest.fixture
def canopy():
    stand_in = Canopy()
    with patch.object(full_access, "sign_offs", stand_in):
        yield stand_in


def _come_back(client, pending, *, token=None):
    return client.get(
        reverse("labs:mcp_full_access_approved"),
        {"sign_off": token or f"approval-for-{pending.ref}", "id": pending.sign_off_id},
    )


# --- the tokens page -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_full_token_waits_for_the_persons_passkey(client, alice, canopy):
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "laptop", "ttl_days": "30"})

    assert resp.status_code == 302 and resp["Location"] == APPROVE_URL
    assert not MCPAccessToken.objects.filter(user=alice).exists()
    pending = PendingFullAccess.objects.get(user=alice)
    (asked,) = canopy.asked
    assert asked["subject"] == "alice@dimagi.com"
    assert asked["action_key"] == "labs.mcp_token.full"
    assert asked["site_ref"] == pending.ref
    assert asked["return_url"].endswith(reverse("labs:mcp_full_access_approved"))

    back = _come_back(client, pending)
    assert back.status_code == 200
    token = MCPAccessToken.objects.get(user=alice, name="laptop")
    assert token.scope == token_scopes.FULL
    assert MCPAccessToken.verify(back.context["raw_token"]) == token


@pytest.mark.django_db
def test_an_approval_completes_its_mint_once(client, alice, canopy):
    client.post(reverse("labs:mcp_tokens_create"), {"name": "laptop"})
    pending = PendingFullAccess.objects.get(user=alice)
    _come_back(client, pending)

    again = _come_back(client, pending)
    assert again.status_code == 404
    assert MCPAccessToken.objects.filter(user=alice).count() == 1


@pytest.mark.django_db
def test_an_approval_for_another_pending_mint_mints_nothing(client, alice, canopy):
    client.post(reverse("labs:mcp_tokens_create"), {"name": "one"})
    client.post(reverse("labs:mcp_tokens_create"), {"name": "two"})
    first, second = PendingFullAccess.objects.filter(user=alice).order_by("pk")

    resp = _come_back(client, second, token=f"approval-for-{first.ref}")
    assert resp.status_code == 403
    assert not MCPAccessToken.objects.filter(user=alice).exists()


@pytest.mark.django_db
def test_someone_elses_approval_return_finds_nothing(client, alice, canopy):
    client.post(reverse("labs:mcp_tokens_create"), {"name": "laptop"})
    pending = PendingFullAccess.objects.get(user=alice)
    client.force_login(User.objects.create(username="mallory", email="mallory@dimagi.com"))

    assert _come_back(client, pending).status_code == 404
    assert not MCPAccessToken.objects.exists()


@pytest.mark.django_db
def test_declining_mints_nothing(client, alice, canopy):
    client.post(reverse("labs:mcp_tokens_create"), {"name": "laptop"})
    pending = PendingFullAccess.objects.get(user=alice)

    resp = client.get(reverse("labs:mcp_full_access_approved"), {"declined": pending.sign_off_id})
    assert b"declined" in resp.content
    assert not MCPAccessToken.objects.filter(user=alice).exists()
    assert _come_back(client, pending).status_code == 404


@pytest.mark.django_db
def test_someone_canopy_cannot_vouch_for_is_offered_no_user_visit_tokens(client, alice, canopy):
    canopy.refuse = "no_approver"
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "laptop"})

    assert resp.status_code == 403
    assert b"no-user-visit token" in resp.content
    assert not PendingFullAccess.objects.exists()
    assert not MCPAccessToken.objects.exists()


@pytest.mark.django_db
def test_no_user_visit_tokens_stay_self_service(client, alice, canopy):
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "agent", "scope": token_scopes.NO_USERVISIT_DATA})

    assert resp.status_code == 200
    assert MCPAccessToken.objects.get(user=alice).scope == token_scopes.NO_USERVISIT_DATA
    assert canopy.asked == []


@pytest.mark.django_db
def test_an_account_allowed_to_self_mint_needs_no_approval(client, alice, canopy):
    allow_self_mint(alice)
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "ace-laptop"})

    assert resp.status_code == 200
    assert MCPAccessToken.objects.get(user=alice).scope == token_scopes.FULL
    assert canopy.asked == []


@pytest.mark.django_db
def test_rotating_a_full_token_keeps_the_old_one_until_approved(client, alice, canopy):
    old, old_raw = mint_token(alice, name="laptop")
    resp = client.post(reverse("labs:mcp_tokens_rotate", args=[old.pk]))

    assert resp["Location"] == APPROVE_URL
    assert MCPAccessToken.verify(old_raw) == old
    _come_back(client, PendingFullAccess.objects.get(user=alice))
    assert MCPAccessToken.verify(old_raw) is None
    assert MCPAccessToken.objects.filter(user=alice, is_active=True, name="laptop").count() == 1


# --- an agent's request, approved by its owner ----------------------------------------


@pytest.mark.django_db
def test_an_agents_request_is_finished_from_its_own_session(client, alice, canopy):
    canopy.approvers = "agent_admins"
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "agent"})
    assert resp.status_code == 200 and APPROVE_URL.encode() in resp.content
    pending = PendingFullAccess.objects.get(user=alice)
    collect = reverse("labs:mcp_full_access_collect", args=[pending.ref])

    assert b"Not approved yet" in client.post(collect).content
    assert not MCPAccessToken.objects.exists()

    canopy.state = "approved"
    done = client.post(collect)
    assert done.status_code == 200
    assert MCPAccessToken.verify(done.context["raw_token"]).user == alice


# --- the labs-token-setup page --------------------------------------------------------


@pytest.mark.django_db
def test_the_token_setup_page_hands_the_token_over_only_after_approval(client, alice, canopy):
    resp = client.post(reverse("mcp:admin_create_token"), {"callback": CALLBACK, "state": STATE, "name": "cc"})
    assert resp["Location"] == APPROVE_URL
    assert not MCPAccessToken.objects.exists()

    back = _come_back(client, PendingFullAccess.objects.get(user=alice))
    location = urlparse(back["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CALLBACK
    query = parse_qs(location.query)
    assert query["state"] == [STATE]
    assert MCPAccessToken.verify(query["token"][0]).user == alice


# --- an MCP app signing in --------------------------------------------------------------


def _consent(client, scope):
    registered = _register(client).json()
    _verifier, challenge = _pkce()
    params = _authorize_params(registered["client_id"], challenge, scope=scope)
    assert client.get("/o/authorize/", params).status_code == 200
    return client.post("/o/authorize/", {**params, "allow": "Approve", "nonce": "", "claims": ""})


@pytest.mark.django_db(transaction=True)
def test_a_full_mcp_sign_in_waits_for_the_passkey(client, alice, canopy):
    resp = _consent(client, oauth.MCP_SCOPE)
    assert resp["Location"] == APPROVE_URL

    back = _come_back(client, PendingFullAccess.objects.get(user=alice, kind=PendingFullAccess.OAUTH))
    location = urlparse(back["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == LOOPBACK
    query = parse_qs(location.query)
    assert query["state"] == ["st4te"] and query["code"]


@pytest.mark.django_db(transaction=True)
def test_a_restricted_mcp_sign_in_is_not_asked(client, alice, canopy):
    resp = _consent(client, oauth.MCP_NO_USERVISIT_SCOPE)
    assert urlparse(resp["Location"]).netloc == urlparse(LOOPBACK).netloc
    assert canopy.asked == []


# --- nothing mints full access without saying why ---------------------------------------


@pytest.mark.django_db
def test_create_token_refuses_full_access_without_a_reason(alice):
    with pytest.raises(full_access.FullAccessRequired):
        MCPAccessToken.create_token(alice, name="sneaky")
    other = User.objects.create(username="other")
    with pytest.raises(full_access.FullAccessRequired):
        MCPAccessToken.create_token(alice, name="borrowed", full_access=full_access.for_server_command(other))
    with pytest.raises(full_access.FullAccessRequired):
        full_access.FullAccess(alice.pk, "passkey", object())
    assert not MCPAccessToken.objects.exists()


def test_only_the_server_command_and_the_test_helper_skip_the_approval():
    repo = Path(__file__).resolve().parents[3]
    found = [
        str(p.relative_to(repo))
        for p in (repo / "connect_labs").rglob("*.py")
        if "/tests/" not in str(p) and "for_server_command" in p.read_text()
    ]
    assert sorted(found) == [
        "connect_labs/mcp/full_access.py",
        "connect_labs/mcp/management/commands/mcp_create_token.py",
        "connect_labs/mcp/testing.py",
    ]


@pytest.mark.django_db
def test_ace_is_allowed_to_self_mint():
    from importlib import import_module

    from django.apps import apps

    ace = User.objects.create(username="ace", email="ACE@dimagi-ai.com")
    import_module("connect_labs.mcp.migrations.0017_ace_may_self_mint").tick_ace(apps, None)
    assert FullAccessSelfMint.objects.filter(user=ace).exists()
