"""DEBUG-only local sign-in, so the supply screens can be iterated on without OAuth.

`connect_labs/labs/oauth_session.py` logs out any authenticated user whose session
has no `labs_oauth` blob and sends them to the real Connect OAuth flow. That is
correct in every deployed environment and impossible to complete on a laptop, so
without this there is no way to open a labs page locally.

This mints the session the middleware expects: a far-future expiry, so the
refresh-or-logout path is never taken, and an empty organization_data, so the
only context that appears is whatever labs-only SyntheticOpportunity rows the
user is allowed to see (see labs.context._merge_labs_only_opps). That keeps the
local view honest — it shows synthetic programmes and nothing else.

Wired only when settings.DEBUG, and it raises Http404 otherwise, so it cannot
exist in a deployed environment. Precedent: the OES satellite's /oes/dev-login/.
"""

import time

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import Http404
from django.shortcuts import redirect

DEV_USERNAME = "dev"
ONE_YEAR = 365 * 24 * 60 * 60


def dev_login(request):
    """Sign in the local dev user and mint the labs_oauth shape the middleware wants."""
    if not settings.DEBUG:
        raise Http404("dev-login is available only when DEBUG is on")

    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=DEV_USERNAME,
        defaults={"email": "dev@dimagi.com", "is_staff": True, "is_superuser": True},
    )
    if not user.view_synthetic_opps:
        user.view_synthetic_opps = True
        user.save(update_fields=["view_synthetic_opps"])

    # ModelBackend is not the only backend configured, so name it explicitly.
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    request.session["labs_oauth"] = {
        "access_token": "local-dev-token",
        "refresh_token": "local-dev-refresh",
        "expires_at": time.time() + ONE_YEAR,
        "organization_data": {"organizations": [], "programs": [], "opportunities": []},
    }
    request.session.modified = True

    return redirect(request.GET.get("next") or "/supply/")
