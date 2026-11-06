"""An agent asks a person for a one-hour, no-user-visit labs token, and gets it sealed.

``labs-token-setup --temporary`` hands the token to the computer the person is sitting
at (``admin_views.create_token_browser`` redirects their browser to ``127.0.0.1``). An
agent running somewhere else, such as a cloud runner, has no such address, so it could
not be given one. This is the same grant for that agent:

1. The agent makes a fresh X25519 keypair, keeps the private half in memory, and asks
   with its OWN labs token (``create``): a reason and the public key.
2. It gives the person the approval link. The person opens it signed in to labs as
   themselves, reads who asked and what they get, and approves or declines (``approve``).
3. Approving mints a temporary ``no-uservisit-data`` token FOR THE PERSON and seals it to
   the agent's public key (``utils.sealed_output``). Labs keeps only that ciphertext, so
   neither a database read nor a log shows a usable token.
4. The agent collects it once (``claim``) and unseals it with its private key.

Why this is safe to offer without a passkey, and what it deliberately is not:

* The level is fixed. Only ``no-uservisit-data`` can be asked for this way: it never
  sees a real visit row (``token_scopes``). Full access keeps its passkey approval
  (``full_access``) and cannot be requested here.
* Who asked is verified, not claimed: it is the labs account the request was signed
  with. The reason is the agent's own words and the approval page says so.
* One hour, no renewal, and listed on the person's MCP access page where they can
  revoke it (``MCPAccessToken.TEMPORARY_LIFETIME``).
* A request waits ``APPROVAL_WINDOW`` for an answer, one agent account may have only
  ``MAX_PENDING`` waiting, and the agent cannot approve its own.
"""

from __future__ import annotations

import base64
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from connect_labs.utils import sealed_output

from . import token_scopes
from .models import AgentTokenRequest, MCPAccessToken

log = logging.getLogger(__name__)

#: How long the person has to answer.
APPROVAL_WINDOW = timedelta(minutes=30)
#: How many unanswered requests one agent account may have open.
MAX_PENDING = 3
REASON_MAX = 200


class AgentRequestError(Exception):
    """A request that cannot proceed; ``code`` is stable for callers, ``message`` is for people."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def clean_reason(reason) -> str:
    """The reason as plain text of one line, or an error: the person reads it before approving."""
    text = " ".join(str(reason or "").split())
    if not text:
        raise AgentRequestError("reason_required", "Say what you will read and why (a short sentence).")
    if len(text) > REASON_MAX:
        raise AgentRequestError("reason_too_long", f"Keep the reason under {REASON_MAX} characters.")
    if any(ord(ch) < 32 for ch in text):
        raise AgentRequestError("reason_invalid", "The reason must be plain text.")
    return text


def create(agent_user, *, reason, recipient_key) -> AgentTokenRequest:
    """Record an agent's request. ``agent_user`` is the account its own token resolved to."""
    reason = clean_reason(reason)
    try:
        sealed_output.parse_recipient(recipient_key)
    except sealed_output.SealedOutputError as exc:
        raise AgentRequestError("bad_recipient_key", str(exc)) from exc
    now = timezone.now()
    open_requests = AgentTokenRequest.objects.filter(
        requested_by=agent_user, status=AgentTokenRequest.PENDING, expires_at__gt=now
    ).count()
    if open_requests >= MAX_PENDING:
        raise AgentRequestError(
            "too_many_pending",
            f"{open_requests} requests from this account are still waiting for an answer.",
        )
    return AgentTokenRequest.objects.create(
        requested_by=agent_user,
        reason=reason,
        recipient_key=recipient_key,
        expires_at=now + APPROVAL_WINDOW,
    )


def _audit(user, outcome: str, req: AgentTokenRequest, detail: str = "") -> None:
    from connect_labs.audit_trail.models import Action, Outcome
    from connect_labs.audit_trail.service import record

    record(
        Action.CREATE if outcome == "approved" else Action.ACCESS_DENIED if outcome == "declined" else Action.UPDATE,
        resource_type="mcp_agent_token_request",
        outcome=Outcome.SUCCESS,
        metadata={
            "ref": req.ref,
            "outcome": outcome,
            "requested_by": req.requested_by_id,
            "detail": detail[:200],
        },
        user=user,
    )


def approve(ref: str, person) -> AgentTokenRequest:
    """The person approves: mint their temporary no-user-visit token and seal it to the agent."""
    with transaction.atomic():
        try:
            req = AgentTokenRequest.objects.select_for_update().select_related("requested_by").get(ref=ref)
        except AgentTokenRequest.DoesNotExist as exc:
            raise AgentRequestError("not_found", "No such request.") from exc
        _must_be_open(req)
        if person.pk == req.requested_by_id:
            raise AgentRequestError("self_approval", "An account cannot approve its own request.")
        name = f"temporary-{req.requested_by.username}"[:90] + f"-{timezone.now():%Y%m%d-%H%M}"
        _, raw = MCPAccessToken.create_token(person, name=name, scope=token_scopes.NO_USERVISIT_DATA, temporary=True)
        sealed = sealed_output.seal(raw.encode("utf-8"), req.recipient_key)
        req.sealed_token = base64.b64encode(sealed).decode("ascii")
        req.status = AgentTokenRequest.APPROVED
        req.approved_by = person
        req.decided_at = timezone.now()
        req.save(update_fields=["sealed_token", "status", "approved_by", "decided_at"])
    _audit(person, "approved", req)
    return req


def decline(ref: str, person) -> AgentTokenRequest:
    with transaction.atomic():
        try:
            req = AgentTokenRequest.objects.select_for_update().get(ref=ref)
        except AgentTokenRequest.DoesNotExist as exc:
            raise AgentRequestError("not_found", "No such request.") from exc
        _must_be_open(req)
        req.status = AgentTokenRequest.DECLINED
        req.approved_by = person
        req.decided_at = timezone.now()
        req.save(update_fields=["status", "approved_by", "decided_at"])
    _audit(person, "declined", req)
    return req


def _must_be_open(req: AgentTokenRequest) -> None:
    if req.status != AgentTokenRequest.PENDING:
        raise AgentRequestError("already_answered", "This request has already been answered.")
    if req.expired:
        raise AgentRequestError("expired", "This request expired before it was answered. Ask the agent to ask again.")


def claim(ref: str, agent_user) -> dict:
    """What the agent sees when it asks how its request went.

    ``approved`` carries the sealed token exactly once; a second ask answers ``claimed``
    with nothing in it. Only the account that asked can ask, and anyone else is told the
    request does not exist.
    """
    with transaction.atomic():
        try:
            req = AgentTokenRequest.objects.select_for_update().get(ref=ref, requested_by=agent_user)
        except AgentTokenRequest.DoesNotExist as exc:
            raise AgentRequestError("not_found", "No such request.") from exc
        if req.status == AgentTokenRequest.PENDING:
            return {"status": "expired" if req.expired else "pending"}
        if req.status == AgentTokenRequest.APPROVED:
            sealed = req.sealed_token
            req.sealed_token = ""
            req.status = AgentTokenRequest.CLAIMED
            req.claimed_at = timezone.now()
            req.save(update_fields=["sealed_token", "status", "claimed_at"])
            return {"status": "approved", "sealed_token": sealed}
        return {"status": req.status}
