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
exist in a deployed environment. Precedent: the OES satellite's
/oes/dev-login/, which this now matches in the one respect that matters -- it
signs in a user the seeder already created and REFUSES if there is none,
rather than creating one.

That distinction is the whole safety argument. An earlier version called
get_or_create with is_superuser=True, so a single misconfiguration
(DJANGO_DEBUG=True in a deployed environment) turned one unauthenticated GET
into a superuser. Signing in an existing local-only user degrades that to
"signs in a user that does not exist there", which fails closed.
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
    user = User.objects.filter(username=DEV_USERNAME).first()
    if user is None:
        # Deliberately does NOT create one. See the module docstring: creating
        # a superuser here is a far worse failure mode than refusing to.
        raise Http404(f'no local user {DEV_USERNAME!r}. Run: make manage CMD="supply_dev_seed"')

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
