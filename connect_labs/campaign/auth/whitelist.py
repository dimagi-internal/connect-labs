"""Decide whether a CommCare identity may sign in, and with what role."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from connect_labs.campaign.models import CampaignUser
from connect_labs.utils.dimagi_user import is_dimagi_email


def is_bootstrap_admin(email: str) -> bool:
    if not email or "@" not in email:
        return False
    configured = getattr(settings, "CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS", None)
    if configured is None:
        # Not configured: Dimagi's own sign-ins, by the one list labs keeps.
        return is_dimagi_email(email)
    domain = email.rsplit("@", 1)[1].lower()
    return domain in [d.lower().lstrip("@") for d in configured]


def verified_email(identity: dict) -> str:
    """The address CommCare HQ vouches for, or "".

    HQ's identity API returns an ``email`` field that a project's admin can set to
    anything on a user they manage (a mobile worker's email is never confirmed), so
    it proves nothing about who is signing in. The one address HQ does stand behind
    is a web user's login name, which IS their email. So an address counts only when
    it is the HQ username itself. A mobile worker (``name@project.commcarehq.org``)
    therefore never matches a Dimagi domain or anyone else's labs account.
    """
    username = (identity.get("username") or "").strip()
    email = (identity.get("email") or "").strip()
    if "@" in username and (not email or email.lower() == username.lower()):
        return username
    return ""


def resolve_campaign_user(identity: dict, django_user) -> CampaignUser | None:
    username = identity.get("username") or ""
    email = identity.get("email") or ""
    name = identity.get("name") or ""

    cu = CampaignUser.objects.filter(commcare_username=username).first()

    if cu is None:
        # Bootstrap on the address HQ vouches for, never on the editable email field:
        # otherwise anyone who can create a CommCare project could name a mobile
        # worker x@dimagi.com and be made a campaign admin.
        if not is_bootstrap_admin(verified_email(identity)):
            return None
        cu = CampaignUser(commcare_username=username, role="campaign_admin")
    elif not cu.is_active_member:
        # An explicit, non-active whitelist row is a hard deny — even for
        # bootstrap-domain users (an admin deliberately deactivated them).
        return None

    cu.user = django_user
    cu.email = email or cu.email
    cu.name = name or cu.name
    cu.last_login_at = timezone.now()
    cu.save()
    return cu
