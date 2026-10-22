"""The one door to the canopy agent panel on a labs page: which data the page would
show the agent, and whether an agent may be offered over it at all.

Every labs view that shows the panel calls ``panel(request, reads=..., ...)`` here and
never the SDK's ``panel_context`` directly (a test pins that). A page states what the
agent could read through it:

* ``NO_VISIT_DATA`` -- the marketplace directory, targeting's open data, a tender, the
  explorer (whose agent writes SQL and never sees a row). Offered as before.
* ``visits(opportunity_ids)`` -- a workflow run, or a page whose workflow sources carry
  run summaries computed from visits. ``None`` means "cannot tell", which counts as
  real data.

**The rule.** Over visit data that is not all GENERATED synthetic data
(``labs/synthetic/provenance.py``) -- production data, with PII and PHI in it -- the
panel is not drawn at all, unless labs has cleared the agent for real data
(``real_data_clearance``). No panel means no page token, no grant and no ID-JAG, so the
agent cannot reach the page's tools as the visitor by any path. The MCP server holds
canopy's calls to the same rule independently (``mcp/server.py:policy_denial``), so a
token minted before a workflow's data class changed is refused too.

**Clearance** needs all three, and fails closed on each:

1. labs allows the agent it offers on real data: it is in labs' ``phi_agents`` (empty
   by default). The agent's author claims it is PHI-safe; labs decides whether to
   accept that claim.
2. every visitor conversation must run on ZDR runners: ``zdr`` is in the conversation's
   runner requirements, which the SDK signs into every visitor assertion and canopy
   enforces when a runner claims the turn.

Labs' side of both is set at Labs Admin › Canopy agents (``labs/canopy_settings.py``;
the ``CANOPY_*`` environment settings are only its defaults).
3. canopy vouches, on each call, for the agent's PHI-safe declaration and the runner
   that is executing it, in a ``Canopy-Execution`` statement labs verifies
   (``mcp/execution.py``; canopy-web spec ``2026-10-22-phi-agents-on-zdr-runners`` §6).

The first two, plus an SDK that can verify statements (``CANOPY_EXECUTION_STATEMENTS``),
decide whether a PAGE may offer the agent (``real_data_clearance``). Each CALL is then
held to its own verified statement as well (``call_real_data_clearance``): the agent it
names is allow-listed, its author declared the capability PHI-safe at exactly the commit
that is executing, the runner carries ``zdr`` in force and reports ZDR mode, and the
conversation requires ``zdr``. No statement, or one that does not verify, is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs import canopy_settings


def _execution_statements_supported() -> bool:
    from connect_labs.mcp import execution

    return execution.supported()


#: Whether labs can verify canopy's per-call ``Canopy-Execution`` statement (the SDK
#: has ``verify_execution``, dimagi-canopy >= 0.13). Without it no agent is ever
#: cleared for real data. With it, a page may offer a cleared agent, and every call is
#: still held to its own verified statement (``call_real_data_clearance``).
CANOPY_EXECUTION_STATEMENTS = _execution_statements_supported()

#: The runner flag every conversation over real data must require.
ZDR = "zdr"

#: Where labs' side of every refusal is fixed.
ADMIN_PAGE = "Labs Admin › Canopy agents"


@dataclass(frozen=True)
class Reads:
    """What the agent could read through a page: no visit data, or visits of these opps.

    ``opportunity_ids is None`` with ``visit_data`` means the page cannot tell which
    opportunities it reads, which is treated as real data.
    """

    visit_data: bool
    opportunity_ids: tuple[int, ...] | None = None


NO_VISIT_DATA = Reads(visit_data=False)


def visits(opportunity_ids) -> Reads:
    if opportunity_ids is None:
        return Reads(visit_data=True, opportunity_ids=None)
    try:
        ids = tuple(sorted({int(o) for o in opportunity_ids}))
    except (TypeError, ValueError):
        return Reads(visit_data=True, opportunity_ids=None)
    return Reads(visit_data=True, opportunity_ids=ids)


@dataclass(frozen=True)
class Clearance:
    granted: bool
    reason: str = ""


def real_data_clearance(agent: str = "", runner_requirements=()) -> Clearance:
    """Whether labs may let ``agent`` (default: the site's) work over real (non-generated) data.

    ``runner_requirements`` are the page's own, which add to the site's (canopy SDK
    0.11: a page can raise the floor, never lower it).
    """
    configured = canopy_settings.current()
    agent = agent or configured.site_agent
    if agent not in set(configured.phi_agents):
        return Clearance(
            False,
            f'{agent} is not allowed over real data: add it to "Agents allowed over real data" '
            f"in {ADMIN_PAGE} (CANOPY_PHI_AGENTS)",
        )
    required = set(configured.runner_requirements) | set(runner_requirements or ())
    if ZDR not in required:
        return Clearance(
            False,
            f"conversations with {agent} here are not held to ZDR (zero-data-retention) runners: "
            f"offer it read-only, or tick zdr, in {ADMIN_PAGE} (CANOPY_RUNNER_REQUIREMENTS)",
        )
    if not CANOPY_EXECUTION_STATEMENTS:
        return Clearance(False, "canopy does not yet vouch for the agent's declaration and its runner on each call")
    return Clearance(True)


def call_real_data_clearance() -> Clearance:
    """Whether THIS call (canopy acting for a visitor) may be given real data.

    Labs' own conditions for the agent canopy's verified statement names, then the
    statement's: PHI-safe declaration at the executing commit, ``zdr`` in force on a
    runner in ZDR mode, a conversation that requires it (``Execution.phi_clearance``).
    """
    from connect_labs.mcp import execution

    call = execution.current()
    if call is None or call.execution is None:
        why = (call.refused if call else "") or "no statement"
        return Clearance(False, f"canopy did not vouch for this call ({why})")
    stated = call.execution
    # Labs' own policy for the agent the statement names -- from labs' settings, never
    # from the statement: the site's requirements, plus the data pages' ZDR when it is
    # the data agent. The statement is then checked against it, separately, below.
    labs_side = real_data_clearance(stated.agent, _labs_requirements_for(stated.agent))
    if not labs_side.granted:
        return labs_side
    refusal = stated.phi_clearance(
        agents=set(canopy_settings.current().phi_agents),
        require=(ZDR,),
    )
    if refusal:
        return Clearance(False, f"canopy's statement does not clear this call for real data ({refusal})")
    return Clearance(True)


def is_real(reads: Reads) -> bool:
    """True when the page would show the agent visit data that is not all generated."""
    from connect_labs.labs.synthetic.provenance import all_generated

    if not reads.visit_data:
        return False
    if not reads.opportunity_ids:
        return True
    return not all_generated(reads.opportunity_ids)


def clearance_for(reads: Reads, agent: str = "", runner_requirements=()) -> Clearance:
    """Whether ``agent`` (default: the site's) may be offered over what ``reads`` names."""
    if not is_real(reads):
        return Clearance(True)
    return real_data_clearance(agent, runner_requirements)


def _labs_requirements_for(agent: str) -> tuple[str, ...]:
    """The runner requirements LABS sets for ``agent``'s conversations beyond the site's:
    a data page's ZDR for the data agent (``data_agent``), nothing for any other."""
    page = data_agent()
    return page.runner_requirements if agent and agent == page.agent else ()


def call_clearance_for(reads: Reads) -> Clearance:
    """Whether this delegated call may read what ``reads`` names (its own statement decides)."""
    if not is_real(reads):
        return Clearance(True)
    return call_real_data_clearance()


@dataclass(frozen=True)
class PageAgent:
    """Which agent a page offers, what its conversations must run on, and how its panel looks."""

    agent: str = ""
    runner_requirements: tuple[str, ...] = ()
    panel: dict | None = None


#: A data page's panel: docked beside the data at a working width, and expandable
#: (canopy's widget grows it over the page).
DATA_PANEL = {"mode": "docked", "width": 560, "expandable": True}


def data_agent() -> PageAgent:
    """The agent labs' data pages offer: ``data_agent`` (``labsdata``) on ZDR runners only,
    or -- when unset -- the site's agent, as on every other page."""
    slug = canopy_settings.current().data_agent
    if not slug:
        return PageAgent()
    return PageAgent(agent=slug, runner_requirements=(ZDR,), panel=dict(DATA_PANEL))


def chosen_agent(choice: str) -> tuple[PageAgent, bool] | None:
    """The agent a workflow chose (``config.agent.agent``: a role or a slug), as its page
    offers it, and whether it is read-only -- or None when labs does not offer it.

    A read-only agent (the data agent, or any agent allowed over real data) runs on ZDR
    runners in the data panel; the site agent is the page's default (an empty
    ``PageAgent``); any other agent is named, on the site's runner requirements."""
    offered = canopy_settings.resolve(choice)
    if offered is None or not offered.slug:
        return None
    if offered.read_only:
        return PageAgent(agent=offered.slug, runner_requirements=(ZDR,), panel=dict(DATA_PANEL)), True
    if offered.value == canopy_settings.SITE_ROLE:
        return PageAgent(), False
    return PageAgent(agent=offered.slug, runner_requirements=canopy_settings.current().runner_requirements), False


@dataclass(frozen=True)
class Availability:
    """Whether a page may offer the agent a workflow chose, and if not, why -- the one
    answer the panel, the run page's Agent box and its "held back" line all read."""

    ok: bool
    read_only: bool = False
    reasons: tuple[str, ...] = ()


def availability(choice: str, reads: Reads) -> Availability:
    """``choice`` (``config.agent.agent``: a role or a slug) over what the page ``reads``."""
    chosen = chosen_agent(choice)
    if chosen is None:
        return Availability(False, reasons=(f"labs does not offer {choice}: choose an agent {ADMIN_PAGE} lists",))
    page_agent, read_only = chosen
    clearance = clearance_for(reads, page_agent.agent, page_agent.runner_requirements)
    if not clearance.granted:
        return Availability(False, read_only, (clearance.reason,))
    return Availability(True, read_only)


def data_agent_clearance() -> Clearance:
    """Whether the data pages may offer their agent over real data (the page-level check)."""
    page = data_agent()
    return real_data_clearance(page.agent, page.runner_requirements)


def panel(
    request, *, reads: Reads, page_agent: PageAgent | None = None, page_key: str = "", **panel_kwargs
) -> dict | None:
    """The SDK's panel context for this page, or None when no agent may be offered here.

    ``panel_kwargs`` are the SDK's ``panel_context`` arguments (resource, backing_tool,
    visible_ids, filters, path). ``page_agent`` names the page's own agent and runner
    requirements, signed into its page token. ``page_key`` signs that token for a
    ``PAGE_SCOPES`` key other than the route's (narrower scopes on the same URL). None
    renders nothing: the include is skipped and no page token is issued.
    """
    page_agent = page_agent or PageAgent()
    if not clearance_for(reads, page_agent.agent, page_agent.runner_requirements).granted:
        return None
    from canopy_sdk.django.pages import panel_context

    if page_agent.agent:
        panel_kwargs.update(agent=page_agent.agent, runner_requirements=list(page_agent.runner_requirements))
    if page_agent.panel:
        panel_kwargs["panel"] = page_agent.panel
    context = panel_context(request, **panel_kwargs)
    return _signed_for(request, context, page_key, page_agent) if page_key else context


def _signed_for(request, context: dict, page_key: str, page_agent: PageAgent) -> dict:
    """The panel with its page token signed for ``page_key`` (a ``PAGE_SCOPES`` key no
    route resolves to) instead of the route that drew it: the page's scopes are
    ``page_key``'s. Only in signed mode -- in key mode the browser names the page by its
    path, so the route's scopes would apply: no panel (fail closed)."""
    if not context.get("ready"):
        return context
    from canopy_sdk.django import conf
    from canopy_sdk.django.pages import _token_url, panel_options

    if conf.page_mode() != conf.SIGNED:
        return {**context, "ready": False}
    token = conf.page_tokens().issue(
        page_key, request.user.pk, agent=page_agent.agent, runner_requirements=list(page_agent.runner_requirements)
    )
    context = {**context, "page_token": token, "token_url": _token_url(token)}
    context["options"] = panel_options(context)
    return context


def workflow_reads(definition, owner_opportunity_id) -> Reads:
    """A workflow run's reads: every opportunity the workflow spans."""
    from connect_labs.workflow.visit_cache import workflow_opportunity_ids

    try:
        return visits(workflow_opportunity_ids(definition, owner_opportunity_id))
    except (TypeError, ValueError):
        return visits(None)


def page_reads(definition, access_token) -> Reads:
    """A page's reads: the opportunities its workflow sources' workflows span.

    A page with no workflow sources shows the agent no visit data (``page_context``
    returns the page, its scope and its Settings). A source whose workflow cannot be
    read here makes the whole page "cannot tell".
    """
    from connect_labs.workflow import workflow_sources
    from connect_labs.workflow.data_access import WorkflowDataAccess

    sources = list(workflow_sources.declared(definition).values())
    if not sources:
        return NO_VISIT_DATA
    ids: set[int] = set()
    for source in sources:
        owner = {k: source[k] for k in workflow_sources.OWNER_KEYS if source.get(k) not in (None, "")}
        if len(owner) != 1:
            return visits(None)
        access = WorkflowDataAccess(access_token=access_token, **owner)
        try:
            target = access.get_definition(int(source["workflow"]))
        except Exception:  # noqa: BLE001 -- unreadable means "cannot tell", never a 500
            target = None
        finally:
            access.close()
        if target is None:
            return visits(None)
        spanned = workflow_reads(target, owner.get("opportunity_id")).opportunity_ids
        if not spanned:
            return visits(None)
        ids.update(spanned)
    return visits(ids)
