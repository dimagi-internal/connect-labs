"""Audit reviews API, called by the audit workflow's render code through the sandbox bridge.

    GET  /labs/audit-reviews/api/<workflow>/world/?opportunity_id=<opp>      sync, then the page's answer
    POST /labs/audit-reviews/api/<workflow>/decisions/?opportunity_id=<opp>  {"decisions": [...]}
    POST /labs/audit-reviews/api/<workflow>/events/?opportunity_id=<opp>     {"review_key", "id", "type", "payload"}
                                             type: action | comment | pin | unpin | owner
    GET  /labs/audit-reviews/api/<workflow>/link-preview/?url=<https link>&opportunity_id=<opp>

Who may: a signed-in Dimagi staff member (audit reviews are for Dimagi staff only) who can read the
audit workflow as themselves, in a scope they may use. The workflow must declare the report it
follows (``config.read_workflows``), as any reader of another workflow's runs must. A sandboxed call
may only name its own workflow.

Anonymous callers get a bare 401 rather than a redirect: these answer JSON only.
"""

from __future__ import annotations

import json
import logging

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from connect_labs.audit_reviews import service
from connect_labs.utils.dimagi_user import is_dimagi_staff

logger = logging.getLogger(__name__)


def _display_name(user) -> str:
    return (getattr(user, "name", "") or getattr(user, "username", "") or "Staff").strip()[:200]


def _scope(request) -> dict:
    from connect_labs.workflow.views import _coerce_int, _validated_opportunity_id, _validated_program_id

    opp = _validated_opportunity_id(request, _coerce_int(request.GET.get("opportunity_id")))
    if opp:
        return {"opportunity_id": opp}
    program = _validated_program_id(request, _coerce_int(request.GET.get("program_id")))
    return {"program_id": program} if program else {}


def _open(request, workflow_id: int):
    """(source, dao, definition) for this request, or (None, response, None) when refused.

    The caller must close the dao."""
    from connect_labs.workflow import declared_reads
    from connect_labs.workflow.data_access import WorkflowDataAccess

    if not request.user.is_authenticated:
        return None, HttpResponse(status=401), None
    if not is_dimagi_staff(request.user):
        return None, JsonResponse({"error": "Audit reviews are for Dimagi staff."}, status=403), None
    framed_for = declared_reads.reader(request)
    if framed_for is not None and framed_for != workflow_id:
        return None, JsonResponse({"error": "Not this workflow's reviews."}, status=403), None
    scope = _scope(request)
    if not scope:
        return None, JsonResponse({"error": "opportunity_id or program_id is required."}, status=400), None
    try:
        dao = WorkflowDataAccess(request=request, **scope)
    except ValueError:
        return None, HttpResponse(status=401), None
    definition = dao.get_definition(workflow_id)
    if definition is None:
        dao.close()
        return None, JsonResponse({"error": "No such workflow."}, status=404), None
    try:
        source = service.open_source(definition, **scope)
    except service.Refused as e:
        dao.close()
        return None, JsonResponse({"error": str(e)}, status=400), None
    if source.source_workflow_id not in declared_reads.readable_definition_ids(definition):
        dao.close()
        return None, JsonResponse({"error": "The workflow does not declare the report it follows."}, status=403), None
    return source, dao, definition


def _body(request) -> dict:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _opportunity_names(request, payload: dict) -> dict:
    """Names of the opportunities in these reviews, from the viewer's own Labs session (KC Pulse's runs
    carry ids only). Only opportunities the viewer's session lists; others stay as ids."""
    session = getattr(request, "session", None) or {}
    org_data = (session.get("labs_oauth") or {}).get("organization_data") or {}
    names = {
        str(o.get("id")): o.get("name") for o in org_data.get("opportunities") or [] if o.get("id") and o.get("name")
    }
    wanted = {str(o.get("id")) for o in ((payload.get("D") or {}).get("source") or {}).get("opportunities") or []}
    return {k: v for k, v in names.items() if k in wanted}


