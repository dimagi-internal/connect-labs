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
* ``PROBE_*`` — canopy's LIVE PROBE: a dedicated, low-privilege principal
  (``PROBE_USERNAME``, created by ``mcp/migrations/0006``) canopy may ask a
  real ID-JAG for, so it can walk the whole grant chain on a schedule with no
  visitor at the keyboard;
* ``host_settings`` — ``settings.CANOPY_HOST`` (the SDK resolves a callable
  on every read), built from labs' existing ``CANOPY_*`` settings and
  ``LABS_PUBLIC_URL``, so no environment variable was renamed and the task
  definitions did not change.

Two rules the SDK keeps and labs relies on: ``sub`` is labs' OWN id for the
signed-in user (``str(user.pk)``), never anything the browser sent; and a page's
scopes come from ``PAGE_SCOPES`` at mint time, never from the browser.

This module is imported by ``config/settings/base.py``, so it must stay free of
anything that needs the app registry at import time.
"""

from __future__ import annotations

from django.conf import settings

#: What each scope unlocks. THE only place a delegated token's reach is decided:
#: a tool not listed under one of the token's scopes is neither listed nor
#: callable with it.
#:
#: A ``:read`` scope holds read tools only. The one write scope, ``workflow:act``,
#: holds ``workflow_run_action``, which runs a workflow's own declared action (the
#: same one its button runs) and cannot run without a preview first: the call that
#: acts must carry the single-use token its preview issued, bound to the person,
#: the run and exactly what was previewed (``workflow/actions.py``). The agent is
#: told to show that preview and get the person's yes in between. A new write
#: scope must hold to the same rule -- ``PREVIEWED_WRITE_SCOPES`` is the list, and a
#: test pins it.
SCOPE_TOOLS: dict[str, frozenset[str]] = {
    "marketplace:read": frozenset({"marketplace_orgs_get", "marketplace_rounds_list"}),
    "workflow:read": frozenset(
        {
            "workflow_run_context",
            "workflow_run_indicators",
            "workflow_indicator_explain",
            "workflow_action_status",
        }
    ),
    "workflow:act": frozenset({"workflow_run_action"}),
    # Public open data (WorldPop, DHS, UN IGME, geoBoundaries) and arithmetic on it:
    # nothing here is specific to the visitor, so the scope adds no exposure.
    "targeting:read": frozenset(
        {
            "targeting_indicators",
            "targeting_select",
            "targeting_methodology",
            "targeting_scenario",
            "targeting_admin_levels",
            "targeting_research",
            "targeting_compare_criteria",
            "targeting_cost_effectiveness",
        }
    ),
    # A tender and its quotes, read as the visitor through the supply operations, which
    # check program membership on every call. Reads only: the panel analyses what the
    # tables show (ruling 2026-10-07: the product shows the data, the AI judges it on
    # request); every change stays the person's own, made in the tables.
    "supply:read": frozenset(
        {
            "supply_chain_tender_get",
            "supply_chain_tender_compare",
            "supply_chain_tender_outstanding_questions",
            "supply_chain_quote_list",
            "supply_chain_quote_get",
            "supply_chain_outreach_list",
            "supply_chain_award_list",
            "supply_chain_supplier_get",
            "supply_chain_commitment_list",
        }
    ),
}

#: Write scopes whose every tool acts only on a confirmed preview.
PREVIEWED_WRITE_SCOPES: frozenset[str] = frozenset({"workflow:act"})

#: Which pages may let canopy act as the visitor, and with which scopes, keyed by
#: URL name. A page's scopes are decided here, server-side, and never from
#: anything the browser sends. A page not listed gets no grant at all — the panel
#: still opens, and the agent works as it did before. Every scope here must be a
#: key of ``SCOPE_TOOLS`` (the SDK refuses to build its page registry otherwise).
#:
#: The workflow run page is registered for every workflow, but a workflow that has
#: not opted in (``config.agent.share``) renders no panel, so no page token and no
#: grant is ever issued for it -- and every ``workflow_*`` run tool refuses a
#: delegated call on a workflow that has not opted in, whatever the token says.
PAGE_SCOPES: dict[str, tuple[str, ...]] = {
    "marketplace:network": ("marketplace:read",),
    "marketplace:round": ("marketplace:read",),
    "labs:workflow:run": ("workflow:read", "workflow:act"),
    "targeting:index": ("targeting:read",),
    "supply_chain:procurement_tender_detail": ("supply:read",),
    "supply_chain:procurement_comparison": ("supply:read",),
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


#: canopy's live probe (SDK 0.4.0, ``canopy_sdk.host.ProbeHandler``). canopy
#: asks ``/labs/canopy/probe/`` — authenticated as its client with
#: ``private_key_jwt`` + DPoP, exactly as at ``/o/token/`` — for a real ID-JAG,
#: redeems it through the normal jwt-bearer grant, and makes real MCP calls
#: with the token. The principal is fixed here, never chosen by the request.
#:
#: The username has a ``:`` on purpose: a Connect username cannot (Django's
#: ``UnicodeUsernameValidator``), so no Connect login can resolve to this row —
#: and the OAuth callback refuses the name outright as well
#: (``is_probe_username``). The account has no usable password, no staff bit and
#: no PAT: the only thing that can act as it is a probe grant.
PROBE_USERNAME = "canopy:probe"
PROBE_DISPLAY_NAME = "canopy live probe (service account)"
#: One READ-ONLY scope and the one call canopy makes with it. The call must
#: SUCCEED as the probe user: ``marketplace_rounds_list`` has no per-user gate
#: (every labs user reads the same directory), and ``open_only`` keeps the answer
#: to the rounds currently taking submissions — an empty list is still a success.
PROBE_SCOPE = "marketplace:read"
PROBE_TOOL = "marketplace_rounds_list"
PROBE_ARGUMENTS = {"open_only": True}
#: A REAL tool outside ``PROBE_SCOPE`` that labs' MCP must neither list nor run
#: for a probe token. A read tool, so a regression that let it through would
#: still write nothing.
PROBE_DENIED_TOOL = "list_templates"
#: The page the probe stands in for (audit only).
PROBE_PAGE = "marketplace:network"


def is_probe_username(username) -> bool:
    """True for the probe principal's username: nobody may sign in as it."""
    return (username or "").strip().lower() == PROBE_USERNAME


