"""The run page's door to a workflow's actions (workflow/actions.py).

The same preview → commit as the MCP's ``workflow_run_action``: a button's click
previews, the runner shows the preview in its own dialog, and the person's confirm
commits it. The run is read in the run's own scope, stamped on the URL by the run
page (``?opportunity_id=`` / ``?program_id=``) — the same way the run's other
endpoints are.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from connect_labs.workflow.actions import ActionError, commit, preview
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.models import WorkflowActionExecution

logger = logging.getLogger(__name__)


def _body(request) -> dict:
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _run_and_definition(request, run_id):
    wda = WorkflowDataAccess(request=request)
    run = wda.get_run(run_id)
    if run is None:
        wda.close()
        return None, None, None
    definition = wda.get_definition((run.data or {}).get("definition_id"))
    if definition is None:
        wda.close()
        return None, None, None
    return wda, run, definition


def _refusal(e: ActionError) -> JsonResponse:
    return JsonResponse(
        {"error": e.public_message, "code": e.code}, status=409 if e.code.startswith("confirm") else 400
    )


@login_required
@require_POST
def action_preview_api(request, run_id, key):
    wda, run, definition = _run_and_definition(request, run_id)
    if run is None:
        return JsonResponse({"error": "Workflow run not found"}, status=404)
    try:
        return JsonResponse(
            preview(
                request.user,
                wda=wda,
                run=run,
                definition=definition,
                key=key,
                arguments=_body(request).get("arguments") or {},
                request=request,
            )
        )
    except ActionError as e:
        return _refusal(e)
    finally:
        wda.close()


@login_required
@require_POST
def action_run_api(request, run_id, key):
    body = _body(request)
    wda, run, definition = _run_and_definition(request, run_id)
    if run is None:
        return JsonResponse({"error": "Workflow run not found"}, status=404)
    try:
        execution = commit(
            request.user,
            wda=wda,
            run=run,
            definition=definition,
            key=key,
            arguments=body.get("arguments") or {},
            confirm=body.get("confirm") or "",
            via="page",
            request=request,
        )
    except ActionError as e:
        return _refusal(e)
    finally:
        wda.close()
    return JsonResponse({"execution": execution.as_dict()}, status=202)


@login_required
@require_GET
def action_execution_api(request, execution_id):
    """An action run's progress — only the person it ran as may read it."""
    execution = WorkflowActionExecution.objects.filter(pk=execution_id, user=request.user).first()
    if execution is None:
        return JsonResponse({"error": "Not found"}, status=404)
    return JsonResponse({"execution": execution.as_dict()})
