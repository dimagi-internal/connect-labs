"""Utilities for obtaining a valid CommCare HQ OAuth access_token for a user.

Mirrors connect_labs.labs.connect_tokens.get_valid_access_token, but for
CommCare HQ instead of Connect. Headless callers (celery tasks, management
commands) call get_valid_cchq_access_token(user) to receive a current
access_token, refreshing automatically if expired, with no request/session
involved.

The ``UserCCHQToken`` row is the one source of truth for a user's CCHQ token
chain. CommCare HQ rotates refresh tokens (django-oauth-toolkit's default), so
redeeming one revokes the previous access and refresh tokens straight away. If
two copies of the chain refresh on their own, whichever goes second holds a
dead pair. Every refresh, web or headless, goes through refresh_cchq_token()
below: it holds one per-user redis lock and writes the result back to this
row. The browser session keeps a mirror of the row, not a second chain (see
CommCareDataAccess._refresh_token).
"""

from __future__ import annotations

import logging
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import httpx
from django.conf import settings

from connect_labs.labs.models import UserCCHQToken
from connect_labs.utils.lock import try_redis_lock

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

# The lock is held for the whole exchange, retries included, so its expiry has
# to outlast them. Waiters give up after _REFRESH_LOCK_WAIT_SECONDS and re-read
# the row.
_REFRESH_LOCK_TIMEOUT_SECONDS = 60
_REFRESH_LOCK_WAIT_SECONDS = 15


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

    return refresh_cchq_token(user, stale_access_token=token.access_token).access_token


def token_to_session_oauth(token: UserCCHQToken) -> dict:
    """The ``request.session["commcare_oauth"]`` shape of a token row."""
    return {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at.timestamp(),
        "token_type": "Bearer",
    }


@contextmanager
def cchq_refresh_lock(user_id: int):
    """Serialize CCHQ refreshes for one user across processes. Yields whether the lock was taken.

    A page load fires several requests that can each find the token expired at
    once (the auth-status check and the pipeline SSE stream, at least), and a
    scheduled task can do the same. Without the lock they all redeem the same
    refresh_token. Only the first succeeds, and the others wrongly conclude the
    user must re-authorize.

    The lock guards against that race; correctness does not depend on it. If
    the lock backend itself fails (a redis blip, or a cache with no ``lock``
    such as locmem), this logs and yields True, so the caller refreshes
    unlocked as it did before the lock existed.
    """
    with ExitStack() as stack:
        try:
            acquired = stack.enter_context(
                try_redis_lock(
                    f"cchq-oauth-refresh:{user_id}",
                    timeout=_REFRESH_LOCK_TIMEOUT_SECONDS,
                    blocking_timeout=_REFRESH_LOCK_WAIT_SECONDS,
                )
            )
        except Exception:
            logger.exception("CCHQ refresh lock machinery failed; falling back to an unlocked refresh")
            acquired = True
        yield acquired


def refresh_cchq_token(
    user,
    *,
    stale_access_token: str | None = None,
    session_oauth: dict | None = None,
) -> UserCCHQToken:
    """Refresh the user's CCHQ token under the per-user lock and persist it. Returns the fresh row.

    Args:
        user: the token's owner.
        stale_access_token: the access token the caller found expired or saw
            rejected. A row that has moved past it while we waited for the lock
            means another caller already refreshed, so we return that row
            without redeeming anything.
        session_oauth: the browser session's copy of the chain, if any. It
            covers a user with no row yet (connected before rows existed) and
            a session that refreshed on its own before this module owned the
            chain. Whichever copy is newer is redeemed first, and the older one
            is tried only if CCHQ rejects the newer.

    Raises:
        CCHQReLoginRequired: CCHQ rejected every refresh_token we hold.
        CCHQTokenError: nothing to refresh with, the lock stayed busy, or the
            exchange failed for another reason.
    """
    with cchq_refresh_lock(user.pk) as acquired:
        token = UserCCHQToken.objects.filter(user=user).first()
        if token is not None and not token.is_expired and token.access_token != stale_access_token:
            return token
        if not acquired:
            raise CCHQTokenError(
                f"Another CommCare HQ token refresh for {user.username!r} is still in flight; try again shortly."
            )

        candidates: list[tuple[float, str]] = []
        if token is not None and token.refresh_token:
            candidates.append((token.expires_at.timestamp(), token.refresh_token))
        session_refresh = (session_oauth or {}).get("refresh_token")
        if session_refresh and all(session_refresh != rt for _, rt in candidates):
            candidates.append((float(session_oauth.get("expires_at") or 0), session_refresh))
        if not candidates:
            raise CCHQTokenError(
                f"No CommCare HQ refresh_token stored for {user.username!r}. "
                "User must re-authorize at /labs/commcare/initiate/."
            )

        rejection: CCHQReLoginRequired | None = None
        for _, refresh_token in sorted(candidates, key=lambda c: c[0], reverse=True):
            try:
                refreshed = _exchange_refresh_token(
                    refresh_token, token_id=token.pk if token else None, username=user.username
                )
            except CCHQReLoginRequired as exc:
                rejection = exc
                continue
            token, _ = UserCCHQToken.objects.update_or_create(
                user=user,
                defaults={
                    "access_token": refreshed["access_token"],
                    "refresh_token": refreshed.get("refresh_token") or refresh_token,
                    "expires_at": datetime.now(tz=dt_timezone.utc)
                    + timedelta(seconds=refreshed.get("expires_in", 3600)),
                },
            )
            return token
        raise rejection


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
