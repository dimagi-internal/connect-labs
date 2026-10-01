"""A user's Open Chat Studio token, without a browser session.

Mirrors ``commcare/cchq_tokens.py``. The ``UserOCSToken`` row is the one source of
truth for a user's OCS token chain; the browser session (``request.session
["ocs_oauth"]``) keeps a mirror of it. OCS is django-oauth-toolkit, which rotates
refresh tokens — redeeming one revokes the previous pair — so every refresh, web
or headless, goes through ``refresh_ocs_token`` under one per-user lock and writes
the result back to the row.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import httpx
from django.conf import settings

from connect_labs.labs.models import UserOCSToken
from connect_labs.utils.lock import try_redis_lock

logger = logging.getLogger(__name__)

_REFRESH_LOCK_TIMEOUT_SECONDS = 60
_REFRESH_LOCK_WAIT_SECONDS = 15

CONNECT_PATH = "/labs/ocs/initiate/"

# The person an MCP tool call is running for. Set around every MCP tool call
# (connect_labs.mcp.server); unset for web requests and background jobs. A pipeline
# that reads OCS sessions with no key of its own uses settings.OCS_API_KEY -- the
# server's team key, which reads EVERY bot's sessions. That is a deliberate choice
# for the web dashboards built on it; it is not a choice an MCP caller gets to make
# by naming an experiment id in a preview. So under an MCP call it reads as the
# caller instead, with their own OCS token, and sees exactly what they can see in OCS.
_mcp_caller: ContextVar = ContextVar("labs_ocs_mcp_caller", default=None)


def current_mcp_caller():
    """The user an MCP tool call is running for, or None outside one."""
    return _mcp_caller.get()


@contextmanager
def mcp_caller(user):
    """Mark the duration of an MCP tool call as running for ``user``."""
    reset = _mcp_caller.set(user)
    try:
        yield
    finally:
        _mcp_caller.reset(reset)


class OCSTokenError(Exception):
    """A usable OCS access token cannot be had for this user."""


class OCSReLoginRequired(OCSTokenError):
    """OCS rejected the refresh token: the user must connect OCS again."""


def save_ocs_token(user, oauth: dict) -> UserOCSToken:
    """Write a session-shaped token (``{access_token, refresh_token, expires_at, scope}``,
    ``expires_at`` in epoch seconds) to the user's row."""
    token, _ = UserOCSToken.objects.update_or_create(
        user=user,
        defaults={
            "access_token": oauth["access_token"],
            "refresh_token": oauth.get("refresh_token") or "",
            "expires_at": datetime.fromtimestamp(float(oauth.get("expires_at") or 0), tz=dt_timezone.utc),
            "scope": (oauth.get("scope") or "")[:255],
        },
    )
    return token


def token_to_session_oauth(token: UserOCSToken) -> dict:
    """The ``request.session["ocs_oauth"]`` shape of a row."""
    return {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at.timestamp(),
        "token_type": "Bearer",
        "scope": token.scope,
    }


def forget_ocs_token(user) -> None:
    """The user disconnected OCS: no caller may use the token any more."""
    UserOCSToken.objects.filter(user=user).delete()


def get_valid_ocs_access_token(user) -> str:
    """A current OCS access token for ``user``, refreshed if it expired.

    Raises OCSTokenError (OCSReLoginRequired when OCS refused the refresh) when
    there is none — the message names where to connect.
    """
    token = UserOCSToken.objects.filter(user=user).first()
    if token is None:
        raise OCSTokenError(f"{user.username} has not connected Open Chat Studio. Connect at {CONNECT_PATH}.")
    if not token.is_expired:
        return token.access_token
    return refresh_ocs_token(user, stale_access_token=token.access_token).access_token


@contextmanager
def _refresh_lock(user_id: int):
    """Serialise one user's refreshes across processes; yields whether the lock was
    taken. If the lock machinery itself fails, refresh unlocked (as cchq_tokens)."""
    with ExitStack() as stack:
        try:
            acquired = stack.enter_context(
                try_redis_lock(
                    f"ocs-oauth-refresh:{user_id}",
                    timeout=_REFRESH_LOCK_TIMEOUT_SECONDS,
                    blocking_timeout=_REFRESH_LOCK_WAIT_SECONDS,
                )
            )
        except Exception:
            logger.exception("OCS refresh lock machinery failed; falling back to an unlocked refresh")
            acquired = True
        yield acquired


def refresh_ocs_token(user, *, stale_access_token: str | None = None, session_oauth: dict | None = None):
    """Refresh under the per-user lock and persist; returns the fresh row.

    A row that moved past ``stale_access_token`` while we waited means another
    caller refreshed already, and that row is returned. ``session_oauth`` covers a
    session that refreshed on its own before the row owned the chain: whichever
    copy is newer is redeemed first.
    """
    with _refresh_lock(user.pk) as acquired:
        token = UserOCSToken.objects.filter(user=user).first()
        if token is not None and not token.is_expired and token.access_token != stale_access_token:
            return token
        if not acquired:
            raise OCSTokenError("Another Open Chat Studio token refresh is in flight; try again shortly.")

        candidates: list[tuple[float, str]] = []
        if token is not None and token.refresh_token:
            candidates.append((token.expires_at.timestamp(), token.refresh_token))
        session_refresh = (session_oauth or {}).get("refresh_token")
        if session_refresh and all(session_refresh != rt for _, rt in candidates):
            candidates.append((float((session_oauth or {}).get("expires_at") or 0), session_refresh))
        if not candidates:
            raise OCSReLoginRequired(f"No Open Chat Studio refresh token held. Connect again at {CONNECT_PATH}.")

        rejection: OCSReLoginRequired | None = None
        for _, refresh_token in sorted(candidates, key=lambda c: c[0], reverse=True):
            try:
                refreshed = _exchange(refresh_token)
            except OCSReLoginRequired as exc:
                rejection = exc
                continue
            now = datetime.now(tz=dt_timezone.utc)
            return save_ocs_token(
                user,
                {
                    "access_token": refreshed["access_token"],
                    "refresh_token": refreshed.get("refresh_token") or refresh_token,
                    "expires_at": (now + timedelta(seconds=refreshed.get("expires_in", 3600))).timestamp(),
                    "scope": refreshed.get("scope") or (token.scope if token else ""),
                },
            )
        raise rejection


def _exchange(refresh_token: str) -> dict:
    client_id = getattr(settings, "OCS_OAUTH_CLIENT_ID", "")
    client_secret = getattr(settings, "OCS_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise OCSTokenError("OCS_OAUTH_CLIENT_ID / OCS_OAUTH_CLIENT_SECRET are not configured")
    base = getattr(settings, "OCS_URL", "https://www.openchatstudio.com").rstrip("/")
    try:
        response = httpx.post(
            f"{base}/o/token/",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise OCSTokenError(f"Open Chat Studio token refresh failed: {type(exc).__name__}") from exc
    if response.status_code == 200:
        return response.json()
    logger.warning("OCS refresh-token exchange failed: status=%s body=%s", response.status_code, response.text[:300])
    if response.status_code in (400, 401):
        raise OCSReLoginRequired(f"Open Chat Studio authorization has expired. Connect again at {CONNECT_PATH}.")
    raise OCSTokenError(f"Open Chat Studio token refresh failed: {response.status_code}")
