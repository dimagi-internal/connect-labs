"""An agent asking a person for a temporary labs token, delivered sealed, behind their passkey.

The browser-callback flow can only hand a token to the computer the person is at; this is
the same grant for an agent running somewhere else (``mcp/agent_requests.py``), with the
same bar and a little more: the person's passkey, through canopy, at EVERY level.
What is pinned here, because each is a way it could quietly become something else:

* nothing is minted until canopy's signed approval for THIS request checks out;
* the token is minted for the PERSON who approves, never for the agent that asked;
* labs keeps only ciphertext sealed to the agent's key, and gives it out once;
* nobody but the account that asked can collect, and it cannot approve itself.

canopy is stood in for at the SDK's boundary, as in ``test_full_access``.
"""

import base64
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from canopy_sdk.sign_offs import NO_APPROVER, Approval, SignOffError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from django.urls import reverse
from django.utils import timezone

from connect_labs.mcp import agent_requests, full_access, oauth, token_scopes
from connect_labs.mcp.models import AgentTokenRequest, MCPAccessToken, PendingFullAccess
from connect_labs.mcp.testing import mint_token
from connect_labs.mcp.tests.test_full_access import Canopy
from connect_labs.users.models import User
from connect_labs.utils import sealed_output


class AgentCanopy(Canopy):
    """canopy, for the agent action: its approval names the agent action key."""

    def verify(self, token, *, action_key, site_ref, subject=None):
        if token != f"approval-for-{site_ref}" or action_key != full_access.AGENT_ACTION_KEY:
            raise SignOffError("wrong_site_ref", "the sign-off is for a different pending action")
        return Approval(
            id="so_1",
            action_key=action_key,
            site_ref=site_ref,
            subject=subject or "",
            approver_email="person@example.org",
            approver_name="Person",
            approved_at="",
            step_up="passkey",
            jti=f"jti-{site_ref}",
            expires_at=0,
        )


@pytest.fixture
def canopy():
    stand_in = AgentCanopy()
    with patch.object(full_access, "sign_offs", stand_in):
        yield stand_in


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


