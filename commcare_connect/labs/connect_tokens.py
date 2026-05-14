"""Utilities for obtaining a valid Connect OAuth access_token for a user.

The MCP server (and future background jobs) call get_valid_access_token(user)
to receive a current access_token, refreshing automatically if expired.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import httpx

from .models import UserConnectToken

CONNECT_URL = os.environ.get("CONNECT_PRODUCTION_URL", "https://connect.dimagi.com")


class ConnectTokenError(Exception):
    """Raised when a valid Connect access_token cannot be obtained."""


def get_valid_access_token(user) -> str:
    """Return a non-expired Connect access_token for the given user.

    If the stored token is expired, uses the refresh_token to obtain a new one
    and persists the refreshed values.

    Raises ConnectTokenError if no token exists for the user, or if refresh fails.
    """
    try:
        token = UserConnectToken.objects.get(user=user)
    except UserConnectToken.DoesNotExist:
        raise ConnectTokenError(
            f"No Connect OAuth token stored for user {user.username!r}. "
            "User must log into labs in a browser at least once."
        )

    if not token.is_expired:
        return token.access_token

    if not token.refresh_token:
        raise ConnectTokenError(
            f"Connect token for {user.username!r} is expired and no refresh_token is stored. "
            "User must log in again."
        )

    refreshed = _exchange_refresh_token(token.refresh_token)
    token.access_token = refreshed["access_token"]
    if refreshed.get("refresh_token"):
        token.refresh_token = refreshed["refresh_token"]
    token.expires_at = datetime.now(tz=dt_timezone.utc) + timedelta(seconds=refreshed.get("expires_in", 3600))
    token.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return token.access_token


def _exchange_refresh_token(refresh_token: str) -> dict:
    """Exchange a refresh_token for a new access_token at Connect."""
    from django.conf import settings

    client_id = getattr(settings, "CONNECT_OAUTH_CLIENT_ID", None) or os.environ.get("CONNECT_OAUTH_CLIENT_ID")
    if not client_id:
        raise ConnectTokenError("CONNECT_OAUTH_CLIENT_ID not configured")

    response = httpx.post(
        f"{CONNECT_URL}/o/token/",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=10.0,
    )
    if not response.is_success:
        raise ConnectTokenError(f"Connect refresh-token exchange failed: {response.status_code} {response.text[:200]}")
    return response.json()
