"""Full-access MCP credentials need a person's passkey, through canopy.

A full-access token (or a full ``mcp`` OAuth sign-in) can do anything its person can
in labs, visit data included. Anyone driving a signed-in browser (an agent with the
person's labs session) could otherwise click "create" and hand itself that. So every
place that mints one asks canopy for the person's passkey approval first (canopy-web
spec ``2026-10-27-site-sign-offs-design``, ``canopy_sdk.django.sign_offs``) and mints
only once canopy's signed approval has been checked here.

* **Who approves** is canopy's answer: the person themselves when canopy knows them as
  a member of labs' workspace, or an agent's owner or admins. Anyone canopy cannot
  vouch for gets ``no_approver``, and labs then offers them no-user-visit tokens only
  (Jonathan, 2026-10-27).
* **Self-mint**: an account Dimagi staff ticked in the admin (``FullAccessSelfMint``)
  mints with no approval. It is on for ACE, which is never in a project space with real
  data, so approving each of its tokens would guard nothing.
* **Untouched**: existing tokens, using any token, and no-user-visit and coaching tokens.

``MCPAccessToken.create_token`` refuses a FULL token without a ``FullAccess`` (``require``),
and only this module makes one, so a new mint path cannot skip the approval by
forgetting it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from canopy_sdk.django import sign_offs
from canopy_sdk.sign_offs import AGENT_ADMINS, NO_APPROVER, SignOffError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import FullAccessSelfMint, PendingFullAccess

log = logging.getLogger(__name__)

#: The action canopy's approval names: one key for every full-access credential.
ACTION_KEY = "labs.mcp_token.full"
#: How long a pending mint waits. canopy closes a person's own request after 15 minutes
#: and an agent's after a day; this outlives both.
PENDING_LIFETIME = timedelta(hours=25)

TITLES = {
    PendingFullAccess.TOKEN_CREATE: "Create a full-access labs MCP token",
    PendingFullAccess.TOKEN_ROTATE: "Rotate a full-access labs MCP token",
    PendingFullAccess.TOKEN_BROWSER: "Create a full-access labs MCP token for an app on your computer",
    PendingFullAccess.OAUTH: "Sign an MCP app in to labs with full access",
}


class FullAccessRequired(PermissionError):
    """A full-access credential was about to be minted with nothing saying it may be."""


_MINT = object()


@dataclass(frozen=True)
class FullAccess:
    """Why this full-access credential may be minted, for this user. Made only here."""

    user_id: int
    #: ``passkey`` (canopy's approval), ``self_mint`` or ``server`` (a command on the server).
    via: str
    _key: object

    def __post_init__(self):
        if self._key is not _MINT:
            raise FullAccessRequired("a FullAccess is made by connect_labs.mcp.full_access only")


def require(full_access, user) -> None:
    """``MCPAccessToken.create_token``'s check for a FULL token."""
    if not isinstance(full_access, FullAccess) or full_access.user_id != getattr(user, "pk", None):
        raise FullAccessRequired(
            "a full-access MCP credential needs its person's passkey approval through canopy "
            "(connect_labs.mcp.full_access), or an account allowed to self-mint"
        )


def may_self_mint(user) -> bool:
    return bool(user and user.is_authenticated and FullAccessSelfMint.objects.filter(user=user).exists())


def self_minted(user) -> FullAccess | None:
    """A ``FullAccess`` when ``user`` may mint full access with no approval, else None."""
    return FullAccess(user.pk, "self_mint", _MINT) if may_self_mint(user) else None


def for_server_command(user) -> FullAccess:
    """``mcp_create_token``: someone with a shell on the server already holds more than any
    token gives, so a passkey would guard nothing."""
    return FullAccess(user.pk, "server", _MINT)


def _audit(user, outcome: str, kind: str, detail: str = "") -> None:
    from connect_labs.audit_trail.models import Action, Outcome
    from connect_labs.audit_trail.service import record

    record(
        Action.CREATE if outcome == "approved" else Action.ACCESS_DENIED,
        resource_type="mcp_full_access",
        outcome=Outcome.SUCCESS if outcome in ("approved", "asked") else Outcome.FAILURE,
        metadata={"kind": kind, "outcome": outcome, "detail": detail[:200]},
        user=user,
    )


def _return_url() -> str:
    from .oauth import public_base_url

    return f"{public_base_url()}{reverse('labs:mcp_full_access_approved')}"


def refused(request, reason: str, *, status: int = 403):
    return render(request, "mcp/full_access.html", {"refused": reason}, status=status)


def begin(request, kind: str, payload: dict, *, detail: str = ""):
    """Ask canopy for the person's approval of one full-access mint and send them to it.

    Their own request: the browser goes straight to canopy's approval page, and comes
    back to ``approved`` with the signed approval. An agent's (approved by its owner or
    admins): the page says who was asked and offers to finish once they have."""
    user = request.user
    pending = PendingFullAccess.objects.create(
        user=user, kind=kind, payload=payload, expires_at=timezone.now() + PENDING_LIFETIME
    )
    try:
        asked = sign_offs.request(
            subject=user.email or "",
            action_key=ACTION_KEY,
            title=TITLES[kind],
            detail=detail,
            site_ref=pending.ref,
            return_url=_return_url(),
        )
    except SignOffError as exc:
        pending.delete()
        _audit(user, "refused", kind, exc.code)
        if exc.code == NO_APPROVER:
            return refused(
                request,
                "Full access needs your passkey approval in canopy, and canopy does not "
                "know this account. Use a no-user-visit token instead.",
            )
        log.warning("full access sign-off for %s: %s %s", user.pk, exc.code, exc.message)
        return refused(
            request,
            "Full access needs your passkey approval in canopy, and canopy could not be "
            "asked just now. Try again in a minute.",
            status=503,
        )
    pending.sign_off_id = asked.id
    pending.save(update_fields=["sign_off_id"])
    _audit(user, "asked", kind, asked.approvers)
    if asked.approvers == AGENT_ADMINS:
        return render(request, "mcp/full_access.html", {"pending": pending, "approve_url": asked.approve_url})
    return redirect(asked.approve_url)


