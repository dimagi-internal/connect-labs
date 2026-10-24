"""The one door to the canopy agent panel on a labs page: which data the page would
show the agent, and whether an agent may be offered over it at all.

Every labs view that shows the panel calls ``panel(request, reads=..., ...)`` here and
never the SDK's ``panel_context`` directly (a test pins that). A page states what the
agent could read through it:

* ``NO_VISIT_DATA`` -- the marketplace directory, targeting's open data, a tender, the
  explorer (whose agent writes SQL and never sees a row). Offered as before.
* ``visits(opportunity_ids)`` -- a workflow run, or a page whose workflow sources carry
  run summaries computed from visits. ``None`` means "cannot tell", which counts as
  sensitive data.

**The rule.** Over visit data that is not all GENERATED synthetic data
(``labs/synthetic/provenance.py``) -- production data, with PII and PHI in it: labs'
sensitive data -- the panel is not drawn at all, unless labs has cleared the agent for
sensitive data
(``sensitive_data_clearance``). No panel means no page token, no grant and no ID-JAG, so the
agent cannot reach the page's tools as the visitor by any path. The MCP server holds
canopy's calls to the same rule independently (``mcp/server.py:policy_denial``), so a
token minted before a workflow's data class changed is refused too.

**Which agent, and whether at all** is ``availability``: labs' ticks (Labs Admin ›
Canopy agents, ``labs/canopy_settings.py``) AND canopy's signed word on the agent (the
site agents API: listed for labs, ready, handles sensitive data, a ready sensitive-data
runner). When canopy has said nothing usable for a day, no agent is shown. An agent
canopy declares handles sensitive data, or that labs lets work with sensitive data, is always
offered on sensitive-data runners and never with the run's actions (``workflow:act``): it
reads, and on a run may propose edits to the workflow that the person confirms and that
stay in labs (``canopy.RUN_FOR_DATA_AGENT``).

**Clearance for sensitive data** needs, and fails closed on, each of:

1. labs lets the agent work with sensitive data ("Sensitive data" ticked; off by default);
2. canopy declares it handles sensitive data and it has a ready sensitive-data runner
   (the page check, from the site agents API -- so drift in canopy withdraws it at once);
3. every conversation with it requires sensitive-data runners (signed into its page token);
4. canopy vouches, on each call, for the agent's sensitive-data declaration and the runner
   executing it, in a ``Canopy-Execution`` statement labs verifies (``mcp/execution.py``;
   ``call_sensitive_data_clearance``). No statement, or one that does not verify, is refused.

A CALL is held to 1, 3 and its own statement (``call_sensitive_data_clearance``), never to a
cached listing: the statement is canopy's word on exactly that call.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs import canopy_settings


def _execution_statements_supported() -> bool:
    from connect_labs.mcp import execution

    return execution.supported()


#: Whether labs can verify canopy's per-call ``Canopy-Execution`` statement (the SDK
#: has ``verify_execution``, dimagi-canopy >= 0.13). Without it no agent is ever
#: cleared for sensitive data. With it, a page may offer a cleared agent, and every call is
#: still held to its own verified statement (``call_sensitive_data_clearance``).
CANOPY_EXECUTION_STATEMENTS = _execution_statements_supported()

#: Where labs' side of every refusal is fixed.
ADMIN_PAGE = "Labs Admin › Canopy agents"

_UNSET = object()


@dataclass(frozen=True)
class Reads:
    """What the agent could read through a page: no visit data, or visits of these opps.

    ``opportunity_ids is None`` with ``visit_data`` means the page cannot tell which
    opportunities it reads, which is treated as sensitive data.
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


def sensitive_data_clearance(agent: str = "") -> Clearance:
    """Labs' own side of letting ``agent`` (default: the default agent) work with labs'
    sensitive data (non-generated visits): "Sensitive data" ticked, and an SDK that verifies canopy's per-call
    statement. Canopy's side is ``canopy_sensitive_data`` (a page) or the call's statement
    (``call_sensitive_data_clearance``).

    Its conversations are held to sensitive-data runners by construction, not by a
    setting: an agent with "Sensitive data" ticked is offered only on sensitive-data runners, with
    ``sensitive_data`` signed into its page token (``availability``), and a call is
    cleared only when its statement shows that requirement (``call_sensitive_data_clearance``).
    """
    ticks = canopy_settings.current()
    agent = agent or ticks.default_agent
    if agent not in ticks.sensitive_data_agents:
        return Clearance(False, f'{agent} is not allowed sensitive data: tick "Sensitive data" for it in {ADMIN_PAGE}')
    if not CANOPY_EXECUTION_STATEMENTS:
        return Clearance(False, "canopy does not yet vouch for the agent's declaration and its runner on each call")
    return Clearance(True)


