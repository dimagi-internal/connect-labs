"""Fetch the current CommCare HQ user's identity for login + whitelisting."""
from __future__ import annotations

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class IdentityError(Exception):
    pass


def fetch_identity(access_token: str) -> dict:
    base = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")
    url = f"{base}/api/v0.5/identity/"
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15.0)
    except httpx.RequestError as e:
        logger.warning("CommCare identity network error: %s", e)
        raise IdentityError(str(e)) from e
    if resp.status_code != 200:
        logger.warning("CommCare identity returned %s", resp.status_code)
        raise IdentityError(f"identity status {resp.status_code}")
    data = resp.json()
    name = " ".join(p for p in [data.get("first_name", ""), data.get("last_name", "")] if p).strip()
    return {
        "username": data.get("username") or "",
        "email": data.get("email") or "",
        "name": name,
        "domains": data.get("domains") or [],
    }
