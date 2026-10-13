"""The run page's door to a workflow's actions (workflow/actions.py), and a page's.

The same preview → commit as the MCP's ``workflow_run_action``: a button's click
previews, the runner shows the preview in its own dialog, and the person's confirm
commits it. The run is read in the run's own scope, stamped on the URL by the run
page (``?opportunity_id=`` / ``?program_id=``) — the same way the run's other
endpoints are.

A PAGE has no run: its actions act within the page's scope (``actions.PageScope``),
at ``api/<definition_id>/page-actions/<key>/``. The page is read where it lives (the
owner scope the page stamps on the URL, as its other endpoints are), must be a page,
and the workers it may name are those of the opportunities it covers -- its own
``opportunity_ids`` and the ones under its owner the viewer can see, from the
viewer's own org data. Nothing the caller sends widens that.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from connect_labs.workflow.actions import ActionError, PageScope, briefing_source, commit, preview
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


def _page_and_scope(request, definition_id):
    """(wda, PageScope, definition) for a page's action, or a refusal response."""
    from connect_labs.scope_config.service import scope_in_view
    from connect_labs.workflow.page_mode import is_page, page_scope

    scope = scope_in_view(getattr(request, "labs_context", None) or {})
    if scope is None or scope.type == "user":
        return None, JsonResponse({"error": "A page's action is run in the page's own scope."}, status=400)
    wda = WorkflowDataAccess(request=request)
    definition = wda.get_definition(definition_id)
    if definition is None:
        wda.close()
        return None, JsonResponse({"error": "Page not found"}, status=404)
    if not is_page(definition):
        wda.close()
        # A workflow's actions act on one of its runs (api/run/<id>/actions/).
        return None, JsonResponse(
            {"error": "This is a workflow with runs: run its actions on a run.", "code": "not_a_page"}, status=400
        )
    # The page must LIVE in the scope it is acted in: with no scope the read filter
    # can fall back to public records (a labs-only organisation sends none), so the
    # read alone does not prove where the page is.
    from connect_labs.labs.context import get_org_data
    from connect_labs.workflow.page_mode import page_owner_scope

    owner = page_owner_scope(definition, get_org_data(request) or {})
    if owner is None or (owner.type, str(owner.key)) != (scope.type, str(scope.key)):
        wda.close()
        return None, JsonResponse(
            {"error": "This page does not live in the scope its action was asked in.", "code": "wrong_scope"},
            status=403,
        )
    prop = page_scope(request, scope)
    # What is under the page's owner, in the viewer's own org data; the page's own
    # opportunity_ids (author-written) can only NARROW that, never add to it.
    under = []
    for oid in [o.get("id") for o in prop.get("opportunities") or []] + [(prop.get("opportunity") or {}).get("id")]:
        try:
            n = int(oid)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in under:
            under.append(n)
    listed = {int(o) for o in (definition.opportunity_ids or []) if str(o).isdigit()}
    reach = [n for n in under if n in listed] if listed else under
    subject = PageScope(
        definition_id=int(definition.id),
        scope_type=scope.type,
        scope_key=str(scope.key),
        opportunity_ids=tuple(reach),
        opportunity_id=scope.id if scope.type == "opportunity" else None,
        program_id=scope.id if scope.type == "program" else None,
    )
    return (wda, subject, definition), None


def _briefing(request, wda, run, definition):
    """The run's grading for a coaching briefing, read in the scope the page stamped on
    the URL (falling back to the run's own)."""
    scope = {}
    for name in ("opportunity_id", "program_id"):
        value = request.GET.get(name)
        if value and value.isdigit():
            scope[name] = int(value)
            break
    return briefing_source(request.user, wda, run, definition, **scope)


def _refusal(e: ActionError) -> JsonResponse:
    return JsonResponse(
        {"error": e.public_message, "code": e.code},
        status=409 if e.code.startswith("confirm") else 403 if e.code == "forbidden" else 400,
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
                briefing=_briefing(request, wda, run, definition),
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
            briefing=_briefing(request, wda, run, definition),
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


@login_required
@require_POST
def page_action_preview_api(request, definition_id, key):
    found, refusal = _page_and_scope(request, definition_id)
    if refusal is not None:
        return refusal
    wda, subject, definition = found
    try:
        return JsonResponse(
            preview(
                request.user,
                wda=wda,
                run=subject,
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
def page_action_run_api(request, definition_id, key):
    body = _body(request)
    found, refusal = _page_and_scope(request, definition_id)
    if refusal is not None:
        return refusal
    wda, subject, definition = found
    try:
        execution = commit(
            request.user,
            wda=wda,
            run=subject,
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
