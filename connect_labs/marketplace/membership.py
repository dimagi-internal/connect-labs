"""Which organisations a signed-in user acts for, and how they come to.

Two sources, and one function (`orgs_for`) that joins them so nothing else
re-derives the answer:

  * **Connect's own membership**, for organisations Connect knows. It arrives
    with the labs sign-in (`labs.context.get_org_data`) and is matched to a
    `LabsOrg` on either join key. Connect is authoritative for these.
  * **`OrgMembership`**, for the organisations that are local for good -- a
    manufacturer that registers on the supply marketplace has no Connect
    organisation and no reason to get one.

Membership is granted by registering a new organisation (its first admin) or
by opening an invitation. Never by an email address alone: a Connect
account's email is not proof of who employs its holder.
"""

from __future__ import annotations

import hmac
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgInvite, OrgMembership
from connect_labs.supply_chain.update_links import tokens

INVITE_DAYS = 30


def orgs_for(request) -> list[LabsOrg]:
    """Every organisation this request's user may act for, by name."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return []
    from connect_labs.labs.context import get_org_data
    from connect_labs.supply_chain.identity import _integer_org_ids, caller_org_slugs

    organizations = (get_org_data(request) or {}).get("organizations") or []
    ids, slugs = _integer_org_ids(organizations), caller_org_slugs(organizations)
    connect = Q(connect_organization_id__in=ids) | Q(
        connect_organization_id__isnull=True, connect_organization_slug__in=slugs
    )
    return list(LabsOrg.objects.filter(connect | Q(memberships__user=user)).distinct().order_by("name"))


def is_admin(user, org) -> bool:
    return OrgMembership.objects.filter(org=org, user=user, role="admin").exists()


@transaction.atomic
def register(user, org: LabsOrg) -> OrgMembership:
    """Make `user` the first admin of an organisation they just registered."""
    membership, _ = OrgMembership.objects.get_or_create(org=org, user=user, defaults={"role": "admin"})
    return membership


def issue_invite(org: LabsOrg, *, email: str = "", issued_by=None, role: str = "member") -> tuple[OrgInvite, str]:
    """A new invitation and its raw token -- the only time the token exists.

    An organisation with no members yet is being handed over, so its first
    member joins as an admin; after that an invitation grants what it says.
    """
    raw = tokens.new_token()
    if not OrgMembership.objects.filter(org=org).exists():
        role = "admin"
    invite = OrgInvite.objects.create(
        org=org,
        email=email,
        role=role,
        token_hash=_hash(raw),
        token_hint=tokens.hint(raw),
        expires_at=timezone.now() + timedelta(days=INVITE_DAYS),
        issued_by=issued_by,
    )
    return invite, raw


def find_invite(raw) -> OrgInvite | None:
    """The unexpired, unaccepted invitation this token belongs to; else None.

    One answer for a token that never existed, has expired or was used, so a
    caller cannot learn which guesses were ever real.
    """
    if not raw or not isinstance(raw, str) or len(raw) > tokens.MAX_TOKEN_LENGTH:
        return None
    digest = _hash(raw)
    invite = OrgInvite.objects.filter(token_hash=digest).select_related("org").first()
    if invite is None or not hmac.compare_digest(invite.token_hash, digest):
        return None
    if invite.accepted_at is not None or invite.expires_at <= timezone.now():
        return None
    return invite


@transaction.atomic
def accept_invite(invite: OrgInvite, user) -> OrgMembership:
    invite = OrgInvite.objects.select_for_update().get(pk=invite.pk)
    if invite.accepted_at is not None:
        raise ValueError("this invitation has already been used")
    membership, created = OrgMembership.objects.get_or_create(
        org=invite.org, user=user, defaults={"role": invite.role, "added_by": invite.issued_by}
    )
    if not created and invite.role == "admin" and membership.role != "admin":
        membership.role = "admin"
        membership.save(update_fields=["role"])
    invite.accepted_at = timezone.now()
    invite.accepted_by = user
    invite.save(update_fields=["accepted_at", "accepted_by"])
    return membership


def _hash(raw: str) -> str:
    # A different key context from update links, so a link token can never be
    # replayed as an invitation or the reverse.
    return tokens.hash_token(f"org-invite:{raw}")
