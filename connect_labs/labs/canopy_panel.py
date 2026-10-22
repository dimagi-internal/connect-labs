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

1. labs allows the agent it offers on real data: ``CANOPY_AGENT_SLUG`` is in
   ``CANOPY_PHI_AGENTS`` (labs' own list; empty by default). The agent's author
   claims it is PHI-safe; labs decides whether to accept that claim.
2. every visitor conversation must run on ZDR runners: ``zdr`` is in
   ``CANOPY_RUNNER_REQUIREMENTS``, which the SDK signs into every visitor assertion and
   canopy enforces when a runner claims the turn.
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

from django.conf import settings


def _execution_statements_supported() -> bool:
    from connect_labs.mcp import execution

    return execution.supported()


#: Whether labs can verify canopy's per-call ``Canopy-Execution`` statement (the SDK
#: has ``verify_execution``, dimagi-canopy >= 0.12). Without it no agent is ever
#: cleared for real data. With it, a page may offer a cleared agent, and every call is
#: still held to its own verified statement (``call_real_data_clearance``).
CANOPY_EXECUTION_STATEMENTS = _execution_statements_supported()

#: The runner flag every conversation over real data must require.
ZDR = "zdr"


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
    agent = agent or getattr(settings, "CANOPY_AGENT_SLUG", "") or ""
    if agent not in set(getattr(settings, "CANOPY_PHI_AGENTS", ()) or ()):
        return Clearance(False, f"labs has not allowed the agent {agent!r} on real data (CANOPY_PHI_AGENTS)")
    required = set(getattr(settings, "CANOPY_RUNNER_REQUIREMENTS", ()) or ()) | set(runner_requirements or ())
    if ZDR not in required:
        return Clearance(False, "labs does not require ZDR runners of this conversation (CANOPY_RUNNER_REQUIREMENTS)")
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
    labs_side = real_data_clearance(stated.agent, stated.requirements)
    if not labs_side.granted:
        return labs_side
    refusal = stated.phi_clearance(
        agents=set(getattr(settings, "CANOPY_PHI_AGENTS", ()) or ()),
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
    """The agent labs' data pages offer: ``CANOPY_DATA_AGENT_SLUG`` (``labsdata``) on ZDR
    runners only, or -- when unset -- the site's agent, as on every other page."""
    slug = getattr(settings, "CANOPY_DATA_AGENT_SLUG", "") or ""
    if not slug:
        return PageAgent()
    return PageAgent(agent=slug, runner_requirements=(ZDR,), panel=dict(DATA_PANEL))


def data_agent_clearance() -> Clearance:
    """Whether the data pages may offer their agent over real data (the page-level check)."""
    page = data_agent()
    return real_data_clearance(page.agent, page.runner_requirements)


def panel(request, *, reads: Reads, page_agent: PageAgent | None = None, **panel_kwargs) -> dict | None:
    """The SDK's panel context for this page, or None when no agent may be offered here.

    ``panel_kwargs`` are the SDK's ``panel_context`` arguments (resource, backing_tool,
    visible_ids, filters, path). ``page_agent`` names the page's own agent and runner
    requirements, signed into its page token. None renders nothing: the include is
    skipped and no page token is issued.
    """
    page_agent = page_agent or PageAgent()
    if not clearance_for(reads, page_agent.agent, page_agent.runner_requirements).granted:
        return None
    from canopy_sdk.django.pages import panel_context

    if page_agent.agent:
        panel_kwargs.update(agent=page_agent.agent, runner_requirements=list(page_agent.runner_requirements))
    if page_agent.panel:
        panel_kwargs["panel"] = page_agent.panel
    return panel_context(request, **panel_kwargs)


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
