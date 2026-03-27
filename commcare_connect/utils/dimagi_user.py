"""Utility for checking if a user is a Dimagi staff member."""

from django.conf import settings


def is_dimagi_user(user) -> bool:
    """Return True if the user is a Dimagi staff member.

    Checks the user's email for @dimagi.com domain first. Falls back to
    LABS_ADMIN_USERNAMES allowlist for dev/test accounts where the email
    is not available via OAuth introspection (openid scope not yet enabled).
    """
    email = getattr(user, "email", "") or ""
    if email.endswith("@dimagi.com"):
        return True
    username = getattr(user, "username", "") or ""
    admin_usernames = getattr(settings, "LABS_ADMIN_USERNAMES", [])
    return username in admin_usernames
