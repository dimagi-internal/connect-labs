"""Labs Admin › Canopy agents: which canopy agents labs shows, and which may read real data.

A table built from canopy's site agents API (``canopy_settings.site_agents``): one row
per agent canopy lists for labs, plus any labs still ticks that canopy no longer lists
(shown in red). Labs' choices are ticks; nothing is typed. Readable by anyone with Labs
Admin; changed only by Dimagi staff (``canopy_settings.can_change``). Below the table,
the last delegated data calls with what canopy's statement said about each.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.labs import canopy_panel, canopy_settings
from connect_labs.labs.view_mixins import AdminRequiredMixin

#: Delegated calls whose statement decides whether an agent sees real rows.
DATA_TOOLS = ("data_query", "data_visits", "data_describe")

#: Any real (non-generated) opportunity: what "over real data" means for a row's state.
_REAL = canopy_panel.visits(None)

#: What ``sensitive_data`` means, when canopy has not said (it is unreachable).
SENSITIVE_DATA_MEANING = (
    "a sensitive-data runner: a ZDR API key, a locked harness, egress limited to an allowlist, "
    "nothing kept on the box"
)


def _rows(ticks: canopy_settings.AgentSettings, listing) -> list[dict]:
    """One row per agent canopy lists, then any labs ticks that canopy does not."""
    listed = list(listing.agents) if listing is not None else []
    slugs = [a.slug for a in listed] + [t.slug for t in ticks.agents if t.slug not in {a.slug for a in listed}]
    rows = []
    for slug in slugs:
        agent = listing.agent(slug) if listing is not None else None
        tick = ticks.ticks(slug)
        eligible, why_not = canopy_settings.eligible_for_real_data(agent)
        here = canopy_panel.availability(slug, canopy_panel.NO_VISIT_DATA, ticks=ticks, listing=listing)
        over_real = canopy_panel.availability(slug, _REAL, ticks=ticks, listing=listing)
        sensitive = next((c for c in agent.capabilities if c.handles_sensitive), None) if agent else None
        rows.append(
            {
                "slug": slug,
                "listed": agent is not None,
                "name": (agent.name if agent else "") or slug,
                "description": (agent.description if agent else "")
                or "; ".join(c.description for c in (agent.capabilities if agent else ()) if c.description),
                "owner": agent.owner if agent else "",
                "url": agent.url if agent else "",
                "handles_sensitive": bool(agent and agent.handles_sensitive),
                "sensitive_set_by": sensitive.sensitive_set_by if sensitive else "",
                "sensitive_set_at": sensitive.sensitive_set_at if sensitive else "",
                "sensitive_data_runner": bool(agent and agent.ready_with_flag(canopy_settings.SENSITIVE_DATA)),
                "sensitive_data_runner_available": bool(
                    agent and canopy_settings.SENSITIVE_DATA in agent.flags_available
                ),
                "ready": bool(agent and agent.ready),
                "problems": list(agent.problems) if agent else [],
                "offered": bool(tick and tick.offered),
                "real_data": bool(tick and tick.real_data),
                "default": slug == ticks.default_agent,
                "eligible_for_real_data": eligible,
                "why_not_real_data": why_not,
                # Ticked but held back (drift, or canopy unreachable): in red, with why.
                "held_back": list(here.reasons) if tick and tick.offered and not here.ok else [],
                "real_data_held_back": (
                    list(over_real.reasons) if tick and tick.real_data and here.ok and not over_real.ok else []
                ),
                "read_only": here.read_only,
            }
        )
    return rows


def _status(listing) -> dict:
    if listing is None:
        return {"level": "error", "text": canopy_settings.unreachable_reason() + ". No agent is shown on labs."}
    minutes = int(listing.age // 60)
    fetched = f"{minutes} min ago" if minutes else "just now"
    if listing.stale:
        return {
            "level": "warning",
            "text": f"canopy cannot be reached: using its last word, fetched {fetched}. "
            "After a day without a fresh one, no agent is shown.",
        }
    return {"level": "ok", "text": f"canopy's signed list for {listing.site_name or 'labs'}, fetched {fetched}."}


def _checks(ticks: canopy_settings.AgentSettings, listing) -> list[dict]:
    """Labs' side of "will the data agent get real rows?", each ok or not, with why."""
    agent = canopy_settings.data_agent(ticks, listing)
    over_real = canopy_panel.availability(agent, _REAL, ticks=ticks, listing=listing) if agent else None
    return [
        {
            "label": "An agent for data work is shown (the Data page and read-only workflows)",
            "ok": bool(agent),
            "detail": agent
            or 'none: tick "Show on labs" for an agent that handles sensitive data; Data offers the default agent',
        },
        {
            "label": "Labs lets it read real data",
            "ok": bool(agent) and agent in ticks.real_data_agents,
            "detail": ", ".join(ticks.real_data_agents) or 'no agent has "Real data" ticked',
        },
        {
            "label": "Canopy can act as the visitor here",
            "ok": bool(getattr(settings, "CANOPY_CLIENT_ID", "")),
            "detail": (
                "the grant is on" if getattr(settings, "CANOPY_CLIENT_ID", "") else "CANOPY_CLIENT_ID is not set"
            ),
        },
        {
            "label": "Labs verifies canopy's per-call statement",
            "ok": canopy_panel.CANOPY_EXECUTION_STATEMENTS,
            "detail": "dimagi-canopy >= 0.13" if canopy_panel.CANOPY_EXECUTION_STATEMENTS else "SDK too old",
        },
        {
            "label": "So a workflow over real data may offer it",
            "ok": bool(over_real and over_real.ok),
            "detail": (
                "; ".join(over_real.reasons)
                if over_real and over_real.reasons
                else ("yes; each call is still held to canopy's statement" if over_real else "no agent for data work")
            ),
        },
    ]


