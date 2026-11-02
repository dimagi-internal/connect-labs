"""Making and ending public links to a run, from its row on the workflow list.

This is the only step with an access rule of its own. Making a link copies a run out from
behind every ACL, so whoever makes one must be Dimagi staff, must be able to read the run
in its scope with their own token (the ACL), and must have access to every opportunity
its snapshot holds. See public_shares.py.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from connect_labs.labs.integrations.connect.api_client import LabsAPIError
from connect_labs.utils.dimagi_user import is_dimagi_staff
from connect_labs.workflow import public_shares
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.models import PublicRunShare
from connect_labs.workflow.render_source import resolve_render_code
from connect_labs.workflow.templates import SnapshotTooLargeError

logger = logging.getLogger(__name__)


def share_list_context(user) -> dict:
    """What the workflow list needs for Share on each saved run's row."""
    if not is_dimagi_staff(user):
        return {"can_share_runs": False, "shared_run_ids": set()}
    return {"can_share_runs": True, "shared_run_ids": public_shares.live_run_ids()}


def snapshot_opportunity_ids(definition, run) -> list[int]:
    """Every opportunity the copy would hold, whatever the run's own scope is: a
    multi-opportunity snapshot can carry other organizations' rows and workers."""
    snapshot = run.snapshot or {}
    found = {run.opportunity_id, *(snapshot.get("opportunity_ids") or [])}
    found.update((definition.data or {}).get("opportunity_ids") or [])
    for pipeline in (snapshot.get("pipelines") or {}).values():
        found.update(row.get("opportunity_id") for row in (pipeline or {}).get("rows") or [] if isinstance(row, dict))
    found.update(w.get("opportunity_id") for w in snapshot.get("workers") or [] if isinstance(w, dict))
    return sorted(int(o) for o in found if isinstance(o, int) or (isinstance(o, str) and o.isdigit()))


def _opportunities_without_access(user, opportunity_ids: list[int]) -> list[int]:
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools.synthetic import _require_opportunity_access

    missing = []
    for opportunity_id in opportunity_ids:
        try:
            _require_opportunity_access(user, opportunity_id)
        except MCPToolError:
            missing.append(opportunity_id)
    return missing


def _public_url(request, token: str) -> str:
    path = reverse("labs:workflow:public_run", kwargs={"token": token})
    base = getattr(settings, "LABS_PUBLIC_URL", "")
    return f"{base.rstrip('/')}{path}" if base else request.build_absolute_uri(path)


def _row(request, share: PublicRunShare) -> dict:
    return {
        "id": share.pk,
        "url": _public_url(request, share.token),
        "created_at": share.created_at.isoformat(),
        "expires_at": share.expires_at.isoformat(),
        "view_count": share.view_count,
        "last_viewed_at": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "created_by": share.created_by.username if share.created_by else "",
    }


@login_required
@require_http_methods(["GET", "POST"])
def public_shares_api(request, run_id):
    if not is_dimagi_staff(request.user):
        return JsonResponse({"error": "Only Dimagi staff can share a run publicly."}, status=403)
    data_access = WorkflowDataAccess(request=request)
    try:
        try:
            run = data_access.get_run(run_id)
        except LabsAPIError as e:
            if e.status_code != 404:
                raise
            run = None
        if run is None:
            return JsonResponse({"error": "Run not found"}, status=404)
        if request.method == "GET":
            return _list(request, data_access, run)
        return _create(request, data_access, run)
    except LabsAPIError:
        logger.exception("Could not share run %s", run_id)
        return JsonResponse({"error": "Could not read this run from Connect. Please try again."}, status=502)
    finally:
        data_access.close()


def _refused_for_opportunities(user, definition, run):
    """A link opens the whole copy, so seeing one takes what making one takes."""
    missing = _opportunities_without_access(user, snapshot_opportunity_ids(definition, run))
    if not missing:
        return None
    names = ", ".join(str(o) for o in missing)
    return JsonResponse(
        {"error": f"This run holds data from opportunities you do not have access to: {names}."}, status=403
    )


def _list(request, data_access, run):
    definition = data_access.get_definition(run.definition_id)
    if definition is None:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    refused = _refused_for_opportunities(request.user, definition, run)
    if refused:
        return refused
    shares = public_shares.live_for_run(run.id, run.opportunity_id, run.program_id)
    return JsonResponse({"shares": [_row(request, s) for s in shares]})


def _create(request, data_access, run):
    try:
        body = json.loads(request.body or b"{}")
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if body.get("confirmed") is not True:
        return JsonResponse({"error": "Confirm that anyone with the link can see this run."}, status=400)
    if not run.is_completed or not run.snapshot:
        return JsonResponse({"error": "Only a saved run can be shared."}, status=400)
    try:
        expires_at = public_shares.resolve_expiry(body.get("days"), body.get("until"))
    except ValueError:
        return JsonResponse({"error": "Choose 1, 7, 30 or 90 days, or a date within the next year."}, status=400)
    definition = data_access.get_definition(run.definition_id)
    if definition is None:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    refused = _refused_for_opportunities(request.user, definition, run)
    if refused:
        return refused
    render_code, _source = resolve_render_code(data_access, definition)
    if not render_code:
        return JsonResponse({"error": "This workflow has nothing to draw."}, status=400)
    try:
        share = public_shares.create(request.user, definition, run, render_code, expires_at)
    except SnapshotTooLargeError:
        return JsonResponse({"error": "This run is too large to share by link."}, status=400)
    return JsonResponse({"share": _row(request, share)}, status=201)


@login_required
@require_POST
def end_public_share_api(request, share_id):
    """Whoever could make a link for this run can end it; so can the person who made it,
    if the run has since gone and can no longer be read."""
    share = PublicRunShare.objects.defer("payload").filter(pk=share_id).first()
    if share is None or not is_dimagi_staff(request.user):
        return JsonResponse({"error": "Not found"}, status=404)
    if share.created_by_id != request.user.pk and not _can_read_run(request, share):
        return JsonResponse({"error": "Not found"}, status=404)
    public_shares.end(share, PublicRunShare.EndedReason.ENDED, request.user)
    return JsonResponse({"success": True})


def _can_read_run(request, share: PublicRunShare) -> bool:
    data_access = WorkflowDataAccess(
        access_token=request.session.get("labs_oauth", {}).get("access_token"),
        user=request.user,
        opportunity_id=share.opportunity_id,
        program_id=share.program_id,
    )
    try:
        return data_access.get_run(share.run_id) is not None
    except LabsAPIError:
        return False
    finally:
        data_access.close()
