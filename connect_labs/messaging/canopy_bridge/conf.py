"""The ``workers`` surface of labs' canopy host site: where labs and canopy meet for Connect Worker
conversations.

Labs is ONE canopy host site, ``connect-labs`` (``CANOPY_APP_NAME``), signed with ONE key
(``CANOPY_SIGNING_KEY``), published in ONE ``site.json`` (``/labs/canopy/site.json``). Its pages
are the site itself (staff, act-as-you grants, sensitive). Connect Worker messaging is a surface of
it, ``workers`` (``SURFACE``), declared in that same ``site.json`` (``labs.canopy.site_needs``):
not sensitive, its MCP resource the worker tools (``MCP_PATH``, bound to one subject), its people's
records ``labs-worker://``. canopy lets a Connect Worker hold a conversation only there, and sends
an agent's worker-tool calls only to that resource.

The relay, the profile push, erasure, the worker tools and the reply webhook all use this module's
``HostConfig`` (``host_config``): the site's name, key and workspace, the worker tools as its
``resource`` (the ``aud`` of every subject statement) and the reply webhook as its ``reply_url``.
It names no runner requirement: the workers surface holds no sensitive data, and canopy refuses a
relay arrival that requires sensitive-data runners there.

Everything is OFF unless ``MESSAGING_ENABLED`` and the site is configured; ``problems()`` says
what is missing.
"""

from __future__ import annotations

import functools

from django.conf import settings

#: The relay channel canopy records (``relay:<site>:personalid``).
CHANNEL = "personalid"
#: The surface of labs' host site this is (``site.json``'s ``surfaces``).
SURFACE = "workers"
#: A Connect Worker's record in labs: ``labs-worker://<username>`` (workers.models.PERSON_SCHEME).
PERSON_SCHEME = "labs-worker"
#: Paths under LABS_PUBLIC_URL (config/urls.py mounts them).
BASE_PATH = "/messaging/canopy/"
MCP_PATH = BASE_PATH + "mcp/"
SURFACE_TITLE = "Connect Worker messaging"


def _public() -> str:
    return (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")


def signing_key() -> str:
    """Labs' one site key (``CANOPY_SIGNING_KEY``), the panel's too."""
    return getattr(settings, "CANOPY_SIGNING_KEY", "") or ""


def site_name() -> str:
    """Labs' host-site name in canopy (``CANOPY_APP_NAME``, ``connect-labs``)."""
    return (getattr(settings, "CANOPY_APP_NAME", "") or "").strip()


def workspace() -> str:
    """The canopy workspace labs' site is registered in: canopy's own answer, learnt and kept at
    Labs Admin › Canopy agents (``canopy_settings.current().workspace``). "" until learnt."""
    from connect_labs.labs import canopy_settings

    return canopy_settings.current().workspace


def canopy_base_url() -> str:
    return (getattr(settings, "CANOPY_BASE_URL", "") or "").rstrip("/")


def problems() -> list[str]:
    """What stops the bridge, in words a person can act on; [] when it is on."""
    out = []
    if not getattr(settings, "MESSAGING_ENABLED", False):
        out.append("MESSAGING_ENABLED is off")
    if not signing_key():
        out.append("CANOPY_SIGNING_KEY is not set")
    if not site_name():
        out.append("CANOPY_APP_NAME is not set")
    if not workspace():
        out.append("labs has not yet learnt the canopy workspace of its site (Labs Admin › Canopy agents)")
    if not canopy_base_url():
        out.append("CANOPY_BASE_URL is not set")
    if not _public():
        out.append("LABS_PUBLIC_URL is not set")
    return out


def site_configured() -> bool:
    """The site's identity is set (its key, name, canopy, public URL): labs can sign as it, so
    an erasure can be sent even while messaging is off."""
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
    """The canopy SDK ``HostConfig`` of the workers surface of labs' site, or None when the
    site's identity is not configured. ``canopy_client_id`` is canopy's (derived from
    CANOPY_BASE_URL): the key its subject statements and reply pushes are verified with.
    ``resource`` is the worker tools (``mcp_resource``), the ``aud`` of every subject statement;
    ``reply_url`` is the reply webhook, the ``aud`` ``verify_reply`` requires, and what
    ``CANOPY_HOST["REPLY_URL"]`` and canopy's host site ``reply_webhook_url`` say."""
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
        raise RuntimeError("the workers surface of labs' canopy site is not configured: " + "; ".join(problems()))
    return config
