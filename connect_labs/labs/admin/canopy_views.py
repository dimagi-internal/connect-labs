"""Labs Admin › Canopy agents: which canopy agents labs offers, and which may read real data.

Readable by anyone with Labs Admin; changed only by Dimagi staff
(``canopy_settings.can_change``). Shows, beside the settings, everything labs can
check about whether an agent will be given real rows, and the last delegated data
calls with what canopy's statement said about each, so a test from the Data page can
be read here.
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


def _checks(current: canopy_settings.AgentSettings) -> list[dict]:
    """Labs' side of "will the data agent get real rows?", each ok or not, with why."""
    agent = current.data_agent
    clearance = canopy_panel.data_agent_clearance()
    return [
        {
            "label": "The Data pages offer their own agent",
            "ok": bool(agent),
            "detail": agent or "not set: the Data pages offer the site agent",
        },
        {
            "label": "Labs allows it over real data",
            "ok": bool(agent) and agent in current.phi_agents,
            "detail": ", ".join(current.phi_agents) or "no agent is allowed",
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
            "detail": "dimagi-canopy >= 0.12" if canopy_panel.CANOPY_EXECUTION_STATEMENTS else "SDK too old",
        },
        {
            "label": "So a Data page may offer it over real data",
            "ok": clearance.granted,
            "detail": clearance.reason or "yes; each call is still held to canopy's statement",
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
        current = canopy_settings.current()
        canopy = (getattr(settings, "CANOPY_BASE_URL", "") or "").rstrip("/")
        context.update(
            current=current,
            environment=canopy_settings.from_environment(),
            can_change=canopy_settings.can_change(self.request.user),
            known_flags=canopy_settings.KNOWN_RUNNER_FLAGS,
            history=canopy_settings.history(),
            checks=_checks(current),
            calls=_recent_calls(),
            canopy_url=canopy,
            data_agent_url=f"{canopy}/agents/{current.data_agent}" if canopy and current.data_agent else "",
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
                canopy_settings.save(
                    request.user,
                    site_agent=request.POST.get("site_agent", ""),
                    data_agent=request.POST.get("data_agent", ""),
                    phi_agents=request.POST.get("phi_agents", "").replace(",", " ").split(),
                    runner_requirements=request.POST.getlist("runner_requirements"),
                    note=request.POST.get("note", ""),
                )
                messages.success(request, "Saved. It is in force now.")
        except (canopy_settings.InvalidSettings, ValueError) as e:
            messages.error(request, str(e))
        return redirect(here)