def canopy_sensitive_data(agent: str, listing) -> Clearance:
    """canopy's side, for a page: the site agents API lists ``agent`` as handling
    sensitive data and ready on a sensitive-data runner. Drift in canopy withdraws the agent here at once."""
    if listing is None:
        return Clearance(False, canopy_settings.unreachable_reason())
    ok, why = canopy_settings.eligible_for_sensitive_data(listing.agent(agent))
    return Clearance(ok, why)


def call_sensitive_data_clearance() -> Clearance:
    """Whether THIS call (canopy acting for a visitor) may be given sensitive data.

    Labs' own conditions for the agent canopy's verified statement names, then the
    statement's: the capability declared to handle sensitive data, ``sensitive_data`` in
    force on a runner in sensitive-data mode, a conversation that requires it
    (``Execution.sensitive_clearance``).
    """
    from connect_labs.mcp import execution

    call = execution.current()
    if call is None or call.execution is None:
        why = (call.refused if call else "") or "no statement"
        return Clearance(False, f"canopy did not vouch for this call ({why})")
    stated = call.execution
    # Labs' own policy for the agent the statement names -- from labs' ticks, never from
    # the statement. An agent allowed sensitive data is only ever offered on sensitive-data
    # runners (its page token requires them), so that is its requirement here too, and the
    # statement is checked against it below.
    labs_side = sensitive_data_clearance(stated.agent)
    if not labs_side.granted:
        return labs_side
    refusal = stated.sensitive_clearance(
        agents=set(canopy_settings.current().sensitive_data_agents),
        require=(canopy_settings.SENSITIVE_DATA,),
    )
    if refusal:
        return Clearance(False, f"canopy's statement does not clear this call for sensitive data ({refusal})")
    return Clearance(True)


def is_sensitive(reads: Reads) -> bool:
    """True when the page would show the agent labs' sensitive data: visit data that is not
    all generated (production visits carry PII and PHI)."""
    from connect_labs.labs.synthetic.provenance import all_generated

    if not reads.visit_data:
        return False
    if not reads.opportunity_ids:
        return True
    return not all_generated(reads.opportunity_ids)


def call_clearance_for(reads: Reads) -> Clearance:
    """Whether this delegated call may read what ``reads`` names (its own statement decides)."""
    if not is_sensitive(reads):
        return Clearance(True)
    return call_sensitive_data_clearance()


@dataclass(frozen=True)
class PageAgent:
    """Which agent a page offers, what its conversations must run on, and how its panel looks.
    An empty ``agent`` is the default agent (the one every token names)."""

    agent: str = ""
    runner_requirements: tuple[str, ...] = ()
    panel: dict | None = None


#: A data page's panel: docked beside the data at a working width, and expandable
#: (canopy's widget grows it over the page).
DATA_PANEL = {"mode": "docked", "width": 560, "expandable": True}


@dataclass(frozen=True)
class Availability:
    """Whether a page may offer an agent, and if not, why -- the one answer the panel,
    the run page's Agent box and its "held back" line, and the Labs Admin table all read."""

    ok: bool
    slug: str = ""
    #: Held to sensitive-data runners and given none of a run's actions (``workflow:act``).
    #: Not "no writes": on a run its page key is ``canopy.RUN_FOR_DATA_AGENT``, whose
    #: ``workflow:edit`` lets it propose edits to the workflow that the person confirms.
    read_only: bool = False
    reasons: tuple[str, ...] = ()
    page_agent: PageAgent | None = None


