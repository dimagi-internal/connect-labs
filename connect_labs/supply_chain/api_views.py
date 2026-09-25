"""HTTP adapter. One view dispatches every operation, so there is no
per-endpoint glue that can drift from the MCP surface.

**Every operation a CALLER may reach — which is not every operation.** Both
views here read `agent_operations()`, the same list the MCP server builds its
tools from, so the two surfaces expose exactly the same set. They used to read
`all_operations()`, which meant the three `internal=True` operations
(`catalogue_seed`, `tracker_import`, `stock_report_ingest`) were off the MCP
catalogue and still POST-able here by any signed-in user — the flag did half
its job. Seeds, bulk imports and ingests are an engineer's deliberate act
against one programme, run through a management command with a shell.

They stay in the registry, because those commands go through `call_operation`
directly and would otherwise lose the schema validation and provenance
stamping every other write gets. What changes is only what a request can
reach.
"""

import json
import logging

import jsonschema
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from connect_labs.labs.access.scopes import Caller
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import agent_operations, call_operation

logger = logging.getLogger(__name__)


def _access(request) -> SupplyDataAccess:
    token = (request.session.get("labs_oauth") or {}).get("access_token")
    return SupplyDataAccess(
        access_token=token,
        request=request,
        user=request.user,
        caller=Caller(request=request, user=request.user),
    )


def has_program_context(request) -> bool:
    """Whether the request carries a programme scope.

    Unlike solicitations' dual org/program scoping (see
    solicitations/views.py's _has_context), procurement's tenders, quotes,
    awards and purchases are programme-scoped ONLY: SupplyDataAccess's
    program_experiment property raises ValueError without a program_id, no
    matter what organization_id is set to. `labs_context = {}` -- no
    programme selected yet -- is a normal state on a fresh '/supply/' or
    '/supply/procurement/' visit, not a bug: check this before calling a
    programme-scoped operation and render a "select a programme" state
    instead of letting that ValueError raise uncaught through the view.
    """
    labs_context = getattr(request, "labs_context", {})
    return bool(labs_context.get("program_id"))


@method_decorator(login_required, name="dispatch")
class OperationListView(View):
    """Discovery: what can be done here, and with what arguments."""

    def get(self, request):
        return JsonResponse(
            {
                "operations": [
                    {
                        "name": operation.name,
                        "summary": operation.summary,
                        "input_schema": operation.input_schema,
                        "is_write": operation.is_write,
                    }
                    for operation in agent_operations().values()
                ]
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_required, name="dispatch")
class OperationDispatchView(View):
    def post(self, request, name):
        # One membership test, not "does it exist" followed by "may you reach
        # it". An internal operation and an unknown name get the SAME 404, so a
        # caller probing for what exists learns nothing from the difference —
        # and there is nothing here for them either way.
        if name not in agent_operations():
            raise Http404(f"no supply operation named {name!r}")

        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError as exc:
            return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)

        try:
            result = call_operation(name, _access(request), payload)
        except jsonschema.ValidationError as exc:
            return JsonResponse({"error": exc.message}, status=400)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"result": result}, safe=False)
