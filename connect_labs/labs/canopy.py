"""Labs as a canopy HOST: the parts that are genuinely labs'.

The protocol itself — signing the visitor assertion and the ID-JAG, the
server-signed page token, the jwt-bearer grant at ``/o/token/``, the DPoP gate
in front of ``/mcp/``, the delegated-token and used-``jti`` tables — is the
canopy SDK's (``dimagi-canopy``, import ``canopy_sdk``; canopy-web
``sdk/python``). One implementation of host grant contract v1, shared with
canopy itself, so the two ends cannot drift. What stays here is what only labs
can decide:

* ``PAGE_SCOPES`` — which labs pages may let canopy act as the visitor, and with
  which scopes;
* ``SCOPE_TOOLS`` — what each scope unlocks at labs' MCP;
* ``user_claims`` — what labs vouches for about its signed-in user;
* ``CanopyHost`` — ``settings.CANOPY_HOST``, built from labs' existing
  ``CANOPY_*`` settings and ``LABS_PUBLIC_URL``, so no environment variable was
  renamed and the task definitions did not change.

Two rules the SDK keeps and labs relies on: ``sub`` is labs' OWN id for the
signed-in user (``str(user.pk)``), never anything the browser sent; and a page's
scopes come from ``PAGE_SCOPES`` at mint time, never from the browser.

This module is imported by ``config/settings/base.py``, so it must stay free of
anything that needs the app registry at import time.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.conf import settings

#: What each scope unlocks. THE only place a delegated token's reach is decided:
#: a tool not listed under one of the token's scopes is neither listed nor
#: callable with it. v1 is read-only on purpose — a write reached through a page
#: should come with a write scope AND a per-call confirmation from the visitor
#: (the design's decision 3), neither of which exists yet.
SCOPE_TOOLS: dict[str, frozenset[str]] = {
    "marketplace:read": frozenset({"marketplace_orgs_get", "marketplace_rounds_list"}),
}

#: Which pages may let canopy act as the visitor, and with which scopes, keyed by
#: URL name. A page's scopes are decided here, server-side, and never from
#: anything the browser sends. A page not listed gets no grant at all — the panel
#: still opens, and the agent works as it did before. Every scope here must be a
#: key of ``SCOPE_TOOLS`` (the SDK refuses to build its page registry otherwise).
PAGE_SCOPES: dict[str, tuple[str, ...]] = {
    "marketplace:network": ("marketplace:read",),
    "marketplace:round": ("marketplace:read",),
}

#: The panel's look on labs' pages. Rendered by the SDK's ``canopy_host/panel.html``.
PANEL = {
    "mode": "overlay",
    "launcher_label": "Ask an agent",
    "theme": {
        "mode": "light",
        "accent": "#3F4FA0",
        "font": "system-ui, -apple-system, 'Segoe UI', sans-serif",
    },
}


def user_claims(user) -> dict:
    """What labs vouches for about ``user`` in the visitor assertion.

    ``get_display_name``, not ``get_full_name``: labs' User replaces
    first_name/last_name with a single ``name``, so the inherited accessor reads
    two fields that are None here.

    ``email_verified``: the address came from Connect's OAuth identity, which is
    how this person signed in to labs — so vouching for it is vouching for our
    own signed-in user. Canopy uses it to let a member of the site's workspace
    arrive as their own canopy account instead of as a contact; for anyone else
    it changes nothing. Never claimed for an empty address (the SDK enforces
    that too).
    """
    return {
        "name": user.get_display_name(),
        "email": user.email or "",
        "email_verified": bool(user.email),
    }


def _panel_token_url() -> str:
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse("labs:canopy_token")
    except NoReverseMatch:
        return ""


def host_settings() -> dict:
    """``CANOPY_HOST`` as the SDK reads it, from labs' settings as they are NOW.

    Read on every call rather than frozen at settings import: ``LABS_PUBLIC_URL``
    is overridden per environment AFTER ``base.py`` (local, test, labs_aws), and
    tests override the individual ``CANOPY_*`` settings. The grant needs a public
    origin — every URL it names is absolute — so without one it is off, exactly
    as before.
    """
    public = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return {
        "SIGNING_KEY": getattr(settings, "CANOPY_SIGNING_KEY", ""),
        "CANOPY_BASE_URL": getattr(settings, "CANOPY_BASE_URL", ""),
        "APP_NAME": getattr(settings, "CANOPY_APP_NAME", ""),
        "AGENT_SLUG": getattr(settings, "CANOPY_AGENT_SLUG", ""),
        "CLIENT_ID": getattr(settings, "CANOPY_CLIENT_ID", ""),
        "ISSUER": public,
        "RESOURCE": f"{public}/mcp/" if public else "",
        "TOKEN_ENDPOINT": f"{public}/o/token/" if public else "",
        "SCOPE_TOOLS": SCOPE_TOOLS,
        "PAGE_SCOPES": PAGE_SCOPES,
        "USER_CLAIMS": user_claims,
        "PANEL_TOKEN_URL": _panel_token_url(),
        "PANEL": PANEL,
    }


class CanopyHost(Mapping):
    """``settings.CANOPY_HOST``: a live view of ``host_settings()``.

    Its repr never shows the values — the signing key is one of them, and a
    Mapping is not a dict, so Django's debug-page cleansing would not reach it.
    """

    def __getitem__(self, key):
        return host_settings()[key]

    def __iter__(self):
        return iter(host_settings())

    def __len__(self):
        return len(host_settings())

    def __repr__(self) -> str:
        return "<CanopyHost: built from the CANOPY_* settings and LABS_PUBLIC_URL>"


def host_config():
    """The SDK's ``HostConfig`` for labs, or ``None`` when labs has no signing key.

    For callers that must degrade rather than fail — the discovery documents and
    the MCP token verifier — where "not configured" means "offer no grant".
    """
    import logging

    from canopy_sdk.django import conf
    from canopy_sdk.host import HostNotConfigured

    try:
        return conf.get_host_config()
    except HostNotConfigured:
        return None
    except Exception:  # noqa: BLE001 - a malformed key is a deployment fault, not a 500 on discovery
        logging.getLogger(__name__).exception("CANOPY_SIGNING_KEY could not be read")
        return None


def allowed_tools(scopes) -> frozenset[str]:
    """The tools a delegated token with ``scopes`` may reach."""
    from canopy_sdk.contract import tools_for_scopes

    return frozenset(tools_for_scopes(scopes, SCOPE_TOOLS))
