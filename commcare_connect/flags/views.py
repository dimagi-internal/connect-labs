"""HTTP endpoints for Flags.

Endpoints are scoped under a workflow run id because every Flag belongs
to one run. The URL prefix is mounted by commcare_connect/workflow/urls.py
at /labs/workflow/api/<int:workflow_run_id>/flags/.

Flags are auto-applied on report mount via view.ensureAutoFlags(...) in
render code, which POSTs anything not already persisted for the run.
Manual flags can also be POSTed by render code if a template exposes a
"flag this row" UI.
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from commcare_connect.flags.data_access import FlagsDataAccess
from commcare_connect.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)


def _refuse_if_run_completed(request: HttpRequest, workflow_run_id: int) -> JsonResponse | None:
    """Return a JsonResponse if the run is missing or completed; else None.

    Completed runs are immutable; any flag-write attempt is rejected here
    so render code that mistakenly tries to write against a saved-run sees
    a clear server-side refusal instead of silently corrupting state.
    """
    da = WorkflowDataAccess(request=request)
    run = da.get_run(workflow_run_id)
    if run is None:
        return JsonResponse({"error": f"Workflow run {workflow_run_id} not found"}, status=404)
    if run.is_completed:
        return JsonResponse(
            {"error": f"Workflow run {workflow_run_id} is completed; flags are read-only"},
            status=409,
        )
    return None


@csrf_exempt
@require_http_methods(["POST"])
def create_flag_for_run(request: HttpRequest, workflow_run_id: int) -> JsonResponse:
    """POST /labs/workflow/api/<workflow_run_id>/flags/

    Body (JSON): {opportunity_id, flw_id, flag_key, flag_label?,
                  evidence?, source?}
    """
    refusal = _refuse_if_run_completed(request, workflow_run_id)
    if refusal is not None:
        return refusal

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    da = FlagsDataAccess(request=request, opportunity_id=body.get("opportunity_id"))
    try:
        flag = da.create_flag(
            workflow_run_id=workflow_run_id,
            opportunity_id=body["opportunity_id"],
            flw_id=body.get("flw_id", ""),
            flag_key=body.get("flag_key", ""),
            flag_label=body.get("flag_label"),
            evidence=body.get("evidence"),
            source=body.get("source", "auto"),
            flagged_by=getattr(request.user, "username", None),
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except KeyError as exc:
        return JsonResponse({"error": f"Missing required field: {exc.args[0]}"}, status=400)

    return JsonResponse(
        {
            "id": flag.id,
            "workflow_run_id": flag.workflow_run_id,
            "flw_id": flag.flw_id,
            "flag_key": flag.flag_key,
            "flag_label": flag.flag_label,
            "flagged_at": flag.flagged_at,
        },
        status=201,
    )


@require_http_methods(["GET"])
def list_flags_for_run(request: HttpRequest, workflow_run_id: int) -> JsonResponse:
    """GET /labs/workflow/api/<workflow_run_id>/flags/

    Returns: {"count": N, "flags": [...]}
    """
    da = FlagsDataAccess(request=request)
    flags = da.get_flags_for_run(workflow_run_id)
    return JsonResponse(
        {
            "count": len(flags),
            "flags": [
                {
                    "id": f.id,
                    "workflow_run_id": f.workflow_run_id,
                    "opportunity_id": f.data.get("opportunity_id"),
                    "flw_id": f.flw_id,
                    "flag_key": f.flag_key,
                    "flag_label": f.flag_label,
                    "evidence": f.evidence,
                    "source": f.source,
                    "flagged_at": f.flagged_at,
                    "flagged_by": f.flagged_by,
                }
                for f in flags
            ],
        }
    )
