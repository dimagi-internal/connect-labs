"""HTTP endpoints for Decisions.

Endpoints are scoped under a workflow run id because every Decision belongs
to one run. The URL prefix is mounted by commcare_connect/workflow/urls.py
at /labs/workflow/api/<int:workflow_run_id>/decisions/.
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from commcare_connect.decisions.data_access import DecisionsDataAccess
from commcare_connect.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)


def _refuse_if_run_completed(request: HttpRequest, workflow_run_id: int) -> JsonResponse | None:
    """Return a JsonResponse if the run is missing or completed; else None.

    Completed runs are immutable; any decision-write attempt is rejected
    here so render code that mistakenly tries to write against a saved-run
    sees a clear server-side refusal instead of silently corrupting state.
    """
    da = WorkflowDataAccess(request=request)
    run = da.get_run(workflow_run_id)
    if run is None:
        return JsonResponse({"error": f"Workflow run {workflow_run_id} not found"}, status=404)
    if run.is_completed:
        return JsonResponse(
            {"error": f"Workflow run {workflow_run_id} is completed; decisions are read-only"},
            status=409,
        )
    return None


@csrf_exempt
@require_http_methods(["POST"])
def create_decision_for_run(request: HttpRequest, workflow_run_id: int) -> JsonResponse:
    """POST /labs/workflow/api/<workflow_run_id>/decisions/

    Body (JSON): {opportunity_id, flw_id, decision_type, reason_key?,
                  reason_label?, kpi_snapshot?, audit_session_ids?,
                  task_ids?, notes?}
    """
    refusal = _refuse_if_run_completed(request, workflow_run_id)
    if refusal is not None:
        return refusal

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    da = DecisionsDataAccess(request=request, opportunity_id=body.get("opportunity_id"))
    try:
        decision = da.create_decision(
            workflow_run_id=workflow_run_id,
            opportunity_id=body["opportunity_id"],
            flw_id=body.get("flw_id", ""),
            decision_type=body.get("decision_type", "no_issues"),
            reason_key=body.get("reason_key"),
            reason_label=body.get("reason_label"),
            kpi_snapshot=body.get("kpi_snapshot"),
            audit_session_ids=body.get("audit_session_ids"),
            task_ids=body.get("task_ids"),
            notes=body.get("notes"),
            decided_by=getattr(request.user, "username", None),
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except KeyError as exc:
        return JsonResponse({"error": f"Missing required field: {exc.args[0]}"}, status=400)

    return JsonResponse(
        {
            "id": decision.id,
            "workflow_run_id": decision.workflow_run_id,
            "flw_id": decision.flw_id,
            "decision_type": decision.decision_type,
            "reason_key": decision.reason_key,
            "decided_at": decision.decided_at,
        },
        status=201,
    )
