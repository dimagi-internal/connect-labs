"""The ``connect-messaging`` host site: where labs and canopy meet for Connect Worker conversations.

A second canopy host-site identity, separate from ``connect-labs`` (the agent panel's): its own
name, its own Ed25519 key (``CANOPY_MESSAGING_SIGNING_KEY``, never the panel's), its own
``site.json`` and JWKS, its own MCP resource (the worker tools, bound to one subject) and its own
reply webhook. Separate because its people are different: the panel's visitors are labs users;
this site's are Connect Workers, for whom labs is the system of record (``person_scheme:
labs-worker``).

Everything is OFF unless ``MESSAGING_ENABLED`` and every setting here is set; ``problems()`` says
which is missing.
"""

from __future__ import annotations

import functools

from django.conf import settings

#: The relay channel canopy records (``relay:<site>:personalid``).
CHANNEL = "personalid"
#: A Connect Worker's record in labs: ``labs-worker://<username>`` (workers.models.PERSON_SCHEME).
PERSON_SCHEME = "labs-worker"
#: Paths under LABS_PUBLIC_URL (config/urls.py mounts them).
BASE_PATH = "/messaging/canopy/"
MCP_PATH = BASE_PATH + "mcp/"
JWKS_PATH = BASE_PATH + "jwks.json"
SITE_PATH = BASE_PATH + "site.json"
SITE_TITLE = "Connect Worker messaging"


def _public() -> str:
    return (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")


def signing_key() -> str:
    return getattr(settings, "CANOPY_MESSAGING_SIGNING_KEY", "") or ""


def site_name() -> str:
    return (getattr(settings, "CANOPY_MESSAGING_SITE_NAME", "") or "").strip()


def workspace() -> str:
    return (getattr(settings, "CANOPY_MESSAGING_WORKSPACE", "") or "").strip()


def canopy_base_url() -> str:
    return (getattr(settings, "CANOPY_BASE_URL", "") or "").rstrip("/")


def problems() -> list[str]:
    """What stops the bridge, in words a person can act on; [] when it is on."""
    out = []
    if not getattr(settings, "MESSAGING_ENABLED", False):
        out.append("MESSAGING_ENABLED is off")
    if not signing_key():
        out.append("CANOPY_MESSAGING_SIGNING_KEY is not set")
    if not site_name():
        out.append("CANOPY_MESSAGING_SITE_NAME is not set")
    if not workspace():
        out.append("CANOPY_MESSAGING_WORKSPACE is not set")
    if not canopy_base_url():
        out.append("CANOPY_BASE_URL is not set")
    if not _public():
        out.append("LABS_PUBLIC_URL is not set")
    return out


def site_configured() -> bool:
    """The site's identity is set (its key, name, canopy, public URL): site.json and the JWKS
    can be served, so canopy can register the site before MESSAGING_ENABLED is turned on."""
    return bool(signing_key() and site_name() and canopy_base_url() and _public())


def enabled() -> bool:
    """The bridge is on: messaging is on and the site is fully configured."""
    return not problems()


def mcp_resource() -> str:
    return f"{_public()}{MCP_PATH}" if _public() else ""


def reply_webhook_url() -> str:
    path = getattr(settings, "CANOPY_MESSAGING_REPLY_WEBHOOK_PATH", "") or ""
    return f"{_public()}{path}" if _public() and path else ""


def webhook_on() -> bool:
    return bool(getattr(settings, "CANOPY_MESSAGING_REPLY_WEBHOOK", False))


def jwks_url() -> str:
    return f"{_public()}{JWKS_PATH}" if _public() else ""


def retention_days() -> int | None:
    """``CANOPY_MESSAGING_RETENTION_DAYS``: how long canopy keeps these workers' conversations,
    declared as ``site.retention_days`` (the shorter of it and the workspace's own rule wins).
    None (the default) leaves it to canopy's rules."""
    value = getattr(settings, "CANOPY_MESSAGING_RETENTION_DAYS", None)
    if value in (None, ""):
        return None
    return int(value)


def turn_timeout_seconds() -> int:
    return int(getattr(settings, "CANOPY_MESSAGING_TURN_TIMEOUT_SECONDS", 600) or 600)


@functools.lru_cache(maxsize=4)
def _config(key: str, base: str, name: str, ws: str, public: str, reply_url: str):
    from canopy_sdk.contract import client_id_for
    from canopy_sdk.host import HostConfig

    return HostConfig(
        signing_key=key,
        canopy_base_url=base,
        app_name=name,
        workspace=ws,
        canopy_client_id=client_id_for(base),
        issuer=public,
        resource=f"{public}{MCP_PATH}",
        reply_url=reply_url,
    )


def host_config():
    """The canopy SDK ``HostConfig`` of the ``connect-messaging`` site, or None when its identity
    is not configured. ``canopy_client_id`` is canopy's (derived from CANOPY_BASE_URL): the key
    its subject statements and reply pushes are verified with. ``reply_url`` is the reply
    webhook's absolute URL, the ``aud`` ``verify_reply`` requires: register exactly this as the
    host site's ``reply_webhook_url`` in canopy.

    This is the messaging site's equivalent of ``CANOPY_HOST`` (which is the panel's
    ``connect-labs`` site): ``PERSON_SCHEME`` is ``PERSON_SCHEME`` here, ``REPLY_URL`` is
    ``reply_webhook_url()`` and ``RETENTION_DAYS`` is ``retention_days()``."""
    if not site_configured():
        return None
    return _config(signing_key(), canopy_base_url(), site_name(), workspace(), _public(), reply_webhook_url())


def client_keys():
    """canopy's keys, read from its client metadata and cached in Django's cache (shared by every
    worker): what subject statements and reply pushes are verified against. Tests replace it."""
    from canopy_sdk.host.client_keys import ClientKeyResolver
    from django.core.cache import cache

    return ClientKeyResolver(cache=cache)


def replay_store():
    """Where subject statements and reply pushes are spent: labs' database table
    (``canopy_sdk.django.stores.DjangoJtiStore``, what the SDK's Django wrappers use), so a replay
    to another worker is refused too. The SDK's default store is per process: never on a
    multi-worker host."""
    from canopy_sdk.django.stores import DjangoJtiStore

    return DjangoJtiStore()


def require_config():
    config = host_config()
    if config is None:
        raise RuntimeError("the connect-messaging canopy site is not configured: " + "; ".join(problems()))
    return config
