"""An agent asks a person for a temporary labs token and gets it sealed.

``labs-token-setup --temporary`` hands the token to the computer the person is sitting
at (``admin_views.create_token_browser`` redirects their browser to ``127.0.0.1``). An
agent running somewhere else, such as a cloud runner, has no such address, so it could
not be given one. This is the same grant for that agent, with the same bar: the person's
passkey, through canopy.

1. The agent makes a fresh X25519 keypair, keeps the private half in memory, and asks
   with its OWN labs token (``create``): a reason, the access level and the public key.
2. It gives the person the approval link. The person opens it signed in to labs as
   themselves and reads who asked and what they would get. Approving sends them to
   canopy's passkey dialog (``full_access``); declining needs no passkey.
3. Once canopy's signed approval checks out, labs mints a one-hour token FOR THE PERSON
   and seals it to the agent's public key (``utils.sealed_output``). Labs keeps only that
   ciphertext, so neither a database read nor a log shows a usable token.
4. The agent collects it once (``claim``) and unseals it with its private key.

What this deliberately is not:

* Not a lower bar than the browser flow. Every level needs the passkey here, including
  no-user-visit, which the browser flow lets a person create without one: the token
  leaves labs for another machine.
* Not a way to name the person. The asker is the labs account its own token resolved to
  (verified); whoever opens the link and passes the passkey is who the token is for, and
  the asker cannot be that person.
* Not long-lived. One hour, no renewal, listed on the person's MCP access page where
  they can revoke it (``MCPAccessToken.TEMPORARY_LIFETIME``).
* A request waits ``APPROVAL_WINDOW`` for an answer, and one agent account may have only
  ``MAX_PENDING`` waiting.
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
#: The levels an agent may ask for. Coaching-picture tokens are not for people or agents.
ASKABLE_SCOPES = (token_scopes.NO_USERVISIT_DATA, token_scopes.FULL)


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


def create(
    agent_user, *, reason, recipient_key, scope=token_scopes.NO_USERVISIT_DATA, runner=None
) -> AgentTokenRequest:
    """Record an agent's request. ``agent_user`` is the account its own token resolved to.

    ``runner`` is what canopy vouched for (``runner_check.check``), or empty: not verified."""
    reason = clean_reason(reason)
    scope = scope or token_scopes.NO_USERVISIT_DATA
    if scope not in ASKABLE_SCOPES:
        raise AgentRequestError("bad_scope", f"Ask for one of: {', '.join(ASKABLE_SCOPES)}.")
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
        scope=scope,
        expires_at=now + APPROVAL_WINDOW,
        runner=(runner or {}).get("runner", ""),
        runner_key_kind=(runner or {}).get("kind", ""),
        runner_agent=(runner or {}).get("agent", ""),
        runner_serves_agent=bool((runner or {}).get("serves_agent")),
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


def check_can_ask_passkey(ref: str, person) -> AgentTokenRequest:
    """Before the passkey is asked for: the request is open and the person is not the asker."""
    try:
        req = AgentTokenRequest.objects.select_related("requested_by").get(ref=ref)
    except AgentTokenRequest.DoesNotExist as exc:
        raise AgentRequestError("not_found", "No such request.") from exc
    _must_be_open(req)
    if person.pk == req.requested_by_id:
        raise AgentRequestError("self_approval", "An account cannot approve its own request.")
    return req


def deliver(ref: str, person, full_access) -> AgentTokenRequest:
    """canopy's passkey approval has been checked: mint the person's temporary token and seal it
    to the agent. ``full_access`` is the proof ``full_access._complete`` made from that approval."""
    with transaction.atomic():
        try:
            req = AgentTokenRequest.objects.select_for_update().select_related("requested_by").get(ref=ref)
        except AgentTokenRequest.DoesNotExist as exc:
            raise AgentRequestError("not_found", "No such request.") from exc
        _must_be_open(req)
        if person.pk == req.requested_by_id:
            raise AgentRequestError("self_approval", "An account cannot approve its own request.")
        name = f"temporary-{req.requested_by.username}"[:90] + f"-{timezone.now():%Y%m%d-%H%M}"
        _, raw = MCPAccessToken.create_token(
            person, name=name, scope=req.scope, temporary=True, full_access=full_access
        )
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