def _open_pending(user, *, ref: str = "", sign_off_id: str = "") -> PendingFullAccess | None:
    rows = PendingFullAccess.objects.filter(user=user, completed_at__isnull=True, expires_at__gt=timezone.now())
    if ref:
        return rows.filter(ref=ref).first()
    return rows.filter(sign_off_id=sign_off_id).first() if sign_off_id else None


def _complete(request, pending: PendingFullAccess, approval_token: str | None):
    """Check canopy's approval for ``pending`` and, once, do what it was waiting for."""
    if not approval_token:
        return refused(request, "There is no approval for this yet.")
    try:
        approval = sign_offs.verify(
            approval_token, action_key=ACTION_KEY, site_ref=pending.ref, subject=request.user.email or ""
        )
    except SignOffError as exc:
        _audit(request.user, "refused", pending.kind, exc.code)
        return refused(request, "canopy's approval could not be checked, so nothing was created.")
    try:
        with transaction.atomic():
            locked = PendingFullAccess.objects.select_for_update().get(pk=pending.pk)
            if locked.completed_at is not None:
                return refused(request, "This was already done.")
            locked.completed_at = timezone.now()
            locked.approval_jti = approval.jti
            locked.save(update_fields=["completed_at", "approval_jti"])
    except IntegrityError:
        return refused(request, "This approval was already used.")
    _audit(request.user, "approved", pending.kind, approval.approver_email)
    return COMPLETERS[locked.kind](request, locked.payload, FullAccess(request.user.pk, "passkey", _MINT))


def approved(request):
    """``/labs/mcp/full-access/approved/``: where canopy sends the browser after the person
    decided (``?sign_off=<jws>&id=<id>`` or ``?declined=<id>``)."""
    declined = request.GET.get("declined") or ""
    pending = _open_pending(request.user, sign_off_id=declined or request.GET.get("id") or "")
    if pending is None:
        return refused(
            request,
            "Nothing is waiting on this approval for your account. If an agent asked, "
            "it finishes from its own labs session.",
            status=404,
        )
    if declined:
        pending.completed_at = timezone.now()
        pending.save(update_fields=["completed_at"])
        _audit(request.user, "declined", pending.kind)
        return refused(request, "You declined, so nothing was created.")
    return _complete(request, pending, request.GET.get("sign_off"))


def collect(request, ref: str):
    """POST ``/labs/mcp/full-access/<ref>/collect/``: an agent's request, approved by its
    owner in canopy, finished from the agent's own session."""
    pending = _open_pending(request.user, ref=ref)
    if pending is None:
        return refused(request, "Nothing is waiting under this link.", status=404)
    try:
        state = sign_offs.status(pending.sign_off_id)
    except SignOffError as exc:
        log.warning("full access status for %s: %s %s", request.user.pk, exc.code, exc.message)
        return refused(request, "canopy could not be asked just now. Try again in a minute.", status=503)
    if state.state == "open":
        return render(request, "mcp/full_access.html", {"pending": pending, "not_yet": True})
    if state.state != "approved":
        pending.completed_at = timezone.now()
        pending.save(update_fields=["completed_at"])
        return refused(request, f"The request was {state.state}, so nothing was created.")
    return _complete(request, pending, state.approval)


# --- what each kind of pending mint does once approved ---------------------------------


def _complete_token_create(request, payload: dict, full: FullAccess):
    from .models import MCPAccessToken
    from .token_scopes import FULL
    from .token_views import _render_index

    name = payload["name"]
    _, raw = MCPAccessToken.create_token(
        request.user, name=name, ttl_days=payload.get("ttl_days"), scope=FULL, full_access=full
    )
    return _render_index(request, raw_token=raw, raw_token_name=name, raw_token_scope=FULL)


def _complete_token_rotate(request, payload: dict, full: FullAccess):
    from .models import MCPAccessToken
    from .token_scopes import FULL
    from .token_views import _render_index

    with transaction.atomic():
        old = MCPAccessToken.objects.filter(pk=payload["rotate_of"], user=request.user).first()
        if old is not None and old.is_active:
            old.is_active = False
            old.save(update_fields=["is_active"])
        name = old.name if old is not None else payload["name"]
        _, raw = MCPAccessToken.create_token(request.user, name=name, ttl_days=90, scope=FULL, full_access=full)
    return _render_index(request, raw_token=raw, raw_token_name=name, raw_token_scope=FULL)


def _complete_token_browser(request, payload: dict, full: FullAccess):
    from .admin_views import mint_and_redirect

    return mint_and_redirect(
        request, callback=payload["callback"], state=payload["state"], name=payload["name"], full_access=full
    )


def _complete_oauth(request, payload: dict, full: FullAccess):
    from .oauth import complete_authorization

    return complete_authorization(request, payload, full)


COMPLETERS = {
    PendingFullAccess.TOKEN_CREATE: _complete_token_create,
    PendingFullAccess.TOKEN_ROTATE: _complete_token_rotate,
    PendingFullAccess.TOKEN_BROWSER: _complete_token_browser,
    PendingFullAccess.OAUTH: _complete_oauth,
}
