"""The explorer page at /labs/explorer/ and its two JSON endpoints.

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

from . import service
from .engine import QueryError
from .scope import ScopeError, directory
from .validator import DEFAULT_MAX_ROWS

EXAMPLE_SQL = """SELECT llo, opportunity_name, COUNT(*) AS visits, COUNT(DISTINCT entity_id) AS cases
FROM visits
WHERE status <> 'rejected'
GROUP BY llo, opportunity_name
ORDER BY visits DESC"""


def _caller(request) -> Caller:
    return Caller(user=request.user, request=request)


def _selected(request) -> list[int]:
    raw = request.GET.get("opps") or ""
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]


class ExplorerView(LoginRequiredMixin, TemplateView):
    template_name = "explorer/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        opps = sorted(directory(_caller(self.request)).values(), key=lambda o: (o.llo.lower(), o.name.lower()))
        selected = _selected(self.request)
        ctx.update(
            {
                "opportunities": opps,
                "selected": selected,
                "example_sql": EXAMPLE_SQL,
                "default_max_rows": DEFAULT_MAX_ROWS,
            }
        )
        from canopy_sdk.django.pages import panel_context

        # Ids, never rows: the agent reads data through explorer_* as the visitor.
        ctx["canopy_panel"] = panel_context(
            self.request,
            resource=(
                "labs-explorer://opportunities/" + ",".join(map(str, selected)) if selected else "labs-explorer://"
            ),
            backing_tool="explorer_describe",
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
            return JsonResponse({"error": e.code, "message": e.message}, status=400)


class DescribeView(_JsonView):
    def handle(self, request, body):
        return service.describe(
            _caller(request),
            body.get("opportunity_ids"),
            field_search=body.get("field_search") or None,
            load_missing=bool(body.get("load_missing")),
        )


class QueryView(_JsonView):
    def handle(self, request, body):
        return service.query(
            _caller(request), body.get("opportunity_ids"), body.get("sql") or "", int(body.get("max_rows") or 500)
        )
