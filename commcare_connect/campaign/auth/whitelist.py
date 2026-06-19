"""Decide whether a CommCare identity may sign in, and with what role."""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from commcare_connect.campaign.models import CampaignUser


def is_bootstrap_admin(email: str) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    allowed = [d.lower() for d in getattr(settings, "CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS", ["dimagi.com"])]
    return domain in allowed


def resolve_campaign_user(identity: dict, django_user) -> CampaignUser | None:
    username = identity.get("username") or ""
    email = identity.get("email") or ""
    name = identity.get("name") or ""

    cu = CampaignUser.objects.filter(commcare_username=username).first()

    if cu is None:
        if not is_bootstrap_admin(email):
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
