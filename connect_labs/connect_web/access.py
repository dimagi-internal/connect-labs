"""Who may use Connect Web: Dimagi staff, plus anyone named in CONNECT_WEB_ALLOWED_EMAILS.

Dimagi staff is labs' one definition (`utils.dimagi_user.is_dimagi_staff`: Django ``is_staff`` or a
Dimagi sign-in). Labs has no feature-flag library — waffle was removed from INSTALLED_APPS during the
labs simplification — so the opt-in for anyone else is an env list of sign-in emails.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

from connect_labs.utils.dimagi_user import is_dimagi_staff


def can_use(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_dimagi_staff(user):
        return True
    allowed = {e.strip().lower() for e in getattr(settings, "CONNECT_WEB_ALLOWED_EMAILS", []) if e and e.strip()}
    return (getattr(user, "email", "") or "").strip().lower() in allowed


class ConnectWebAccessMixin(AccessMixin):
    """Signed in to labs (else to the labs login), then `can_use` (else 403)."""

    login_url = "/labs/login/"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not can_use(request.user):
            raise PermissionDenied("Connect Web is open to Dimagi staff and people it has been opened to.")
        return super().dispatch(request, *args, **kwargs)
