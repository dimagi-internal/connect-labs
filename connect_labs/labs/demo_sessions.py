"""Sign a demo persona in on a LOCAL labs build, for the DDD inner loop's recorder.

Labs signs people in through Connect OAuth, which a locally served build cannot
complete for a demo persona (Sophie has no Connect account) or for a recorder
with no browser of its own. On labs itself the walkthrough seeders mint the
session a real sign-in would produce, server-side, in `manage.py shell`
(`scripts/walkthroughs/supply-sophie-unanswered-round/replay.py:mint_sophie_session`).
This is the same mint for a build on this machine, packaged as the Playwright
storage state the recorder loads (`auth.type: storage_state`).

It is LOCAL ONLY, and that is checked here rather than trusted to the caller:

- the settings module must be `config.settings.local` AND `DEBUG` must be on,
  so `config.settings.labs_aws` can never pass, even with `DJANGO_DEBUG=True`
  set on a task (`tests/test_demo_sessions.py` pins both);
- the cookie is only ever written for a loopback host, so the file this makes
  cannot be replayed against a deployed origin.
"""

from __future__ import annotations

import time
from importlib import import_module
from urllib.parse import urlparse

from django.conf import settings

LOCAL_SETTINGS_MODULES = frozenset({"config.settings.local"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class LocalOnly(RuntimeError):
    """Refused: this is not a local, DEBUG build."""


def allowed(debug: bool, settings_module: str | None) -> bool:
    """True only for a DEBUG build running the local settings module."""
    return bool(debug) and (settings_module or "") in LOCAL_SETTINGS_MODULES


def require_local() -> None:
    module = getattr(settings, "SETTINGS_MODULE", None)
    if not allowed(settings.DEBUG, module):
        raise LocalOnly(
            f"demo sessions are minted only on a local DEBUG build (settings={module!r}, DEBUG={settings.DEBUG})"
        )


def mint_session(user, *, hours: int = 12) -> dict:
    """The session a real sign-in would produce for `user`: the same keys the labs seeders mint."""
    require_local()
    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store["_auth_user_hash"] = user.get_session_auth_hash()
    store["labs_oauth"] = {
        "access_token": "demo-persona-no-connect-account",
        "refresh_token": "",
        "expires_at": time.time() + hours * 3600,
        "organization_data": {"organizations": [], "programs": [], "opportunities": []},
    }
    store.set_expiry(hours * 3600)
    store.create()
    return {"key": store.session_key, "expires": int(time.time() + hours * 3600)}


def storage_state(user, base_url: str, *, hours: int = 12) -> dict:
    """A Playwright storage state that is `user`, signed in, on the local build at `base_url`."""
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host not in LOOPBACK_HOSTS:
        raise LocalOnly(f"a demo session cookie is only written for a loopback host, not {host!r}")
    session = mint_session(user, hours=hours)
    return {
        "cookies": [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session["key"],
                "domain": host,
                "path": "/",
                "expires": session["expires"],
                "httpOnly": True,
                "secure": parsed.scheme == "https",
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
