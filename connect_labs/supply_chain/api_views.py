"""HTTP adapter. One view dispatches every operation, so there is no
per-endpoint glue that can drift from the MCP surface.
"""

import json
import logging

import jsonschema
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import all_operations, call_operation, get_operation

logger = logging.getLogger(__name__)


def _access(request) -> SupplyDataAccess:
    token = (request.session.get("labs_oauth") or {}).get("access_token")
    return SupplyDataAccess(access_token=token, request=request)


def has_program_context(request) -> bool:
    """Whether the request carries a programme scope.

    Unlike solicitations' dual org/program scoping (see
    solicitations/views.py's _has_context), procurement's rounds, quotes,
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
                    for operation in all_operations().values()
                ]
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_required, name="dispatch")
class OperationDispatchView(View):
    def post(self, request, name):
        try:
            get_operation(name)
        except KeyError as exc:
            raise Http404(f"no procurement operation named {name!r}") from exc

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
