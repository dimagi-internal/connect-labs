"""Labs Usage: who uses labs, for which organisation / programme / opportunity,
doing what, over time. One view serves the overview and every drill-down; the
rollups live in ``usage.py``."""

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.audit_trail import service as audit_service
from connect_labs.audit_trail.models import Action as AuditAction
from connect_labs.labs.context import CONTEXT_PARAMS
from connect_labs.labs.view_mixins import AdminRequiredMixin

from . import usage

EXCLUDE_STAFF_PARAM = "exclude_dimagi_staff"

SCOPE_KINDS = {"org": "Organisation", "program": "Programme", "opp": "Opportunity", "user": "User"}


# Labs context param -> usage scope kind, most specific first.
CONTEXT_SCOPES = (("opportunity_id", "opp"), ("program_id", "program"), ("organization_id", "org"))


def _scope_from(context: dict) -> tuple[str, str] | None:
    for param, kind in CONTEXT_SCOPES:
        value = context.get(param)
        if value not in (None, ""):
            return kind, str(value)
    return None


class UsageView(AdminRequiredMixin, TemplateView):
    """Usage pages carry their scope in the path, and the overview is about all of
    labs, so the labs context does not decide what they show. The context
    middleware leaves them undecorated (``CROSS_SCOPE_PAGE_PREFIXES``); an explicit
    context param on the overview is honoured by sending you to that scope's page."""

    template_name = "labs/admin/usage.html"

    def get(self, request, *args, **kwargs):
        if kwargs.get("kind", "all") == "all":
            explicit = _scope_from({p: request.GET.get(p) for p in CONTEXT_PARAMS})
            if explicit:
                target = reverse("labs_admin:usage_scope", args=explicit)
                query = self._forwarded_params().urlencode()
                return HttpResponseRedirect(f"{target}?{query}" if query else target)
        return super().get(request, *args, **kwargs)

    def _forwarded_params(self):
        """This request's params minus labs context ones, which say nothing to a
        usage page and would otherwise ride along on every link it draws."""
        params = self.request.GET.copy()
        for param in CONTEXT_PARAMS:
            params.pop(param, None)
        return params

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
                "export_triggers": usage.export_triggers(events),
                "agent_tools": usage.agent_tools(scope, report["since"], hide_staff=hide_staff),
                "recent": self._recent(events, directory) if kind != "all" else [],
                "parent": self._parent(scope, directory),
                "query": self._forwarded_params().urlencode(),
                "context_link": self._context_link(scope),
                "staff_included_url": self._with_staff(False),
                "staff_excluded_url": self._with_staff(True),
            }
        )
        return context

    def _with_staff(self, exclude: bool) -> str:
        """This page with the staff filter set, everything else kept."""
        params = self._forwarded_params()
        params.pop(EXCLUDE_STAFF_PARAM, None)
        if exclude:
            params[EXCLUDE_STAFF_PARAM] = "1"
        return f"{self.request.path}?{params.urlencode()}" if params else self.request.path

    def _context_link(self, scope) -> dict | None:
        """A one-click way from here to the usage of the labs context you are in."""
        target = _scope_from(getattr(self.request, "labs_context", None) or {})
        if not target or (scope.kind, scope.key) == target:
            return None
        kind, key = target
        directory = usage.Directory.load(
            [int(key)] if kind == "opp" else [],
            [int(key)] if kind == "program" else [],
            [key] if kind == "org" else [],
        )
        label = self._title(usage.Scope(kind, key), directory)
        query = self._forwarded_params().urlencode()
        url = reverse("labs_admin:usage_scope", args=[kind, key])
        return {"label": f"{SCOPE_KINDS[kind]}: {label}", "url": f"{url}?{query}" if query else url}

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
