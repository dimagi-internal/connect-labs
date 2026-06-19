# commcare_connect/campaign/middleware.py
"""Keep Django auth and session['campaign_oauth'] consistent under /campaign/.

Mirrors labs/oauth_session.py but is scoped to this app and its own session
key. Plan 1 has no refresh-token store, so an expired token => logout.
"""
from __future__ import annotations

import logging

from django.contrib.auth import logout
from django.utils import timezone

logger = logging.getLogger(__name__)

_PATH_PREFIX = "/campaign/"
_SKIP_PREFIXES = (
    "/campaign/login/",
    "/campaign/logout/",
    "/campaign/ping/",
)


class CampaignOAuthSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_check(request):
            self._sync(request)
        return self.get_response(request)

    @staticmethod
    def _should_check(request) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        path = request.path
        if not path.startswith(_PATH_PREFIX):
            return False
        return not any(path.startswith(p) for p in _SKIP_PREFIXES)

    @staticmethod
    def _sync(request) -> None:
        campaign_oauth = request.session.get("campaign_oauth")
        if not campaign_oauth:
            logout(request)
            return
        if timezone.now().timestamp() >= campaign_oauth.get("expires_at", 0):
            logger.info("campaign_oauth expired for %s; logging out", request.user.username)
            request.session.pop("campaign_oauth", None)
            logout(request)
