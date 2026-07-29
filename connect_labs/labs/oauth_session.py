"""Middleware that keeps Django's session and the labs OAuth token consistent.

The labs UI lies when Django's auth says the user is logged in but the
``session["labs_oauth"]`` token is dead. Symptoms: avatar shows everywhere,
but every view that talks to prod renders a silent empty page because
``SolicitationsDataAccess(request=request)`` (and the equivalents in other
labs apps) raise ``ValueError`` on a missing access token and the call sites
swallow that into ``ctx["solicitations"] = []`` etc. See gh#198.

The middleware closes the gap. On every request, if the session token has
expired, it tries the refresh path that already exists for the MCP server
(``UserConnectToken.refresh_token`` via ``get_valid_access_token``) and
writes the new access token back into the session. If the refresh fails —
or the user has no ``UserConnectToken`` at all, or no ``labs_oauth`` shape
in their session — Django's auth is cleared too, so the UI never reports
"logged in" when it can't actually talk to prod.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone

logger = logging.getLogger(__name__)


# Labs-owned paths that either *create* the session.labs_oauth shape, *consume*
# a different auth mechanism, or are otherwise outside the labs UI surface.
# Running the refresh-or-logout check on these would be incorrect (e.g., logging
# the user out mid-OAuth-callback) or pointless (e.g., the MCP endpoint
# authenticates via Bearer PAT, not session).
_LABS_SKIP_PATH_PREFIXES = (
    "/labs/login/",
    "/labs/initiate/",
    "/labs/callback/",
    "/labs/logout/",
    "/labs/test-auth/",
    "/labs/commcare/",
    "/labs/ocs/",
    "/mcp/",
    "/admin/",
    "/o/",
    "/health/",
)

# This middleware is the labs authentication boundary in a SHARED-AUTH host:
# labs and every standalone satellite site (supply, campaign, and any future
# site) run in one Django project against one users.User table and one session
# cookie, so `request.user.is_authenticated` is global — signing into any site
# authenticates you everywhere. The boundary that keeps a satellite login from
# BECOMING a labs login is here: any authenticated request to a NON-skipped path
# with no live `session["labs_oauth"]` is logged out (see `_sync_session_token`).
# Labs access therefore requires a labs OAuth session, which a satellite's own
# login never establishes.
#
# Each satellite must skip-list its URL prefix, or its own users get logged out
# on every request to it. Satellites declare their prefix in the
# `LABS_SATELLITE_URL_PREFIXES` setting (alongside their INSTALLED_APPS + urls
# entries) rather than importing labs — see docs/multi-site-auth.md. Kept as a
# host setting so a new site is one config line, not an edit to this module.
_DEFAULT_SATELLITE_URL_PREFIXES = ("/supply/", "/campaign/")


def get_skip_path_prefixes() -> tuple[str, ...]:
    """All path prefixes exempt from the labs OAuth reconcile/logout check.

    Labs-owned prefixes plus every registered satellite site's prefix
    (`settings.LABS_SATELLITE_URL_PREFIXES`). This is the multi-site host
    contract; `docs/multi-site-auth.md` documents how to extend it.
    """
    satellites = tuple(getattr(settings, "LABS_SATELLITE_URL_PREFIXES", _DEFAULT_SATELLITE_URL_PREFIXES))
    return _LABS_SKIP_PATH_PREFIXES + satellites


# Back-compat alias: some callers/tests reference this name. Prefer
# get_skip_path_prefixes(), which also reflects registered satellites.
_SKIP_PATH_PREFIXES = _LABS_SKIP_PATH_PREFIXES


class LabsOAuthSessionMiddleware:
    """Reconcile Django auth and session.labs_oauth on every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_check(request):
            self._sync_session_token(request)
        return self.get_response(request)

    @staticmethod
    def _should_check(request) -> bool:
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return False
        path = request.path
        return not any(path.startswith(p) for p in get_skip_path_prefixes())

    @staticmethod
    def _sync_session_token(request) -> None:
        labs_oauth = request.session.get("labs_oauth")

        if not labs_oauth:
            # Django session survived but the labs payload is gone (cleared
            # cookie, partial logout, etc.). Re-OAuth needed — drop Django
            # auth too so the avatar disappears.
            logger.info(
                "labs_oauth missing for authenticated user %s; logging out",
                request.user.username,
            )
            logout(request)
            return

        expires_at = labs_oauth.get("expires_at", 0)
        if timezone.now().timestamp() < expires_at:
            return  # Session token still valid.

        # Session token expired. Try the refresh path that the MCP server uses.
        from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
        from connect_labs.labs.models import UserConnectToken

        try:
            access_token = get_valid_access_token(request.user)
            uct = UserConnectToken.objects.get(user=request.user)
        except (ConnectTokenError, UserConnectToken.DoesNotExist) as e:
            logger.info(
                "labs_oauth refresh failed for %s (%s); logging out",
                request.user.username,
                e.__class__.__name__,
            )
            logout(request)
            return

        # Preserve organization_data and other session payload; only refresh
        # the access_token and expires_at.
        labs_oauth["access_token"] = access_token
        labs_oauth["expires_at"] = uct.expires_at.timestamp()
        request.session["labs_oauth"] = labs_oauth
        request.session.modified = True
