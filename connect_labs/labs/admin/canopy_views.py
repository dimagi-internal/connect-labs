"""Labs Admin › Canopy agents: the one place labs' side of canopy is set up.

A table built from canopy's site agents API (``canopy_settings.site_agents``): one row
per agent canopy lists for labs, plus any labs still ticks that canopy no longer lists
(shown in red). Labs' choices are ticks; nothing is typed. Readable by anyone with Labs
Admin; changed only by Dimagi staff (``canopy_settings.can_change``), who may also ask
canopy for its list now ("Refresh from canopy"). Below the table, the last delegated
data calls with what canopy's statement said about each.

Every sentence from canopy is canopy's own; when canopy cannot be reached the page says
so rather than putting words in its mouth.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe
from django.views.generic import TemplateView

from connect_labs.labs import canopy, canopy_panel, canopy_settings
from connect_labs.labs.view_mixins import AdminRequiredMixin

logger = logging.getLogger(__name__)

#: Delegated calls whose statement decides whether an agent sees real rows.
DATA_TOOLS = ("data_query", "data_visits", "data_describe")

#: Any real (non-generated) opportunity: what "over real data" means for a row's state.
_REAL = canopy_panel.visits(None)

_URL = re.compile(r"https?://[^\s<>\"']+")


def canopy_links(listing) -> dict[str, str]:
    """canopy's pages, by the words labs' own sentences name them with. canopy's answer
    gives labs' site page (``site_settings_url``) and, per agent, the page that fixes each
    problem (``problem_links``, see ``_agent_links``); labs builds a URL only where canopy
    gives none: its tenant's Runners settings (else Supervisor's Runners tab), and the
    Connected sites page of a canopy that predates ``site_settings_url``."""
    base = canopy_settings.canopy_url()
    links = {}
    if base:
        tenant = quote(getattr(listing, "tenant", "") or "", safe="") if listing is not None else ""
        links[canopy_settings.RUNNERS] = (
            f"{base}/w/{tenant}/settings/topology/runners" if tenant else f"{base}/supervisor?tab=runners"
        )
        if tenant:
            links[canopy_settings.CONNECTED_SITES] = f"{base}/w/{tenant}/settings/connected-apps"
    site_url = getattr(listing, "site_settings_url", "") if listing is not None else ""
    if _is_web_url(site_url):
        links[canopy_settings.CONNECTED_SITES] = site_url
    return links


def _agent_links(agent, links: dict[str, str]) -> dict[str, str]:
    """``links``, plus each of ``agent``'s problem sentences linked where canopy says it is fixed."""
    return {**links, **{p.text: p.url for p in getattr(agent, "problem_links", ()) if p.text and _is_web_url(p.url)}}


def _is_web_url(url: str) -> bool:
    """An absolute http(s) URL: the only kind of link canopy's answer may put on this page."""
    return bool(_URL.fullmatch(url or ""))


def linkify(text: str, links: dict[str, str] | None = None) -> SafeString:
    """``text``, escaped, with every http(s) URL in it, and every phrase ``links`` names,
    made a link (opening in a new tab)."""
    links = links or {}
    parts = [_URL.pattern] + [re.escape(p) for p in sorted(links, key=len, reverse=True)]
    out, pos = [], 0
    for m in re.finditer("|".join(parts), text or ""):
        token, end = m.group(0), m.end()
        if token in links:
            href = links[token]
        else:  # a URL: sentence punctuation after it is not part of it
            stripped = token.rstrip(".,;:)")
            end -= len(token) - len(stripped)
            token = href = stripped
        out.append(escape(text[pos : m.start()]))
        out.append(
            format_html(
                '<a class="text-blue-600 underline" href="{}" target="_blank" rel="noopener">{}</a>', href, token
            )
        )
        pos = end
    out.append(escape((text or "")[pos:]))
    return mark_safe("".join(str(o) for o in out))  # noqa: S308 -- every piece escaped or format_html'd