@require_GET
def world_api(request, workflow_id: int):
    source, dao, definition = _open(request, workflow_id)
    if source is None:
        return dao
    step = "reading KC Pulse's runs"
    try:
        service.sync(source, dao, definition)
        step = "working out the reviews"
        payload = service.world(source)
    except Exception as e:
        logger.exception("audit_reviews: could not build the world for workflow %s (%s)", workflow_id, step)
        # Staff only: say which step failed and how, so a report of it can be acted on.
        return JsonResponse(
            {"error": f"Could not read the reviews: failed while {step} ({type(e).__name__}).", "step": step},
            status=500,
        )
    finally:
        dao.close()
    return JsonResponse(
        {
            **payload,
            "opportunity_names": _opportunity_names(request, payload),
            "owner_candidates": service.owner_candidates(source),
            "me": {"id": request.user.pk, "name": service.owner_name(request.user)},
        }
    )


@require_GET
def link_preview_api(request, workflow_id: int):
    """A link's title for a comment's card: {"url", "title"} (title null for a private page)."""
    from connect_labs.audit_reviews import links

    source, dao, _definition = _open(request, workflow_id)
    if source is None:
        return dao
    dao.close()
    # A person pasting links reads a handful; this keeps the endpoint from being a fetcher.
    key = f"audit_reviews:link-rate:{request.user.pk}"
    used = cache.get_or_set(key, 0, 60)
    if used >= 60:
        return JsonResponse({"error": "Too many link previews; try again in a minute."}, status=429)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, 60)
    try:
        return JsonResponse(links.preview(request.GET.get("url") or ""))
    except links.Refused as e:
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def decisions_api(request, workflow_id: int):
    source, dao, _definition = _open(request, workflow_id)
    if source is None:
        return dao
    dao.close()
    try:
        body = _body(request)
        done = service.record_decisions(
            source,
            body.get("decisions"),
            user=request.user,
            by=_display_name(request.user),
            run_id=body.get("run_id"),
        )
    except service.Refused as e:
        return JsonResponse({"error": e.args[0] if e.args else "Refused."}, status=409)
    return JsonResponse({"recorded": done, "world": service.world(source)})


@require_POST
def events_api(request, workflow_id: int):
    source, dao, _definition = _open(request, workflow_id)
    if source is None:
        return dao
    dao.close()
    body = _body(request)
    try:
        made = service.record_event(
            source,
            review_key=str(body.get("review_key") or ""),
            event_id=str(body.get("id") or ""),
            type=str(body.get("type") or ""),
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            user=request.user,
            by=_display_name(request.user),
        )
    except service.Refused as e:
        return JsonResponse({"error": e.args[0] if e.args else "Refused."}, status=409)
    return JsonResponse({"recorded": made, "world": service.world(source)})


def _settings_call(request, workflow_id: int, do):
    source, dao, _definition = _open(request, workflow_id)
    if source is None:
        return dao
    dao.close()
    body = _body(request)
    try:
        out = do(source, body)
    except service.Refused as e:
        return JsonResponse({"error": e.args[0] if e.args else "Refused."}, status=409)
    return JsonResponse(out)


@require_POST
def settings_preview_api(request, workflow_id: int):
    """What the reviews would look like under these settings. Saves nothing."""
    return _settings_call(
        request, workflow_id, lambda source, body: service.preview_settings(source, body.get("settings"))
    )


@require_POST
def settings_save_api(request, workflow_id: int):
    def do(source, body):
        saved = service.save_settings(
            source,
            body.get("settings"),
            user=request.user,
            by=_display_name(request.user),
            note=str(body.get("note") or ""),
            expected_version=body.get("expected_version"),
        )
        return {"saved": saved, "world": service.world(source)}

    return _settings_call(request, workflow_id, do)


@require_POST
def settings_undo_api(request, workflow_id: int):
    def do(source, body):
        saved = service.undo_settings(
            source, user=request.user, by=_display_name(request.user), expected_version=body.get("expected_version")
        )
        return {"saved": saved, "world": service.world(source)}

    return _settings_call(request, workflow_id, do)
