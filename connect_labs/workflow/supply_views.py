"""The two endpoints behind a workflow's supply sources (supply_sources.py).

`api/<id>/supply-data/` loads every eager source for the run page (the `supply`
prop); `api/<id>/supply-query/` runs one source on demand
(`actions.querySupply`). Both run as the signed-in viewer, over the workflow's
own opportunities only.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from connect_labs.workflow import supply_sources
from connect_labs.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)


def _as_of(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise supply_sources.SupplySourceError(f"as_of {value!r} is not a date")


def _definition_and_opps(request, definition_id):
    data_access = WorkflowDataAccess(request=request)
    try:
        definition = data_access.get_definition(definition_id)
    finally:
        data_access.close()
    if not definition:
        return None, []
    context_opp = (getattr(request, "labs_context", None) or {}).get("opportunity_id")
    spanned = [int(o) for o in (definition.opportunity_ids or [])]
    if not spanned and context_opp:
        spanned = [int(context_opp)]
    return definition, spanned


@login_required
@require_GET
def supply_data_api(request, definition_id):
    definition, spanned = _definition_and_opps(request, definition_id)
    if definition is None:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    try:
        data = supply_sources.load(
            request, definition, opportunity_ids=spanned, as_of=_as_of(request.GET.get("as_of"))
        )
    except supply_sources.SupplySourceError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except Exception:
        logger.exception("supply data failed for workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    return JsonResponse({"supply": data})


@login_required
@require_POST
def supply_query_api(request, definition_id):
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)
    definition, spanned = _definition_and_opps(request, definition_id)
    if definition is None:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    opportunity_id = body.get("opportunity_id")
    if opportunity_id is not None:
        if int(opportunity_id) not in spanned:
            return JsonResponse(
                {"error": f"workflow {definition_id} does not span opportunity {opportunity_id}"}, status=403
            )
        spanned = [int(opportunity_id)]
    try:
        spec = supply_sources.find(definition, str(body.get("alias") or ""))
        result = supply_sources.run(
            request,
            definition,
            spec,
            opportunity_ids=spanned,
            as_of=_as_of(body.get("as_of") or request.GET.get("as_of")),
            args=body.get("args") or {},
        )
    except supply_sources.SupplySourceError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except Exception:
        logger.exception("supply query failed for workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    return JsonResponse(result)
