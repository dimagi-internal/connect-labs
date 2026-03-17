"""CommCare HQ API client for fetching application definitions.

Auth: Loaded automatically from the project's .env file.
Uses COMMCARE_USERNAME + COMMCARE_API_KEY to build the ApiKey header.
Caching: In-memory, keyed by (domain, app_id). Invalidated on server restart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env from the project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

HQ_URL = os.environ.get("COMMCARE_HQ_URL", "https://www.commcarehq.org")
HQ_DOMAIN = os.environ.get("COMMCARE_HQ_DOMAIN", "")
HTTP_TIMEOUT = httpx.Timeout(connect=10, read=120, write=10, pool=10)

# In-memory cache: (domain, app_id) -> app definition dict
_app_cache: dict[tuple[str, str], dict] = {}
# domain -> list of app summaries
_app_list_cache: dict[str, list[dict]] = {}


def _auth_header() -> dict[str, str]:
    """Build Authorization header from .env credentials."""
    username = os.environ.get("COMMCARE_USERNAME", "")
    api_key = os.environ.get("COMMCARE_API_KEY", "")
    if not username or not api_key:
        raise ValueError(
            "COMMCARE_USERNAME and COMMCARE_API_KEY must be set in .env. "
            f"Looked for .env at: {_PROJECT_ROOT / '.env'}"
        )
    return {"Authorization": f"ApiKey {username}:{api_key}"}


async def list_apps(domain: str | None = None) -> list[dict]:
    """Fetch all applications for a domain from CommCare HQ.

    Returns list of dicts with: id, name, version, module_count, form_count.
    Results are cached in memory.
    """
    domain = domain or HQ_DOMAIN
    if not domain:
        raise ValueError("No domain specified. Set COMMCARE_HQ_DOMAIN or pass domain parameter.")

    if domain in _app_list_cache:
        return _app_list_cache[domain]

    apps_raw = await _fetch_all_apps(domain)
    summaries = []
    for app in apps_raw:
        modules = app.get("modules", [])
        form_count = sum(len(m.get("forms", [])) for m in modules)
        summaries.append(
            {
                "id": app.get("id", ""),
                "name": app.get("name", ""),
                "version": app.get("version", 0),
                "is_released": app.get("is_released", False),
                "module_count": len(modules),
                "form_count": form_count,
            }
        )

    _app_list_cache[domain] = summaries
    return summaries


async def get_app(domain: str | None, app_id: str) -> dict:
    """Fetch a single application definition. Cached after first fetch."""
    domain = domain or HQ_DOMAIN
    if not domain:
        raise ValueError("No domain specified.")

    cache_key = (domain, app_id)
    if cache_key in _app_cache:
        return _app_cache[cache_key]

    # Fetch all apps and find the one we want
    apps = await _fetch_all_apps(domain)
    for app in apps:
        key = (domain, app.get("id", ""))
        _app_cache[key] = app

    if cache_key not in _app_cache:
        raise ValueError(f"App {app_id} not found in domain {domain}")

    return _app_cache[cache_key]


async def _fetch_all_apps(domain: str) -> list[dict]:
    """Fetch all application definitions from the HQ API with pagination."""
    url = f"{HQ_URL}/a/{domain}/api/v0.5/application/"
    params = {"limit": 100}
    apps: list[dict] = []

    async with httpx.AsyncClient(
        headers=_auth_header(),
        timeout=HTTP_TIMEOUT,
    ) as client:
        while url:
            resp = await client.get(url, params=params)
            if resp.status_code in (401, 403):
                raise PermissionError(
                    f"CommCare HQ auth failed for domain {domain}: HTTP {resp.status_code}. "
                    "Check COMMCARE_USERNAME and COMMCARE_API_KEY in .env."
                )
            resp.raise_for_status()
            data = resp.json()
            apps.extend(data.get("objects", []))
            url = data.get("next")
            params = {}  # next URL includes params

    logger.info("Fetched %d apps for domain %s", len(apps), domain)
    return apps


def clear_cache():
    """Clear all cached app definitions."""
    _app_cache.clear()
    _app_list_cache.clear()