def availability(choice: str, reads: Reads, *, ticks=None, listing=_UNSET) -> Availability:
    """Whether ``choice`` (a slug; legacy ``"site"``/``"data"``, or "" for the default
    agent) may be offered over what the page ``reads``, and on what terms.

    Shown by labs ("Show on labs"), listed and ready in canopy; on sensitive-data runners,
    without the run's actions, when canopy declares it handles sensitive data or labs lets it
    work with sensitive data; over sensitive data,
    only when both sides clear it (``sensitive_data_clearance``, ``canopy_sensitive_data``)."""
    ticks = ticks or canopy_settings.current()
    listing = canopy_settings.site_agents() if listing is _UNSET else listing
    if not ticks.agents:
        return Availability(False, reasons=(f"nothing is set up yet: choose agents in {ADMIN_PAGE}",))
    slug = canopy_settings.resolve_choice(choice, ticks, listing)
    if not slug:
        return Availability(False, reasons=(f"no agent for data work is shown: tick one in {ADMIN_PAGE}",))
    tick = ticks.ticks(slug)
    if tick is None or not tick.offered:
        return Availability(False, slug, reasons=(f'labs does not show {slug}: tick "Show on labs" in {ADMIN_PAGE}',))
    if listing is None:
        return Availability(False, slug, reasons=(canopy_settings.unreachable_reason(),))
    agent = listing.agent(slug)
    if agent is None:
        return Availability(
            False, slug, reasons=(f"canopy does not allow {slug} on labs' site: {canopy_settings.CONNECTED_SITES}",)
        )
    if not agent.ready:
        return Availability(False, slug, reasons=agent.problems or (f"{slug} is not ready in canopy",))
    read_only = tick.sensitive_data or agent.handles_sensitive
    if read_only:
        if not agent.ready_with_flag(canopy_settings.SENSITIVE_DATA):
            return Availability(
                False,
                slug,
                True,
                (
                    f"{slug} runs only on sensitive-data runners, and has no ready one in canopy "
                    f"({canopy_settings.RUNNERS})",
                ),
            )
        page_agent = PageAgent(
            agent=slug, runner_requirements=(canopy_settings.SENSITIVE_DATA,), panel=dict(DATA_PANEL)
        )
    elif slug == ticks.default_agent:
        page_agent = PageAgent()
    else:
        page_agent = PageAgent(agent=slug, runner_requirements=tuple(ticks.runner_requirements))
    if is_sensitive(reads):
        for clearance in (
            sensitive_data_clearance(slug),
            canopy_sensitive_data(slug, listing),
        ):
            if not clearance.granted:
                return Availability(False, slug, read_only, (clearance.reason,), page_agent)
    return Availability(True, slug, read_only, (), page_agent)


def data_page_agent(reads: Reads = None) -> Availability:
    """The Data pages' agent: the agent for data work (``canopy_settings.data_agent``),
    always on sensitive-data runners -- or the default agent when there is none."""
    reads = reads or NO_VISIT_DATA
    ticks = canopy_settings.current()
    listing = canopy_settings.site_agents()
    slug = canopy_settings.data_agent(ticks, listing)
    return availability(slug or "", reads, ticks=ticks, listing=listing)


def panel(
    request,
    *,
    reads: Reads,
    available: Availability | None = None,
    page_key: str = "",
    holds=None,
    **panel_kwargs,
) -> dict | None:
    """The SDK's panel context for this page, or None when no agent may be offered here.

    ``available`` is the page's own agent (``availability``); default: the default agent.
    ``panel_kwargs`` are the SDK's ``panel_context`` arguments (resource, backing_tool,
    visible_ids, filters, path). ``page_key`` signs the page token for a ``PAGE_SCOPES``
    key other than the route's (narrower scopes on the same URL). ``holds``
    (``labsdata.page_holds.Holds``) is what the page shows: signed onto the token URL, it is
    what the agent's ``data_*`` reads are held to (a page granting ``data:read`` without it
    reads nothing). None renders nothing: the include is skipped and no page token is issued.
    """
    available = available or availability("", reads)
    if not available.ok:
        return None
    page_agent = available.page_agent or PageAgent()
    from canopy_sdk.django.pages import panel_context

    if page_agent.agent:
        panel_kwargs.update(agent=page_agent.agent, runner_requirements=list(page_agent.runner_requirements))
    if page_agent.panel:
        panel_kwargs["panel"] = page_agent.panel
    context = panel_context(request, **panel_kwargs)
    context = _signed_for(request, context, page_key, page_agent) if page_key else context
    if holds is not None:
        from connect_labs.labsdata import page_holds

        context = page_holds.attach(request, context, holds)
    return context


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
