"""The Data page at /labs/data/ and its JSON endpoints.

The endpoints are thin: they build a ``Caller`` from the request and call the
same ``service`` functions the MCP tools call, so the page, the in-page agent and
an MCP client can never disagree about what a person may read.
"""

from __future__ import annotations

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller

from . import scope, service
from .engine import QueryError
from .scope import ScopeError
from .validator import DEFAULT_MAX_ROWS

EXAMPLE_SQL = """SELECT llo, opportunity_name, COUNT(*) AS visits, COUNT(DISTINCT entity_id) AS cases
FROM visits
WHERE status <> 'rejected'
GROUP BY llo, opportunity_name
ORDER BY visits DESC"""


def _caller(request) -> Caller:
    return Caller(user=request.user, request=request)


def _ids(raw: str | None) -> list[int]:
    return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()]


def _selected(request) -> list[int]:
    return _ids(request.GET.get("opps"))


def _workflow_param(raw: str | None) -> dict | None:
    """``?workflow=<id>:opp:<opportunity_id>`` or ``<id>:program:<program_id>`` -- the scope it is read in."""
    parts = (raw or "").split(":")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit() or parts[1] not in {"opp", "program"}:
        return None
    key = "opportunity_id" if parts[1] == "opp" else "program_id"
    return {"id": int(parts[0]), key: int(parts[2])}


def initial_selection(request) -> dict:
    """What the page opens on: the URL's selection, else the labs context (an opportunity
    or a program), else nothing. Only ids -- every one is checked when it is read."""
    workflow = _workflow_param(request.GET.get("workflow"))
    opps, programs = _selected(request), _ids(request.GET.get("programs"))
    if workflow or opps or programs:
        return {
            "workflow": workflow,
            "opportunity_ids": [] if workflow else opps,
            "program_ids": [] if workflow else programs,
        }
    context = getattr(request, "labs_context", None) or {}
    if context.get("opportunity_id"):
        return {"workflow": None, "opportunity_ids": [int(context["opportunity_id"])], "program_ids": []}
    if context.get("program_id"):
        return {"workflow": None, "opportunity_ids": [], "program_ids": [int(context["program_id"])]}
    return {"workflow": None, "opportunity_ids": [], "program_ids": []}


class DataView(LoginRequiredMixin, TemplateView):
    template_name = "labsdata/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        caller = _caller(self.request)
        opps = sorted(scope.directory(caller).values(), key=lambda o: (o.llo.lower(), o.name.lower()))
        initial = initial_selection(self.request)
        selected = initial["opportunity_ids"]
        ctx.update(
            {
                "labsdata_options": {
                    "opportunities": [
                        {"id": o.id, "name": o.name, "llo": o.llo, "program_id": o.program_id} for o in opps
                    ],
                    "programs": scope.programs(caller),
                    "initial": initial,
                },
                # A link from the agent's result View opens the page on its query; it is
                # put in the editor, never run on load.
                "example_sql": (self.request.GET.get("sql") or "")[:20000] or EXAMPLE_SQL,
                "default_max_rows": DEFAULT_MAX_ROWS,
            }
        )
        from connect_labs.labs import canopy_panel

        # Ids, never rows: the agent reads data itself through the data_* tools, as
        # the visitor, and those tools decide whether it may see production rows
        # (service.agent_sees_sensitive_rows). Until labs clears the agent for sensitive data
        # it gets structure and hands SQL back to this page, so the page shows it no
        # visit data by itself.
        ctx["canopy_panel"] = canopy_panel.panel(
            self.request,
            reads=canopy_panel.NO_VISIT_DATA,
            # The agent for data work (labsdata), read-only on sensitive-data runners; else the default agent.
            available=canopy_panel.data_page_agent(),
            resource="labs-data://opportunities/" + ",".join(map(str, selected)) if selected else "labs-data://",
            backing_tool="data_describe",
            visible_ids=selected,
            filters={"opportunity_ids": selected},
            path=self.request.path,
        )
        return ctx


class _JsonView(LoginRequiredMixin, View):
    def handle(self, request, body: dict) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request):
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_json", "message": "Body must be JSON."}, status=400)
        try:
            return JsonResponse(self.handle(request, body))
        except ScopeError as e:
            status = 503 if e.code == "scopes_unavailable" else 403 if e.code == "not_held" else 400
            return JsonResponse({"error": e.code, "message": e.message}, status=status)
        except QueryError as e:
            return JsonResponse(e.as_dict(), status=400)


def _selection(body: dict) -> dict:
    return {"program_ids": body.get("program_ids") or None, "workflow": body.get("workflow") or None}


class DescribeView(_JsonView):
    def handle(self, request, body):
        return service.describe(
            _caller(request),
            body.get("opportunity_ids"),
            load_missing=bool(body.get("load_missing")),
            # The page shows no registries and no form-field index; skip both. The
            # agent's data_describe keeps them -- they are how it finds fields and
            # governed measures.
            include_registries=False,
            include_fields=False,
            **_selection(body),
        )


class QueryView(_JsonView):
    def handle(self, request, body):
        return service.query(
            _caller(request),
            body.get("opportunity_ids"),
            body.get("sql") or "",
            int(body.get("max_rows") or 500),
            **_selection(body),
        )


class WorkflowsView(_JsonView):
    """Workflows to choose from: those readable, as the person, in the selection's scopes."""

    def handle(self, request, body):
        return service.workflows(
            _caller(request), opportunity_ids=body.get("opportunity_ids"), program_ids=body.get("program_ids")
        )


class VisitsView(_JsonView):
    def handle(self, request, body):
        return service.visits(_caller(request), body.get("opportunity_ids"), body.get("visit_ids") or [])
