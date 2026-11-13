"""Who CommCare says a person is, and the labs user that stands for them.

Two rules that matter more than the code:

* A labs user for a CommCare sign-in is found by ``identity_username`` ONLY. Never by email.
  Campaign's login merges by email so a person who first came through Connect keeps one row;
  here that would let anyone who can put an address on a CommCare account sign in as the labs
  user holding it, staff included.
* The user is created with NO email. Dimagi status is read from ``User.email``
  (``utils/dimagi_user.py``), so a CommCare-only identity is never staff, whatever address
  CommCare reports.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.utils import timezone

from connect_labs.hqdata.access import refresh_memberships
from connect_labs.hqdata.models import HQIdentity

logger = logging.getLogger(__name__)

IDENTITY_MAX_AGE = timedelta(minutes=15)
USERNAME_PREFIX = "cchq:"


class IdentityError(Exception):
    pass


def hq_base_url() -> str:
    return getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org").rstrip("/")


def hq_host() -> str:
    return urlparse(hq_base_url()).netloc


def fetch_identity(access_token: str) -> dict:
    """GET /api/v0.5/identity/ with the person's own token."""
    try:
        resp = httpx.get(
            f"{hq_base_url()}/api/v0.5/identity/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    except httpx.RequestError as exc:
        raise IdentityError(f"CommCare identity request failed: {exc}") from exc
    if resp.status_code != 200:
        raise IdentityError(f"CommCare identity returned {resp.status_code}")
    data = resp.json()
    name = " ".join(p for p in [data.get("first_name", ""), data.get("last_name", "")] if p).strip()
    return {
        "username": data.get("username") or "",
        "email": data.get("email") or "",
        "name": name,
        "domains": [d for d in (data.get("domains") or []) if isinstance(d, str)],
    }


def identity_username(host: str, hq_username: str) -> str:
    """The labs ``User.username`` for a CommCare account. Deterministic, within the 150-char limit."""
    candidate = f"{USERNAME_PREFIX}{host}:{hq_username}"
    if len(candidate) <= 150:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:24]
    return f"{USERNAME_PREFIX}{host}:{digest}"


def user_for_identity(identity: dict, *, host: str):
    """Find or create the labs user for this CommCare account. Never matches on email."""
    from connect_labs.users.models import User

    username = identity_username(host, identity["username"])
    user = User.objects.filter(username=username).first()
    name = identity.get("name") or ""
    if user is None:
        user = User(username=username, email=None, name=name)
        user.set_unusable_password()
        user.save()
    elif name and user.name != name:
        user.name = name
        user.save(update_fields=["name"])
    return user


def sync_identity(
    user,
    access_token: str,
    *,
    host: str | None = None,
    now: datetime | None = None,
    max_age: timedelta = IDENTITY_MAX_AGE,
    identity: dict | None = None,
) -> HQIdentity | None:
    """Prove the person's domains to labs, at most once per ``max_age``.

    This is the only place memberships are stamped, and it reads them off the person's own
    CommCare account. Returns the identity row, or None when CommCare could not be asked and
    there is none yet; memberships then simply keep ageing out.
    """
    host = host or hq_host()
    now = now or timezone.now()
    existing = HQIdentity.objects.filter(user=user).first()
    if identity is None and existing is not None and now - existing.refreshed_at < max_age:
        return existing
    if identity is None:
        try:
            identity = fetch_identity(access_token)
        except IdentityError as exc:
            logger.warning("hqdata identity refresh failed for %s: %s", user.pk, exc)
            return existing
    if not identity.get("username"):
        return existing
    row, _ = HQIdentity.objects.update_or_create(
        user=user,
        defaults={
            "hq_host": host,
            "hq_username": identity["username"],
            "email": identity.get("email", ""),
            "name": identity.get("name", ""),
            "domains": identity["domains"],
            "refreshed_at": now,
        },
    )
    refresh_memberships(user, identity["domains"], hq_host=host, now=now)
    return row