def _recent_calls(limit: int = 15) -> list:
    from connect_labs.mcp.models import MCPAuditLog

    return list(
        MCPAuditLog.objects.filter(tool_name__in=DATA_TOOLS)
        .exclude(execution={})
        .select_related("user")
        .order_by("-id")[:limit]
    )


class CanopyAgentsView(AdminRequiredMixin, TemplateView):
    template_name = "labs/admin/canopy_agents.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ticks = canopy_settings.current()
        listing = canopy_settings.site_agents()
        canopy = (getattr(settings, "CANOPY_BASE_URL", "") or "").rstrip("/")
        # The runner flags canopy names (sensitive_data always), and what each means.
        flags = [{"flag": f.flag, "meaning": f.meaning} for f in (listing.runner_flags if listing else ())]
        if not any(f["flag"] == canopy_settings.SENSITIVE_DATA for f in flags):
            flags.append({"flag": canopy_settings.SENSITIVE_DATA, "meaning": SENSITIVE_DATA_MEANING})
        context.update(
            current=ticks,
            listing=listing,
            status=_status(listing),
            rows=_rows(ticks, listing),
            runner_flags=flags,
            can_change=canopy_settings.can_change(self.request.user),
            history=canopy_settings.history(),
            checks=_checks(ticks, listing),
            calls=_recent_calls(),
            canopy_url=canopy,
            admin_page=canopy_panel.ADMIN_PAGE,
        )
        return context

    def post(self, request, *args, **kwargs):
        here = reverse("labs_admin:canopy_agents")
        if not canopy_settings.can_change(request.user):
            messages.error(request, "Only Dimagi staff can change which agents labs allows.")
            return redirect(here)
        try:
            if request.POST.get("restore"):
                canopy_settings.restore(request.user, int(request.POST["restore"]))
                messages.success(request, "Restored.")
            else:
                listing = canopy_settings.site_agents()
                agents = [
                    canopy_settings.AgentTicks(
                        slug,
                        offered=f"offered:{slug}" in request.POST,
                        real_data=f"real_data:{slug}" in request.POST,
                    )
                    for slug in request.POST.getlist("slug")
                ]
                canopy_settings.save(
                    request.user,
                    agents=agents,
                    default_agent=request.POST.get("default_agent", ""),
                    runner_requirements=request.POST.getlist("runner_requirements"),
                    listing=listing,
                    note=request.POST.get("note", ""),
                )
                messages.success(request, "Saved. It is in force now.")
        except (canopy_settings.InvalidSettings, ValueError) as e:
            messages.error(request, str(e))
        return redirect(here)
