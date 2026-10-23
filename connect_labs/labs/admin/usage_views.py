"""Labs Usage: who uses labs, for which organisation / programme / opportunity,
doing what, over time. One view serves the overview and every drill-down; the
rollups live in ``usage.py``."""

from django.http import Http404
from django.views.generic import TemplateView

from connect_labs.audit_trail import service as audit_service
from connect_labs.audit_trail.models import Action as AuditAction
from connect_labs.labs.view_mixins import AdminRequiredMixin

from . import usage

EXCLUDE_STAFF_PARAM = "exclude_dimagi_staff"

SCOPE_KINDS = {"org": "Organisation", "program": "Programme", "opp": "Opportunity", "user": "User"}


class UsageView(AdminRequiredMixin, TemplateView):
    template_name = "labs/admin/usage.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        kind = kwargs.get("kind", "all")
        key = kwargs.get("key", "")
        if kind != "all" and kind not in SCOPE_KINDS:
            raise Http404
        if kind in ("program", "opp", "user") and not key.isdigit():
            raise Http404
        scope = usage.Scope(kind=kind, key=key)

        try:
            days = int(self.request.GET.get("days", usage.DEFAULT_WINDOW))
        except ValueError:
            days = usage.DEFAULT_WINDOW
        if days not in usage.WINDOW_CHOICES:
            days = usage.DEFAULT_WINDOW
        # Carried forward on every link the page draws (they append ``query``), so
        # it holds while you drill down.
        hide_staff = self.request.GET.get(EXCLUDE_STAFF_PARAM) == "1"

        # Usage metadata is audit-trail data (who was where); reading it is itself audited.
        audit_service.record(
            AuditAction.READ,
            resource_type="usage_analytics",
            resource_id=f"{kind}:{key}" if key else kind,
        )

        report = usage.build_report(scope, days=days, hide_staff=hide_staff)
        events = report.pop("events")
        directory = report["directory"]
        context.update(report)
        context.update(
            {
                "scope": scope,
                "scope_kind_label": SCOPE_KINDS.get(kind, ""),
                "title": self._title(scope, directory),
                "window_choices": usage.WINDOW_CHOICES,
                # On a user's page every row has exactly one user: the column says nothing.
                "show_scope_users": kind != "user",
                "top_pages": usage.top_pages(events),
                "agent_tools": usage.agent_tools(scope, report["since"], hide_staff=hide_staff),
                "recent": self._recent(events, directory) if kind != "all" else [],
                "parent": self._parent(scope, directory),
                "query": self.request.GET.urlencode(),
            }
        )
        return context

    @staticmethod
    def _title(scope, directory) -> str:
        if scope.kind == "org":
            return directory.org_label(scope.key)
        if scope.kind == "program":
            return directory.program_label(int(scope.key))
        if scope.kind == "opp":
            return directory.opp_label(int(scope.key))
        if scope.kind == "user":
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.filter(pk=int(scope.key)).first()
            return user.username if user else f"user #{scope.key}"
        return "Labs Usage"

    @staticmethod
    def _parent(scope, directory) -> list[dict]:
        """Breadcrumb links up the org -> programme -> opportunity tree."""
        crumbs = []
        if scope.kind == "opp":
            org, prog, _ = directory.resolve(int(scope.key), None, None)
            if org:
                crumbs.append({"kind": "org", "key": org, "label": directory.org_label(org)})
            if prog is not None:
                crumbs.append({"kind": "program", "key": prog, "label": directory.program_label(prog)})
        elif scope.kind == "program":
            org, _, _ = directory.resolve(None, int(scope.key), None)
            if org:
                crumbs.append({"kind": "org", "key": org, "label": directory.org_label(org)})
        return crumbs

    @staticmethod
    def _recent(events, directory) -> list[dict]:
        rows = usage.recent_activity(events)
        for r in rows:
            if r["source"] == "mcp":
                # An MCP call's audit context sets path to "mcp:<tool_name>" (mcp/server.py).
                tool = r["path"].removeprefix("mcp:") if r["path"].startswith("mcp:") else ""
                r["what"] = f"MCP {tool}" if tool else "MCP call"
            elif r["action"] == "page_view":
                r["what"] = r["path"] + (f"?{r['query_string']}" if r["query_string"] else "")
            else:
                r["what"] = f"{r['action']} {r['resource_type']}"
            r["scope_label"] = (
                directory.opp_label(r["eff_opp"])
                if r["eff_opp"] is not None
                else directory.program_label(r["eff_prog"]) if r["eff_prog"] is not None else ""
            )
        return rows
