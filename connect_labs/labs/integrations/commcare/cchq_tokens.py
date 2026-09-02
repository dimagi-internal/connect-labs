"""Utilities for obtaining a valid CommCare HQ OAuth access_token for a user.

Mirrors connect_labs.labs.connect_tokens.get_valid_access_token, but for
CommCare HQ instead of Connect. Headless callers (celery tasks, management
commands) call get_valid_cchq_access_token(user) to receive a current
access_token, refreshing automatically if expired, with no request/session
involved.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import httpx
from django.conf import settings

from connect_labs.labs.models import UserCCHQToken

logger = logging.getLogger(__name__)

# A transient blip on CommCare HQ's OAuth endpoint (timeout, connection reset,
# 5xx) was being treated identically to a genuinely dead refresh_token --
# both surfaced as "CCHQ unavailable" for that run, and for flw_daily_summary_report
# specifically that meant work_areas_left silently went missing for the whole
# day with no visible error (see 2026-09-02 incident: program 217's schedule
# reported STATUS_OK -- the Connect-token half succeeded -- while the CCHQ
# half had quietly failed). Retrying a few times with backoff absorbs exactly
# that kind of blip without waiting for a human to notice and re-authorize.
# A 400/401 (the refresh_token itself rejected) is NOT retried here -- that's
# a real "must re-login" case, not a blip, and retrying it would just delay
# the CCHQReLoginRequired the caller needs to see.
_REFRESH_RETRY_ATTEMPTS = 3
_REFRESH_RETRY_BACKOFF_SECONDS = 2.0


class CCHQTokenError(Exception):
    """Raised when a valid CommCare HQ access_token cannot be obtained."""


class CCHQReLoginRequired(CCHQTokenError):
    """Raised when the refresh_token is no longer valid and the user must re-authorize."""


def get_valid_cchq_access_token(user) -> str:
    """Return a non-expired CommCare HQ access_token for the given user.

    If the stored token is expired, uses the refresh_token to obtain a new one
    and persists the refreshed values.

    Raises CCHQTokenError if no token exists for the user, or if refresh fails.
    """
    try:
        token = UserCCHQToken.objects.get(user=user)
    except UserCCHQToken.DoesNotExist:
        raise CCHQTokenError(
            f"No CommCare HQ OAuth token stored for user {user.username!r}. "
            "User must authorize CommCare access at /labs/commcare/initiate/ at least once."
        )

    if not token.is_expired:
        return token.access_token

    if not token.refresh_token:
        raise CCHQTokenError(
            f"CommCare HQ token for {user.username!r} is expired and no refresh_token is stored. "
            "User must re-authorize at /labs/commcare/initiate/."
        )

    refreshed = _exchange_refresh_token(token.refresh_token, token_id=token.pk, username=user.username)
    token.access_token = refreshed["access_token"]
    if refreshed.get("refresh_token"):
        token.refresh_token = refreshed["refresh_token"]
    token.expires_at = datetime.now(tz=dt_timezone.utc) + timedelta(seconds=refreshed.get("expires_in", 3600))
    token.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return token.access_token


def _exchange_refresh_token(refresh_token: str, *, token_id: int | None = None, username: str | None = None) -> dict:
    """Exchange a refresh_token for a new access_token at CommCare HQ.

    Retries transient failures (timeouts, connection errors, 5xx) up to
    _REFRESH_RETRY_ATTEMPTS times with linear backoff before giving up. A
    400/401 is never retried -- it means CommCare HQ rejected the
    refresh_token itself, which a retry cannot fix.
    """
    client_id = getattr(settings, "COMMCARE_OAUTH_CLIENT_ID", "")
    client_secret = getattr(settings, "COMMCARE_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise CCHQTokenError("COMMCARE_OAUTH_CLIENT_ID / COMMCARE_OAUTH_CLIENT_SECRET not configured")

    commcare_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")
    last_error: str | None = None

    for attempt in range(1, _REFRESH_RETRY_ATTEMPTS + 1):
        try:
            response = httpx.post(
                f"{commcare_url}/oauth/token/",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "CommCare HQ refresh-token exchange network error on attempt %s/%s: token_row=%s user=%s error=%s",
                attempt,
                _REFRESH_RETRY_ATTEMPTS,
                token_id,
                username,
                last_error,
            )
            if attempt < _REFRESH_RETRY_ATTEMPTS:
                time.sleep(_REFRESH_RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise CCHQTokenError(f"CommCare HQ refresh-token exchange failed after retries: {last_error}") from exc

        if response.status_code == 200:
            return response.json()

        # CommCare HQ rejected the refresh. Log enough to diagnose without leaking the token itself.
        logger.warning(
            "CommCare HQ refresh-token exchange failed: status=%s attempt=%s/%s token_row=%s user=%s body=%s",
            response.status_code,
            attempt,
            _REFRESH_RETRY_ATTEMPTS,
            token_id,
            username,
            response.text[:500],
        )
        if response.status_code in (400, 401):
            # invalid_grant / invalid_client almost always means the stored refresh_token
            # was rotated out or hit its absolute lifetime — there is no recovery short of
            # the user re-running the browser OAuth flow at /labs/commcare/initiate/.
            # Not retried: a dead refresh_token doesn't come back with time.
            raise CCHQReLoginRequired(
                "Your CommCare HQ authorization has expired. Re-authorize at "
                "/labs/commcare/initiate/ to restore access. "
                f"(CommCare HQ returned {response.status_code}: {response.text[:200]})"
            )

        # Other statuses (5xx, etc.) are treated as transient and retried.
        last_error = f"{response.status_code} {response.text[:200]}"
        if attempt < _REFRESH_RETRY_ATTEMPTS:
            time.sleep(_REFRESH_RETRY_BACKOFF_SECONDS * attempt)
            continue
        raise CCHQTokenError(f"CommCare HQ refresh-token exchange failed after retries: {last_error}")