def _rows(ticks: canopy_settings.AgentSettings, listing, links: dict[str, str]) -> list[dict]:
    """One row per agent canopy lists, then any labs ticks that canopy does not."""
    listed = list(listing.agents) if listing is not None else []
    slugs = [a.slug for a in listed] + [t.slug for t in ticks.agents if t.slug not in {a.slug for a in listed}]
    rows = []
    for slug in slugs:
        agent = listing.agent(slug) if listing is not None else None
        tick = ticks.ticks(slug)
        agent_links = _agent_links(agent, links)
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
                "problems": [linkify(p, agent_links) for p in agent.problems] if agent else [],
                "offered": bool(tick and tick.offered),
                "real_data": bool(tick and tick.real_data),
                "default": slug == ticks.default_agent,
                "eligible_for_real_data": eligible,
                "why_not_real_data": why_not,  # plain, for a title attribute
                "why_not_real_data_html": linkify(why_not, agent_links),
                # Ticked but held back (drift, or canopy unreachable): in red, with why.
                "held_back": (
                    [linkify(r, agent_links) for r in here.reasons] if tick and tick.offered and not here.ok else []
                ),
                "real_data_held_back": (
                    [linkify(r, agent_links) for r in over_real.reasons]
                    if tick and tick.real_data and here.ok and not over_real.ok
                    else []
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
        error = canopy_settings.last_error()
        why = f" ({error.code}: {error.message})" if error is not None else ""
        return {
            "level": "warning",
            "text": f"canopy's list could not be refreshed{why}: using its last word, fetched {fetched}. "
            "After a day without a fresh one, no agent is shown.",
        }
    return {"level": "ok", "text": f"canopy's signed list for {listing.site_name or 'labs'}, fetched {fetched}."}


def _drift(ticks: canopy_settings.AgentSettings, listing) -> str:
    """Why to review the settings, when canopy's list has changed since they were saved."""
    if not ticks.is_set_up or listing is None or not ticks.site_agents_version:
        return ""
    if listing.version == ticks.site_agents_version:
        return ""
    return (
        f"canopy's list changed since these settings were saved (saved against version "
        f"{ticks.site_agents_version[:12]}, canopy now says {listing.version[:12]}) — review them and save."
    )


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
            "ok": canopy.grant_on(),
            "detail": (
                "the grant is on"
                if canopy.grant_on()
                else (
                    'tick "Canopy may act as the visitor" above and save'
                    if canopy.canopy_client_id()
                    else "labs has no canopy (CANOPY_BASE_URL is not set)"
                )
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
        listing = canopy_settings.site_agents()
        if listing is not None:
            canopy_settings.learn_workspace(listing)
        ticks = canopy_settings.current()
        links = canopy_links(listing)
        error = canopy_settings.last_error()
        # The runner flags canopy names (sensitive_data always), and what canopy says each
        # means -- nothing, when canopy cannot be reached.
        flags = [{"flag": f.flag, "meaning": f.meaning} for f in (listing.runner_flags if listing else ())]
        if not any(f["flag"] == canopy_settings.SENSITIVE_DATA for f in flags):
            flags.append({"flag": canopy_settings.SENSITIVE_DATA, "meaning": ""})
        context.update(
            current=ticks,
            listing=listing,
            status=_status(listing),
            drift=_drift(ticks, listing),
            rows=_rows(ticks, listing, links),
            runner_flags=flags,
            can_change=canopy_settings.can_change(self.request.user),
            history=canopy_settings.history(),
            checks=[{**c, "detail": linkify(c["detail"], links)} for c in _checks(ticks, listing)],
            calls=_recent_calls(),
            canopy_url=canopy_settings.canopy_url(),
            connected_sites_url=links.get(canopy_settings.CONNECTED_SITES, ""),
            error=error,
            # The tenant labs names cannot be right: offer to name it by the default agent again.
            offer_forget_workspace=bool(ticks.workspace and error is not None and error.code == "workspace_mismatch"),
            grant_available=bool(canopy.canopy_client_id()),
            admin_page=canopy_panel.ADMIN_PAGE,
        )
        return context

    def post(self, request, *args, **kwargs):
        here = reverse("labs_admin:canopy_agents")
        if not canopy_settings.can_change(request.user):
            messages.error(request, "Only Dimagi staff can change which agents labs allows.")
            return redirect(here)
        try:
            if request.POST.get("refresh"):
                self._refresh(request)
            elif request.POST.get("restore"):
                canopy_settings.restore(
                    request.user, int(request.POST["restore"]), listing=canopy_settings.site_agents()
                )
                messages.success(request, "Restored.")
            elif request.POST.get("forget_workspace"):
                canopy_settings.forget_workspace(request.user)
                messages.success(request, "Workspace forgotten: canopy is asked by the default agent again.")
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
                    acts_as_visitor="acts_as_visitor" in request.POST,
                    listing=listing,
                    note=request.POST.get("note", ""),
                )
                messages.success(request, "Saved. It is in force now.")
        except (canopy_settings.InvalidSettings, ValueError) as e:
            messages.error(request, str(e))
        return redirect(here)

    @staticmethod
    def _refresh(request):
        """Ask canopy for its list now, and say what came back."""
        from canopy_sdk.site_agents import SiteAgentsError

        try:
            listing = canopy_settings.refresh()
        except SiteAgentsError as e:
            messages.error(request, f"canopy could not be asked ({e.code}): {e.message}")
            return
        except Exception as e:  # noqa: BLE001 -- say it on the page, never a 500
            logger.exception("Refresh from canopy failed")
            messages.error(request, f"canopy could not be asked ({type(e).__name__}).")
            return
        canopy_settings.learn_workspace(listing)
        agents = ", ".join(a.slug for a in listing.agents) or "none"
        messages.success(
            request,
            f"canopy answered: {len(listing.agents)} agent(s) for {listing.site_name or 'labs'} ({agents}), "
            f"version {listing.version[:12]}.",
        )
