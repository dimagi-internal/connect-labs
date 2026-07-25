"""Read-side client for the self-hosted Umami REST API.

Used by the labs-embedded analytics dashboard (/labs/analytics/) so viewers
ride labs' own Connect OAuth instead of needing an Umami login. Auth is the
Umami admin account (password injected from Secrets Manager as
UMAMI_ADMIN_PASSWORD); the JWT is cached in Redis and refreshed on 401.

All calls raise UmamiAPIError on failure — callers render a degraded page,
never a 500.
"""
import logging

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "umami_api_token"
TOKEN_TTL_SECONDS = 3600
REQUEST_TIMEOUT = 15.0


class UmamiAPIError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.UMAMI_HOST_URL and settings.UMAMI_WEBSITE_ID and settings.UMAMI_ADMIN_PASSWORD)


def _base_url() -> str:
    return settings.UMAMI_HOST_URL.rstrip("/")


def _login() -> str:
    response = httpx.post(
        f"{_base_url()}/api/auth/login",
        json={"username": settings.UMAMI_ADMIN_USERNAME, "password": settings.UMAMI_ADMIN_PASSWORD},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise UmamiAPIError(f"Umami login failed ({response.status_code})")
    token = response.json().get("token")
    if not token:
        raise UmamiAPIError("Umami login returned no token")
    cache.set(TOKEN_CACHE_KEY, token, TOKEN_TTL_SECONDS)
    return token


def _get(path: str, params: dict | None = None) -> dict | list:
    """GET an Umami API path, re-authenticating once on 401.

    Note: the labs Redis cache is configured with IGNORE_EXCEPTIONS, so a
    cache outage silently degrades to a login per request — acceptable for
    an admin dashboard.
    """
    if not is_configured():
        raise UmamiAPIError("Umami analytics is not configured")
    token = cache.get(TOKEN_CACHE_KEY) or _login()
    for attempt in (1, 2):
        try:
            response = httpx.get(
                f"{_base_url()}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise UmamiAPIError(f"Umami request failed: {e}") from e
        if response.status_code == 401 and attempt == 1:
            token = _login()
            continue
        if response.status_code != 200:
            raise UmamiAPIError(f"Umami API {path} returned {response.status_code}")
        return response.json()
    raise UmamiAPIError("unreachable")  # pragma: no cover


def get_admin_token() -> str:
    """Cached admin JWT — used for API reads and the labs→Umami SSO bridge."""
    if not is_configured():
        raise UmamiAPIError("Umami analytics is not configured")
    return cache.get(TOKEN_CACHE_KEY) or _login()


def _website_path(suffix: str) -> str:
    return f"/api/websites/{settings.UMAMI_WEBSITE_ID}{suffix}"


def website_stats(start_ms: int, end_ms: int) -> dict:
    """Aggregate pageviews/visitors/visits/bounces for a window."""
    return _get(_website_path("/stats"), {"startAt": start_ms, "endAt": end_ms})


def pageviews_series(start_ms: int, end_ms: int, unit: str = "day") -> dict:
    """Timeseries {pageviews: [{x, y}], sessions: [{x, y}]}."""
    return _get(
        _website_path("/pageviews"),
        {"startAt": start_ms, "endAt": end_ms, "unit": unit, "timezone": "UTC"},
    )


def metrics(metric_type: str, start_ms: int, end_ms: int, limit: int = 10) -> list:
    """Top-N breakdown; metric_type is one of path, event, browser, os, device, country (Umami v3 names)."""
    return _get(
        _website_path("/metrics"),
        {"startAt": start_ms, "endAt": end_ms, "type": metric_type, "limit": limit},
    )


def active_visitors() -> int:
    """Visitors active in the last ~5 minutes. Handles both response shapes."""
    data = _get(_website_path("/active"))
    if isinstance(data, dict):
        return int(data.get("visitors") or data.get("x") or 0)
    if isinstance(data, list) and data:
        return int(data[0].get("x") or data[0].get("visitors") or 0)
    return 0
