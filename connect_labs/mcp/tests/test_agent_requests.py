"""An agent asking a person for a temporary no-user-visit token, delivered sealed.

The browser-callback flow can only hand a token to the computer the person is at; this
is the same grant for an agent running somewhere else (``mcp/agent_requests.py``).
What is pinned here, because each is a way it could quietly become something else:

* only the no-user-visit level can be asked for, and it is always temporary;
* the token is minted for the PERSON who approves, never for the agent that asked;
* labs keeps only ciphertext sealed to the agent's key, and gives it out once;
* nobody but the account that asked can collect, and it cannot approve itself.
"""

import base64
import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from django.urls import reverse
from django.utils import timezone

from connect_labs.mcp import agent_requests, token_scopes
from connect_labs.mcp.models import AgentTokenRequest, MCPAccessToken
from connect_labs.mcp.testing import mint_token
from connect_labs.users.models import User
from connect_labs.utils import sealed_output


def _keypair():
    private = X25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    return private, public


@pytest.fixture
def agent(db):
    user = User.objects.create(username="agent-ace", email="agent@example.org")
    _, raw = mint_token(user, name="agent")
    return user, raw


@pytest.fixture
def person(db, client):
    user = User.objects.create(username="jane", email="jane@example.org")
    client.force_login(user)
    return user


def _ask(client, raw, public, reason="read how the CHC reports compute coverage"):
    return client.post(
        reverse("mcp:agent_request_create"),
        data=json.dumps({"reason": reason, "recipient_key": public}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )


def _claim(client, raw, ref):
    return client.post(reverse("mcp:agent_request_claim", args=[ref]), HTTP_AUTHORIZATION=f"Bearer {raw}")


@pytest.mark.django_db
def test_an_agent_asks_and_gets_an_approval_link(client, agent):
    _, raw = agent
    _, public = _keypair()
    resp = _ask(client, raw, public)
    assert resp.status_code == 201
    body = resp.json()
    assert body["access"] == token_scopes.NO_USERVISIT_DATA
    assert reverse("labs:mcp_agent_request", args=[body["ref"]]) in body["approve_url"]
    assert AgentTokenRequest.objects.get(ref=body["ref"]).status == AgentTokenRequest.PENDING


@pytest.mark.django_db
def test_asking_needs_the_agents_own_token(client):
    _, public = _keypair()
    assert _ask(client, "not-a-token", public).status_code == 401
    assert (
        client.post(reverse("mcp:agent_request_create"), data="{}", content_type="application/json").status_code == 401
    )


@pytest.mark.django_db
def test_a_no_user_visit_token_may_ask_too(client, db):
    user = User.objects.create(username="restricted-agent")
    _, raw = mint_token(user, scope=token_scopes.NO_USERVISIT_DATA)
    _, public = _keypair()
    assert _ask(client, raw, public).status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize(
    "reason, code",
    [
        ("", "REASON_REQUIRED"),
        ("   ", "REASON_REQUIRED"),
        ("x" * 201, "REASON_TOO_LONG"),
        ("bad\x07text", "REASON_INVALID"),
    ],
)
def test_the_reason_must_be_short_plain_text(client, agent, reason, code):
    _, raw = agent
    _, public = _keypair()
    resp = _ask(client, raw, public, reason=reason)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == code


@pytest.mark.django_db
def test_the_recipient_key_must_be_an_x25519_public_key(client, agent):
    _, raw = agent
    resp = _ask(client, raw, "not-a-key")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_RECIPIENT_KEY"


@pytest.mark.django_db
def test_an_agent_cannot_pile_up_requests(client, agent):
    _, raw = agent
    _, public = _keypair()
    for _ in range(agent_requests.MAX_PENDING):
        assert _ask(client, raw, public).status_code == 201
    resp = _ask(client, raw, public)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "TOO_MANY_PENDING"


@pytest.mark.django_db
def test_the_person_sees_who_asked_and_what_they_would_get(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    resp = client.get(reverse("labs:mcp_agent_request", args=[ref]))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "agent@example.org" in html  # the verified asker
    assert "read how the CHC reports compute coverage" in html
    assert "agent&#x27;s own words" in html or "agent's own words" in html  # the reason is a claim
    assert "No user visit data" in html
    assert "1 hour" in html


@pytest.mark.django_db
def test_the_approval_page_needs_a_login(client, agent):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    resp = client.get(reverse("labs:mcp_agent_request", args=[ref]))
    assert resp.status_code == 302
    assert "login" in resp["Location"]


@pytest.mark.django_db
def test_approving_mints_the_persons_temporary_no_user_visit_token_sealed_to_the_agent(client, agent, person):
    agent_user, raw = agent
    private, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]

    assert _claim(client, raw, ref).json() == {"status": "pending"}

    resp = client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})
    assert resp.status_code == 200
    assert b"Approved" in resp.content

    claimed = _claim(client, raw, ref).json()
    assert claimed["status"] == "approved"
    token_raw = sealed_output.unseal(base64.b64decode(claimed["sealed_token"]), private).decode()

    token = MCPAccessToken.verify(token_raw)
    assert token is not None
    assert token.user_id == person.pk, "the token is the person's, not the agent's"
    assert token.user_id != agent_user.pk
    assert token.scope == token_scopes.NO_USERVISIT_DATA
    assert token.expires_at <= timezone.now() + MCPAccessToken.TEMPORARY_LIFETIME + timedelta(seconds=5)


