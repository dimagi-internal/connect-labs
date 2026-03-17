"""Connect API client for resolving opportunity → CommCare domain + app IDs.

Reads the CLI OAuth token from ~/.commcare-connect/token.json (managed by
the Connect CLI token_manager). Calls the Connect production API to fetch
opportunity metadata including learn/deliver app cc_domain and cc_app_id.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CONNECT_URL = os.environ.get("CONNECT_PRODUCTION_URL", "https://connect.dimagi.com")
TOKEN_FILE = Path.home() / ".commcare-connect" / "token.json"
HTTP_TIMEOUT = httpx.Timeout(connect=10, read=30, write=10, pool=10)

# Cache: opportunity_id -> metadata dict
_opportunity_cache: dict[int, dict] = {}


def _get_connect_token() -> str:
    """Read the Connect CLI OAuth token from ~/.commcare-connect/token.json."""
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"No Connect CLI token found at {TOKEN_FILE}. " "Run the CLI OAuth flow first to generate a token."
        )

    data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

    # Unwrap v2 multi-profile format
    if "_version" in data and "profiles" in data:
        active = data.get("_active_profile")
        profiles = data["profiles"]
        if active and active in profiles:
            data = profiles[active]
        elif profiles:
            data = next(iter(profiles.values()))
        else:
            raise ValueError("No profiles in token file.")

    # Check expiry
    if "expires_at" in data:
        expires_at = datetime.fromisoformat(data["expires_at"])
        if datetime.now() >= (expires_at - timedelta(minutes=5)):
            raise PermissionError(
                f"Connect CLI token expired at {expires_at}. " "Run the CLI OAuth flow again to refresh."
            )

    token = data.get("access_token")
    if not token:
        raise ValueError("No access_token in token file.")
    return token


async def get_opportunity_apps(opportunity_id: int) -> dict:
    """Fetch opportunity metadata from Connect API.

    Returns:
        {
            "opportunity_id": int,
            "opportunity_name": str,
            "learn_app": {"cc_domain": str, "cc_app_id": str, "name": str} | None,
            "deliver_app": {"cc_domain": str, "cc_app_id": str, "name": str} | None,
        }
    """
    if opportunity_id in _opportunity_cache:
        return _opportunity_cache[opportunity_id]

    token = _get_connect_token()
    url = f"{CONNECT_URL}/export/opportunity/{opportunity_id}/"

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code in (401, 403):
            raise PermissionError(
                f"Connect API auth failed: HTTP {resp.status_code}. " "Your CLI token may have expired."
            )
        resp.raise_for_status()
        data = resp.json()

    def _extract_app(app_data: dict | None) -> dict | None:
        if not app_data:
            return None
        return {
            "cc_domain": app_data.get("cc_domain", ""),
            "cc_app_id": app_data.get("cc_app_id", ""),
            "name": app_data.get("name", ""),
        }

    result = {
        "opportunity_id": opportunity_id,
        "opportunity_name": data.get("name", ""),
        "learn_app": _extract_app(data.get("learn_app")),
        "deliver_app": _extract_app(data.get("deliver_app")),
    }

    _opportunity_cache[opportunity_id] = result
    return result


async def resolve_domain_and_app(
    opportunity_id: int | None = None,
    domain: str = "",
    app_id: str = "",
    app_type: str = "deliver",
) -> tuple[str, str]:
    """Resolve domain and app_id from either direct params or opportunity_id.

    If opportunity_id is provided, fetches the opportunity metadata and uses
    the learn or deliver app's cc_domain and cc_app_id.

    Args:
        opportunity_id: Connect opportunity ID (takes priority)
        domain: Direct CommCare domain (fallback)
        app_id: Direct CommCare app ID (fallback)
        app_type: "deliver" or "learn" (which app to use from opportunity)

    Returns:
        (domain, app_id) tuple
    """
    if opportunity_id:
        opp = await get_opportunity_apps(opportunity_id)
        app_key = f"{app_type}_app"
        app_data = opp.get(app_key)
        if not app_data:
            raise ValueError(
                f"Opportunity {opportunity_id} has no {app_type} app. "
                f"Available: {', '.join(k for k in ('learn_app', 'deliver_app') if opp.get(k))}"
            )
        return app_data["cc_domain"], app_data["cc_app_id"]

    if not domain:
        raise ValueError(
            "Provide either opportunity_id or domain. "
            "Use get_opportunity_apps to find the domain for an opportunity."
        )
    if not app_id:
        raise ValueError("Provide app_id when using domain directly. " "Use list_apps to find available app IDs.")
    return domain, app_id
