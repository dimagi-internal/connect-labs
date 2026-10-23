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
  on every read), built from where labs and canopy meet (``CANOPY_BASE_URL``,
  ``CANOPY_APP_NAME``, ``LABS_PUBLIC_URL``), the one secret
  (``CANOPY_SIGNING_KEY``), and everything else from Labs Admin › Canopy agents
  (``canopy_settings.current()``).

Two rules the SDK keeps and labs relies on: ``sub`` is labs' OWN id for the
signed-in user (``str(user.pk)``), never anything the browser sent; and a page's
scopes come from ``PAGE_SCOPES`` at mint time, never from the browser.

This module is imported by ``config/settings/base.py``, so it must stay free of
anything that needs the app registry at import time.
"""

from __future__ import annotations

from django.conf import settings

from connect_labs.labs import canopy_settings

#: What each scope unlocks. THE only place a delegated token's reach is decided:
#: a tool not listed under one of the token's scopes is neither listed nor
#: callable with it.
#:
#: A ``:read`` scope holds read tools only. The one write scope, ``workflow:act``,
#: holds ``workflow_run_action``, which runs a workflow's own declared action (the
#: same one its button runs) and cannot run without a preview first: the call that
#: acts must carry the single-use token its preview issued, bound to the person,
#: the run and exactly what was previewed (``workflow/actions.py``). A coaching
#: action's token never reaches the agent at all: only the View canopy renders gets
#: one, from ``workflow_action_preview_view`` (app-only, MCP Apps), previewed as the
#: person looking at it -- so a coaching send is that person's click. A new write
#: scope must hold to the same rule -- ``PREVIEWED_WRITE_SCOPES`` is the list, and a
#: test pins it.
SCOPE_TOOLS: dict[str, frozenset[str]] = {
    "marketplace:read": frozenset({"marketplace_orgs_get", "marketplace_rounds_list"}),
    "workflow:read": frozenset(
        {
            "workflow_run_context",
            "workflow_run_indicators",
            "workflow_indicator_explain",
            "workflow_run_cases",
            "workflow_action_status",
        }
    ),
    "workflow:act": frozenset({"workflow_run_action", "workflow_action_preview_view"}),
    # Change the workflow, its pipelines and its semantic registry -- by proposal only.
    # The agent proposes (workflow_edit_propose); the person saves it with the confirm
    # card's Confirm (workflow_edit_apply, app-only, carrying the token only the card
    # gets). The edit tools themselves are not here. What may be proposed, and what is
    # refused because it would send data elsewhere: mcp/tools/workflow_edit.py.
    "workflow:edit": frozenset(
        {"workflow_edit_propose", "workflow_edit_review_view", "workflow_edit_apply", "workflow_edit_decline"}
    ),
    # A page (workflow/page_mode.py): what it is built from -- its scope, the Settings it
    # reads and its workflow sources' run summaries -- read as the visitor. The tool
    # refuses a definition that is not a page, or does not share, whatever the token.
    "page:read": frozenset({"page_context"}),
    # Public open data (WorldPop, DHS, UN IGME, geoBoundaries) and arithmetic on it:
    # nothing here is specific to the visitor, so the scope adds no exposure.
    # targeting_pmc_run_model starts a compute job (IDM's EMOD on a shared on-demand box) and
    # targeting_pmc_run_status polls it. They are read-scope on purpose: they take public inputs,
    # write no user data (a run is cached by the hash of its inputs and shared by everyone who asks
    # the same question), and mutate nothing the visitor owns.
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
            "targeting_pmc_schedules",
            "targeting_pmc_rank",
            "targeting_pmc_states",
            "targeting_pmc_run_model",
            "targeting_pmc_run_status",
        }
    ),
    # A tender and its quotes, read as the visitor through the supply operations, which
    # check program membership on every call. Reads only: the panel analyses what the
    # tables show (ruling 2026-10-07: the product shows the data, the AI judges it on
    # request); every change stays the person's own, made in the tables.
    # Labs data (connect_labs/labsdata), the labsdata agent's scope: SQL over the
    # visits of the opportunities on screen, whole visits down to their form JSON,
    # and the definitions behind a workflow (its pipelines, their SQL, the semantic
    # registry). Read-only throughout. The data_* tools decide for themselves what an
    # agent sees (labsdata/service.py:agent_sees_real_rows): real rows only for an
    # agent labs has cleared for real data (labs/canopy_panel.py); otherwise
    # structure, validation over zero rows and synthetic data, and the agent hands
    # its SQL to the person on the Data page (the `data_set_query` page action).
    "data:read": frozenset(
        {
            "data_describe",
            "data_validate",
            "data_query",
            "data_visits",
            "data_workflow_context",
            "workflow_get",
            "pipeline_get",
            "pipeline_sql",
            "semantic_registry_list",
            "semantic_registry_get",
            "semantic_registry_explain",
        }
    ),
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

#: Scopes whose tools show the agent data computed from visits. Over real
#: (non-generated) data they run only for an agent labs has cleared
#: (``labs/canopy_panel.py``, ``mcp/visit_access.py:delegated_denied_reason``); a
#: test pins that every one of their tools says what it reads.
VISIT_SCOPES: frozenset[str] = frozenset({"workflow:read", "workflow:act", "page:read"})

#: Write scopes whose every tool acts only on a confirmed preview.
PREVIEWED_WRITE_SCOPES: frozenset[str] = frozenset({"workflow:act", "workflow:edit"})

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
    # data:read so the agent can check a run against the raw data behind it
    # (data_workflow_context, data_query, data_visits) -- real rows only for a cleared agent.
    "labs:workflow:run": ("workflow:read", "workflow:act", "data:read"),
    # A run whose workflow offers the DATA agent (``config.agent.agent == "data"``): the
    # run's reads, the raw data behind it, and edits to the workflow confirmed in chat --
    # never ``workflow:act`` (``RUN_FOR_DATA_AGENT``).
    "labs:workflow:run:data": ("workflow:read", "data:read", "workflow:edit"),
    # A page drawn at its workflow URL (or any route registered with more than a page
    # needs): its panel names this key instead of the route (``PAGE_ON_ANOTHER_ROUTE``),
    # so a page's agent reads the page and nothing a workflow's run would reach.
    "labs:workflow:run:page": ("page:read",),
    "labs:page_home": ("page:read",),
    "labs:page": ("page:read",),
    "supply_chain:workflow_view": ("page:read",),
    "targeting:index": ("targeting:read",),
    "targeting:pmc": ("targeting:read",),
    "labsdata:index": ("data:read",),
    "supply_chain:procurement_tender_detail": ("supply:read",),
    "supply_chain:procurement_comparison": ("supply:read",),
}

#: What each page is, for people reading labs' ``site.json`` in canopy (Connected sites).
PAGE_TITLES: dict[str, str] = {
    "marketplace:network": "The partner network",
    "marketplace:round": "A marketplace round",
    "labs:workflow:run": "A workflow run",
    "labs:workflow:run:data": (
        "A workflow run, read-only (an agent that handles sensitive data, on sensitive-data runners)"
    ),
    "labs:workflow:run:page": "A page drawn at its workflow's address",
    "labs:page_home": "An organisation's or programme's home page",
    "labs:page": "A page",
    "supply_chain:workflow_view": "A supply page",
    "targeting:index": "Targeting",
    "targeting:pmc": "Targeting: malaria PMC",
    "labsdata:index": "Data",
    "supply_chain:procurement_tender_detail": "A tender",
    "supply_chain:procurement_comparison": "A tender's quote comparison",
}

#: The page key a page's panel uses on a route whose own registration grants more
#: than ``page:read`` (the workflow run URL). Not a URL name: no route resolves to it.
PAGE_ON_ANOTHER_ROUTE = "labs:workflow:run:page"
PAGE_ONLY_SCOPES = ("page:read",)

#: The page key a run page's panel uses when its workflow offers the data agent.
#: Not a URL name either: the run route's own registration includes ``workflow:act``.
RUN_FOR_DATA_AGENT = "labs:workflow:run:data"

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


def site_needs() -> dict:
    """What labs asks of canopy's agents, for ``site.json``: to read real data an agent
    must be declared to handle sensitive data and run on sensitive-data runners; and which
    agents labs shows and lets read real data now, against which site agents version they
    were chosen."""
    agents = canopy_settings.current()
    return {
        "real_data": {"requires": {"handles_sensitive": True, "runner_flag": canopy_settings.SENSITIVE_DATA}},
        "uses": {
            "agents": list(agents.offered),
            "real_data_agents": list(agents.real_data_agents),
            "version": agents.site_agents_version,
        },
    }


def host_settings() -> dict:
    """``CANOPY_HOST`` as the SDK reads it, from labs' settings as they are NOW.

    ``settings.CANOPY_HOST`` is this function, and the SDK calls it on every
    read rather than freezing it at settings import: ``LABS_PUBLIC_URL`` is
    overridden per environment AFTER ``base.py`` (local, test, labs_aws), and
    the agent settings change when staff save at Labs Admin › Canopy agents. A
    callable (not a ``Mapping``) also keeps the signing key off Django's debug
    page, which shows a callable by name. The grant needs a public
    origin — every URL it names is absolute — so without one it is off.

    ``AGENT_SLUG`` is the saved default agent and nothing else ("" until someone
    saves); ``WORKSPACE`` the tenant canopy named for labs' site ("" until learnt,
    when ``AGENT_SLUG`` names it). ``CLIENT_ID`` turns the jwt-bearer grant on: it is canopy's client
    id, derived from ``CANOPY_BASE_URL``, while the saved settings say canopy
    may act as the visitor, and "" (the grant off) otherwise.
    """
    public = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    base = getattr(settings, "CANOPY_BASE_URL", "") or ""
    agents = canopy_settings.current()  # Labs Admin › Canopy agents
    config = {
        "SIGNING_KEY": getattr(settings, "CANOPY_SIGNING_KEY", ""),
        "CANOPY_BASE_URL": base,
        "APP_NAME": getattr(settings, "CANOPY_APP_NAME", ""),
        "AGENT_SLUG": agents.default_agent,
        # The tenant labs' site is registered in (dimagi-canopy 0.16). While it is not
        # yet known, AGENT_SLUG names it.
        "WORKSPACE": agents.workspace,
        "CLIENT_ID": canopy_client_id() if agents.acts_as_visitor else "",
        "ISSUER": public,
        "RESOURCE": f"{public}/mcp/" if public else "",
        "TOKEN_ENDPOINT": f"{public}/o/token/" if public else "",
        "RUNNER_REQUIREMENTS": list(agents.runner_requirements),
        "SCOPE_TOOLS": SCOPE_TOOLS,
        "PAGE_SCOPES": PAGE_SCOPES,
        "PAGE_TITLES": PAGE_TITLES,
        # site.json: what labs asks of canopy's agents (spec 2026-10-22-canopy-site-agents-api-design §2).
        "SITE_NEEDS": site_needs,
        "USER_CLAIMS": user_claims,
        # Reversed by the SDK per request, so it follows labs' URLconf.
        "PANEL_TOKEN_URL_NAME": "labs:canopy_token",
        "PANEL": PANEL,
    }
    if public:
        # site.json's ``site`` block: labs' key is at its own long-standing URL, not the
        # SDK's default route (the origins default to ISSUER's).
        config["JWKS_URL"] = f"{public}/labs/canopy/jwks/"
    if public:
        # Configured wherever the grant can be. The SDK runs the probe only on
        # top of a working grant (CLIENT_ID set), and ``probe_subject``
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


def canopy_client_id() -> str:
    """canopy's OAuth client id: the URL of its client metadata document, under
    ``CANOPY_BASE_URL`` (``https://canopy.dimagi.com/oauth/client.json``). "" without
    a canopy."""
    from canopy_sdk.contract import client_id_for

    base = getattr(settings, "CANOPY_BASE_URL", "") or ""
    return client_id_for(base) if base else ""


def grant_on() -> bool:
    """Whether canopy may act as the visitor here: the saved switch, over a canopy."""
    return bool(canopy_settings.current().acts_as_visitor and canopy_client_id())


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