def _ask(client, raw, public, reason="read how the CHC reports compute coverage", **extra):
    return client.post(
        reverse("mcp:agent_request_create"),
        data=json.dumps({"reason": reason, "recipient_key": public, **extra}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )


def _claim(client, raw, ref):
    return client.post(reverse("mcp:agent_request_claim", args=[ref]), HTTP_AUTHORIZATION=f"Bearer {raw}")


def _page(ref):
    return reverse("labs:mcp_agent_request", args=[ref])


def _passkey_given(client, canopy, ref):
    """The person approved in canopy's dialog: the page reports back, and labs reads canopy's state."""
    pending = PendingFullAccess.objects.get(kind=PendingFullAccess.AGENT_REQUEST, payload__request_ref=ref)
    canopy.state = "approved"
    return client.post(
        reverse("labs:mcp_full_access_collect", args=[pending.ref]),
        {"sign_off": pending.sign_off_id, "state": "approved"},
    )


def _token_for(private, claimed):
    return sealed_output.unseal(base64.b64decode(claimed["sealed_token"]), private).decode()


# --- asking --------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_agent_asks_and_gets_an_approval_link(client, agent):
    _, raw = agent
    _, public = _keypair()
    resp = _ask(client, raw, public)
    assert resp.status_code == 201
    body = resp.json()
    assert body["access"] == token_scopes.NO_USERVISIT_DATA, "the default is the restricted level"
    assert _page(body["ref"]) in body["approve_url"]
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
def test_only_the_two_real_levels_can_be_asked_for(client, agent):
    _, raw = agent
    _, public = _keypair()
    assert _ask(client, raw, public, scope=token_scopes.FULL).status_code == 201
    resp = _ask(client, raw, public, scope=token_scopes.COACH_IMAGES)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_SCOPE"


@pytest.mark.django_db
def test_an_agent_cannot_pile_up_requests(client, agent):
    _, raw = agent
    _, public = _keypair()
    for _ in range(agent_requests.MAX_PENDING):
        assert _ask(client, raw, public).status_code == 201
    resp = _ask(client, raw, public)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "TOO_MANY_PENDING"


# --- the person's page ---------------------------------------------------------------


@pytest.mark.django_db
def test_the_person_sees_who_asked_what_they_would_get_and_that_a_passkey_is_next(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    resp = client.get(_page(ref))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "agent@example.org" in html  # the verified asker
    assert "read how the CHC reports compute coverage" in html
    assert "can&#x27;t check them" in html or "can't check them" in html  # the reason is a claim
    assert "No user visit data" in html
    assert "1 hour" in html
    assert "passkey" in html


@pytest.mark.django_db
def test_a_full_access_request_is_shown_as_full_access(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public, scope=token_scopes.FULL).json()["ref"]
    assert b"Full access" in client.get(_page(ref)).content


@pytest.mark.django_db
def test_the_approval_page_needs_a_login(client, agent):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    resp = client.get(_page(ref))
    assert resp.status_code == 302
    assert "login" in resp["Location"]


@pytest.mark.django_db
def test_an_unknown_request_is_not_found(client, person):
    assert client.get(_page("nope")).status_code == 404


# --- the passkey -----------------------------------------------------------------------


@pytest.mark.django_db
def test_approving_asks_for_the_passkey_and_mints_nothing_until_it_is_given(client, agent, person, canopy):
    agent_user, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    before = MCPAccessToken.objects.count()

    resp = client.post(_page(ref), {"action": "approve"})

    assert resp.status_code == 200
    (asked,) = canopy.asked
    assert asked["action_key"] == full_access.AGENT_ACTION_KEY == "labs.mcp_token.agent"
    assert asked["subject"] == "jane@example.org", "the person, not the agent"
    assert "agent@example.org" in asked["title"]
    pending = PendingFullAccess.objects.get(kind=PendingFullAccess.AGENT_REQUEST)
    assert asked["site_ref"] == pending.ref
    assert resp.context["full_access_notice"]["sign_off"] == pending.sign_off_id
    assert b"data-canopy-sign-off-open" in resp.content
    assert MCPAccessToken.objects.count() == before
    assert _claim(client, raw, ref).json() == {"status": "pending"}


@pytest.mark.django_db
def test_the_passkey_mints_the_persons_token_sealed_to_the_agent(client, agent, person, canopy):
    agent_user, raw = agent
    private, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(_page(ref), {"action": "approve"})

    done = _passkey_given(client, canopy, ref)
    assert done.status_code == 200
    assert b"Approved with your passkey" in done.content

    claimed = _claim(client, raw, ref).json()
    assert claimed["status"] == "approved"
    token = MCPAccessToken.verify(_token_for(private, claimed))
    assert token is not None
    assert token.user_id == person.pk, "the token is the person's, not the agent's"
    assert token.user_id != agent_user.pk
    assert token.scope == token_scopes.NO_USERVISIT_DATA
    assert token.expires_at <= timezone.now() + MCPAccessToken.TEMPORARY_LIFETIME + timedelta(seconds=5)


@pytest.mark.django_db
def test_a_full_access_request_mints_a_full_token_only_behind_the_passkey(client, agent, person, canopy):
    _, raw = agent
    private, public = _keypair()
    ref = _ask(client, raw, public, scope=token_scopes.FULL).json()["ref"]
    client.post(_page(ref), {"action": "approve"})
    assert not MCPAccessToken.objects.filter(user=person, scope=token_scopes.FULL).exists()

    _passkey_given(client, canopy, ref)
    token = MCPAccessToken.verify(_token_for(private, _claim(client, raw, ref).json()))
    assert token.user_id == person.pk
    assert token.scope == token_scopes.FULL
    assert token.expires_at <= timezone.now() + MCPAccessToken.TEMPORARY_LIFETIME + timedelta(seconds=5)


@pytest.mark.django_db
def test_the_page_saying_approved_is_not_believed(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(_page(ref), {"action": "approve"})
    pending = PendingFullAccess.objects.get(kind=PendingFullAccess.AGENT_REQUEST)

    # The browser claims it was approved; canopy's own state still says open.
    resp = client.post(
        reverse("labs:mcp_full_access_collect", args=[pending.ref]),
        {"sign_off": pending.sign_off_id, "state": "approved"},
    )
    assert b"Not approved yet" in resp.content
    assert _claim(client, raw, ref).json() == {"status": "pending"}


@pytest.mark.django_db
def test_a_full_access_approval_cannot_complete_an_agent_request(client, agent, person):
    """canopy's approval names the action: the full-access one does not open the agent one."""
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    with patch.object(full_access, "sign_offs", Canopy()) as canopy:
        client.post(_page(ref), {"action": "approve"})
        _passkey_given(client, canopy, ref)
    assert _claim(client, raw, ref).json() == {"status": "pending"}
    assert not MCPAccessToken.objects.filter(user=person, scope=token_scopes.NO_USERVISIT_DATA).exists()


@pytest.mark.django_db
def test_someone_canopy_cannot_vouch_for_gets_nothing(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    canopy.refuse = NO_APPROVER
    resp = client.post(_page(ref), {"action": "approve"})
    assert resp.status_code == 403
    assert b"canopy does not know this account" in resp.content
    assert _claim(client, raw, ref).json() == {"status": "pending"}


@pytest.mark.django_db
def test_declining_creates_nothing_and_asks_for_no_passkey(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    before = MCPAccessToken.objects.count()
    resp = client.post(_page(ref), {"action": "decline"})
    assert resp.status_code == 200
    assert canopy.asked == []
    assert MCPAccessToken.objects.count() == before
    assert _claim(client, raw, ref).json() == {"status": "declined"}


# --- the seal and the claim ------------------------------------------------------------


@pytest.mark.django_db
def test_labs_keeps_only_ciphertext_and_gives_it_out_once(client, agent, person, canopy):
    _, raw = agent
    private, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(_page(ref), {"action": "approve"})
    _passkey_given(client, canopy, ref)

    stored = AgentTokenRequest.objects.get(ref=ref).sealed_token
    token_raw = sealed_output.unseal(base64.b64decode(stored), private).decode()
    assert token_raw not in stored, "the stored value is not the token"
    other, _ = _keypair()
    with pytest.raises(Exception):
        sealed_output.unseal(base64.b64decode(stored), other)

    assert _claim(client, raw, ref).json()["status"] == "approved"
    assert _claim(client, raw, ref).json() == {"status": "claimed"}
    assert AgentTokenRequest.objects.get(ref=ref).sealed_token == ""


@pytest.mark.django_db
def test_only_the_account_that_asked_can_collect(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(_page(ref), {"action": "approve"})
    _passkey_given(client, canopy, ref)

    other = User.objects.create(username="other-agent")
    _, other_raw = mint_token(other)
    assert _claim(client, other_raw, ref).status_code == 404
    assert AgentTokenRequest.objects.get(ref=ref).status == AgentTokenRequest.APPROVED


@pytest.mark.django_db
def test_an_account_cannot_approve_its_own_request(client, agent, canopy):
    agent_user, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.force_login(agent_user)
    resp = client.post(_page(ref), {"action": "approve"})
    assert resp.status_code == 403
    assert canopy.asked == [], "its passkey is not even asked for"
    assert AgentTokenRequest.objects.get(ref=ref).sealed_token == ""


@pytest.mark.django_db
def test_an_old_request_cannot_be_approved(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    AgentTokenRequest.objects.filter(ref=ref).update(expires_at=timezone.now() - timedelta(minutes=1))

    assert _claim(client, raw, ref).json() == {"status": "expired"}
    resp = client.post(_page(ref), {"action": "approve"})
    assert resp.status_code == 409
    assert canopy.asked == []


@pytest.mark.django_db
def test_an_answered_request_cannot_be_answered_twice(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    client.post(_page(ref), {"action": "approve"})
    _passkey_given(client, canopy, ref)
    count = MCPAccessToken.objects.filter(user=person).count()

    resp = client.post(_page(ref), {"action": "approve"})
    assert resp.status_code == 409
    assert MCPAccessToken.objects.filter(user=person).count() == count


# --- which runner asked ---------------------------------------------------------------


class Runner:
    """canopy's answer about a runner proof (``canopy_sdk.django.runner_attestations.attest``)."""

    def __init__(self, vouch=None, refuse=None, down=False):
        self.vouch = vouch
        self.refuse = refuse
        self.down = down
        self.asked = []

    def __call__(self, **kw):
        from canopy_sdk.runner_attestations import Attestation, AttestationError

        self.asked.append(kw)
        if self.down:
            raise AttestationError("unreachable", "canopy could not be reached")
        if self.refuse:
            raise AttestationError("proof_refused", "no approved runner key proved this request", status=401)
        return Attestation(**(self.vouch or {}))


CLOUD_RUNNER = {"runner": "cloud-ec2-2", "kind": "aws-iam", "jkt": "j", "agent": "ace", "serves_agent": True}


def _ask_from_runner(client, raw, public, canopy_says, proof="aws-iam abc", **extra):
    body = json.dumps({"reason": "read how the CHC reports compute coverage", "recipient_key": public, **extra})
    with patch("canopy_sdk.django.runner_attestations.attest", canopy_says):
        return (
            client.post(
                reverse("mcp:agent_request_create"),
                data=body,
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {raw}",
                **({"HTTP_X_CANOPY_RUNNER_PROOF": proof} if proof else {}),
            ),
            body,
        )


@pytest.mark.django_db
def test_a_request_without_a_runner_proof_is_not_verified_but_goes_through(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    resp, _ = _ask_from_runner(client, raw, public, Runner(), proof="")
    assert resp.status_code == 201 and resp.json()["runner"] is None
    html = client.get(_page(resp.json()["ref"])).content.decode()
    assert "Not verified" in html


@pytest.mark.django_db
def test_a_proof_canopy_vouches_for_names_the_runner_for_the_person(client, agent, person):
    from canopy_sdk.runner_attestations import bound_url

    _, raw = agent
    _, public = _keypair()
    says = Runner(vouch=CLOUD_RUNNER)
    resp, body = _ask_from_runner(client, raw, public, says, agent="ace")
    assert resp.status_code == 201 and resp.json()["runner"] == "cloud-ec2-2"
    (asked,) = says.asked
    assert asked["kind"] == "aws-iam" and asked["proof"] == "abc" and asked["speaks_for"] == "ace"
    # The proof must be for THIS endpoint and THESE bytes: the digest is in the URL canopy checks.
    base = oauth.public_base_url() or "http://testserver"
    assert asked["htu"] == bound_url(base + reverse("mcp:agent_request_create"), body.encode())
    req = AgentTokenRequest.objects.get(ref=resp.json()["ref"])
    assert (req.runner, req.runner_key_kind, req.runner_serves_agent) == ("cloud-ec2-2", "aws-iam", True)
    html = client.get(_page(req.ref)).content.decode()
    assert "cloud-ec2-2" in html and "Not verified" not in html


@pytest.mark.django_db
def test_a_runner_that_does_not_serve_the_agent_it_names_is_flagged(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    says = Runner(vouch={**CLOUD_RUNNER, "serves_agent": False})
    resp, _ = _ask_from_runner(client, raw, public, says, agent="ace")
    html = client.get(_page(resp.json()["ref"])).content.decode()
    assert "does not serve it" in html


@pytest.mark.django_db
def test_a_proof_canopy_refuses_refuses_the_request(client, agent):
    _, raw = agent
    _, public = _keypair()
    resp, _ = _ask_from_runner(client, raw, public, Runner(refuse=True))
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "RUNNER_PROOF_REFUSED"
    assert not AgentTokenRequest.objects.exists(), "a bad proof must not read as 'not verified'"


@pytest.mark.django_db
def test_a_malformed_proof_header_is_a_bad_request(client, agent):
    _, raw = agent
    _, public = _keypair()
    resp, _ = _ask_from_runner(client, raw, public, Runner(), proof="rsa nonsense")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "BAD_RUNNER_PROOF"


@pytest.mark.django_db
def test_when_canopy_cannot_be_asked_the_request_is_not_verified(client, agent, person):
    _, raw = agent
    _, public = _keypair()
    resp, _ = _ask_from_runner(client, raw, public, Runner(down=True))
    assert resp.status_code == 201 and resp.json()["runner"] is None


@pytest.mark.django_db
def test_the_passkey_card_says_where_the_request_came_from(client, agent, person, canopy):
    _, raw = agent
    _, public = _keypair()
    ref = _ask_from_runner(client, raw, public, Runner(vouch=CLOUD_RUNNER))[0].json()["ref"]
    client.post(_page(ref), {"action": "approve"})
    assert "cloud-ec2-2" in canopy.asked[-1]["detail"]


@pytest.mark.django_db
def test_the_request_is_answered_on_the_main_tokens_page(client, agent, person):
    """One page for tokens: the agent's link lands on the tokens page, request on top, the person's own
    tokens and the create form beneath it."""
    _, raw = agent
    _, public = _keypair()
    ref = _ask(client, raw, public).json()["ref"]
    html = client.get(_page(ref)).content.decode()
    assert 'data-testid="agent-request-card"' in html
    assert "Your active tokens" in html and "Create a new token" in html
    assert html.index("agent-request-card") < html.index("Create a new token")
    # Without a request in the link, the page is the plain tokens page.
    plain = client.get(reverse("labs:mcp_tokens_index")).content.decode()
    assert "agent-request-card" not in plain