def probe_subject() -> str:
    """The probe principal's subject (``str(pk)``), or ``""`` — which turns the
    probe OFF — while the row does not exist here.

    Deliberately not filtered on ``is_active``: a deactivated probe user should
    show up in canopy as a REFUSED probe (the SDK checks ``SUBJECT_ACTIVE``), not
    as a site that never configured one.

    The SDK resolves this whenever it builds the host config, and one of those
    callers is the MCP's DPoP gate, which runs on the event loop and never needs
    the probe — so the ORM refusing to run there (``SynchronousOnlyOperation``),
    or a database that cannot answer, means "no probe for this read", never an
    error.
    """
    from connect_labs.users.models import User

    try:
        pk = User.objects.filter(username=PROBE_USERNAME).values_list("pk", flat=True).first()
    except Exception:  # noqa: BLE001 - see above: off for this read, never a failure
        return ""
    return str(pk) if pk is not None else ""


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


def host_settings() -> dict:
    """``CANOPY_HOST`` as the SDK reads it, from labs' settings as they are NOW.

    ``settings.CANOPY_HOST`` is this function, and the SDK calls it on every
    read rather than freezing it at settings import: ``LABS_PUBLIC_URL`` is
    overridden per environment AFTER ``base.py`` (local, test, labs_aws), and
    tests override the individual ``CANOPY_*`` settings. A callable (not a
    ``Mapping``) also keeps the signing key off Django's debug page, which shows
    a callable by name. The grant needs a public
    origin — every URL it names is absolute — so without one it is off, exactly
    as before.
    """
    public = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    config = {
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
        # Reversed by the SDK per request, so it follows labs' URLconf.
        "PANEL_TOKEN_URL_NAME": "labs:canopy_token",
        "PANEL": PANEL,
    }
    if public:
        # Configured wherever the grant can be. The SDK runs the probe only on
        # top of a working grant (CANOPY_CLIENT_ID set), and ``probe_subject``
        # keeps it off until the probe user exists. ENDPOINT must be the public
        # URL exactly: a DPoP proof's ``htu`` is compared to it.
        config["PROBE"] = {
            "ENDPOINT": f"{public}/labs/canopy/probe/",
            "SUBJECT_RESOLVER": probe_subject,
            "SCOPE": PROBE_SCOPE,
            "TOOL": PROBE_TOOL,
            "ARGUMENTS": PROBE_ARGUMENTS,
            "DENIED_TOOL": PROBE_DENIED_TOOL,
            "PAGE": PROBE_PAGE,
        }
    return config


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