@pytest.mark.django_db
def test_labs_keeps_only_ciphertext_and_gives_it_out_once(client, agent, person):
    _, raw = agent
    private, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})

    stored = AgentTokenRequest.objects.get(ref=ref).sealed_token
    token_raw = sealed_output.unseal(base64.b64decode(stored), private).decode()
    assert token_raw not in stored, "the stored value is not the token"
    # A different key cannot open it.
    other, _ = _keypair()
    with pytest.raises(Exception):
        sealed_output.unseal(base64.b64decode(stored), other)

    assert _claim(client, raw, ref).json()["status"] == "approved"
    again = _claim(client, raw, ref).json()
    assert again == {"status": "claimed"}
    assert AgentTokenRequest.objects.get(ref=ref).sealed_token == ""


@pytest.mark.django_db
def test_only_the_account_that_asked_can_collect(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})

    other = User.objects.create(username="other-agent")
    _, other_raw = mint_token(other)
    resp = _claim(client, other_raw, ref)
    assert resp.status_code == 404
    assert AgentTokenRequest.objects.get(ref=ref).status == AgentTokenRequest.APPROVED


@pytest.mark.django_db
def test_an_account_cannot_approve_its_own_request(client, agent):
    agent_user, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.force_login(agent_user)
    resp = client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})
    assert resp.status_code == 403
    req = AgentTokenRequest.objects.get(ref=ref)
    assert req.status == AgentTokenRequest.PENDING
    assert req.sealed_token == ""
    assert not MCPAccessToken.objects.filter(user=agent_user, scope=token_scopes.NO_USERVISIT_DATA).exists()


@pytest.mark.django_db
def test_declining_creates_nothing(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    before = MCPAccessToken.objects.count()
    resp = client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "decline"})
    assert resp.status_code == 200
    assert MCPAccessToken.objects.count() == before
    assert _claim(client, raw, ref).json() == {"status": "declined"}


@pytest.mark.django_db
def test_an_old_request_cannot_be_approved(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    AgentTokenRequest.objects.filter(ref=ref).update(expires_at=timezone.now() - timedelta(minutes=1))

    assert _claim(client, raw, ref).json() == {"status": "expired"}
    before = MCPAccessToken.objects.count()
    resp = client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})
    assert resp.status_code == 409
    assert MCPAccessToken.objects.count() == before


@pytest.mark.django_db
def test_an_answered_request_cannot_be_answered_twice(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})
    count = MCPAccessToken.objects.filter(user=person).count()
    resp = client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})
    assert resp.status_code == 409
    assert MCPAccessToken.objects.filter(user=person).count() == count


@pytest.mark.django_db
def test_an_unknown_request_is_not_found(client, person):
    assert client.get(reverse("labs:mcp_agent_request", args=["nope"])).status_code == 404


@pytest.mark.django_db
def test_full_access_cannot_be_asked_for_this_way(client, agent, person):
    """The level is not a parameter: whatever the body says, the token is no-user-visit."""
    _, raw = agent
    private, public = _keypair()
    resp = client.post(
        reverse("mcp:agent_request_create"),
        data=json.dumps({"reason": "everything", "recipient_key": public, "scope": "full", "temporary": False}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    ref = resp.json()["ref"]
    client.post(reverse("labs:mcp_agent_request", args=[ref]), {"action": "approve"})
    token_raw = sealed_output.unseal(
        base64.b64decode(_claim(client, raw, ref).json()["sealed_token"]), private
    ).decode()
    token = MCPAccessToken.verify(token_raw)
    assert token.scope == token_scopes.NO_USERVISIT_DATA
