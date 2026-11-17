"""A CommCare project's workflows, drawn under /commcare/ for a person with no Connect login.

Only the page kind (no runs) and only GET: a page is drawn by the workflow runner in page mode, in
the sandbox (``workflow/sandbox.py``), and reads the one endpoint below as the viewer. The project
comes from the URL and is checked by ``hqdata.workflows.project_dao`` on every request; nothing
here reads ``labs_context``, ``?opportunity_id`` or a Connect token.
"""

from __future__ import annotations

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods

from connect_labs.hqdata.models import HQProject, HQProjectMember
from connect_labs.hqdata.web.decorators import hq_login_required
from connect_labs.hqdata.web.views import _project_for, render
from connect_labs.hqdata.workflows import (
    PageNotFound,
    PageProblem,
    ProjectWorkflowsUnavailable,
    delete_run,
    ensure_project_dashboard,
    get_run,
    list_runs,
    load_page,
    read_project_pipeline_data,
    save_run,
    workflow_dao,
)
from connect_labs.workflow.page_mode import RUN_ENDPOINTS, blank_instance, is_page
from connect_labs.workflow.sandbox import host_frame_src, host_payload, labs_origin


def _open(request: HttpRequest, project_id: int):
    project = _project_for(request, project_id)
    try:
        return project, workflow_dao(request.user, project)
    except ProjectWorkflowsUnavailable as exc:
        raise _Unavailable(project, str(exc)) from exc


class _Unavailable(Exception):
    def __init__(self, project: HQProject, message: str):
        self.project, self.message = project, message


def _unavailable(request: HttpRequest, exc: _Unavailable) -> HttpResponse:
    return render(request, "hqdata/workflow.html", {"project": exc.project, "error": exc.message}, status=409)


def _instance_for(definition, run) -> dict:
    """The blank in-memory instance a page has, or a saved run as a completed one (state and snapshot as saved)."""
    if run is None:
        return blank_instance(definition.id, None, None, [])
    data = run.data or {}
    return {
        "id": run.id,
        "definition_id": definition.id,
        "opportunity_id": None,
        "program_id": None,
        "opportunity_ids": [],
        "name": (data.get("state") or {}).get("name", ""),
        "status": data.get("status") or "completed",
        "state": data.get("state") or {},
        "period_start": data.get("period_start"),
        "period_end": data.get("period_end"),
        "completed_at": data.get("completed_at"),
        "snapshot": data.get("snapshot"),
    }


def build_workflow_data(
    request: HttpRequest, project: HQProject, definition, render_code: str, *, data_url: str | None, run=None
) -> dict:
    """The ``workflow-data`` the runner reads to draw one of a project's pages.

    ``data_url`` is the ONE endpoint the page reads; every other ``apiEndpoints`` entry is ``None``, so
    nothing reaches a ``/labs/...`` route. Shared by the ``/commcare/`` page and the embed frame.

    With ``run`` (a saved run) the page is drawn from the data that run froze: it arrives with the page
    (``pipeline_data``, and the run as ``instance`` with its snapshot, completed), nothing is read live, so
    ``data_url`` is None.
    """
    user = request.user
    api = {
        "getWorkers": None,
        "getPipelineData": data_url,
        "authStatus": None,
        "streamPipelineData": None,
        "queryPipelineRows": None,
        "getWorkflowData": None,
        "queryWorkflow": None,
        "getSupplyData": None,
        "querySupply": None,
        "updateOpportunityIds": None,
        **dict.fromkeys(RUN_ENDPOINTS),
    }
    return {
        "definition": definition.data,
        "definition_id": definition.id,
        "opportunity_id": None,
        "program_id": None,
        "program_scoped": False,
        "opportunity_ids": [],
        "multi_opp": False,
        "render_code": render_code,
        "instance": _instance_for(definition, run),
        "is_edit_mode": False,
        "is_page": True,
        "workers": [],
        "pipeline_data": ((run.data or {}).get("snapshot") or {}).get("pipelines") or {} if run is not None else {},
        "flags": [],
        "audits": [],
        "tasks": [],
        "links": {"auditUrlBase": "", "taskUrlBase": ""},
        "apiEndpoints": api,
        "viewer_is_staff": False,
        "scope": {
            "type": "project",
            "key": str(project.pk),
            "organization": None,
            "program": None,
            "opportunity": None,
            "user": {"username": user.username, "name": getattr(user, "name", "") or ""},
            "programs": [],
            "opportunities": [],
            "settings_url": None,
        },
        "config": {},
        "sandbox": {**host_payload(request), "readable_definitions": []},
        "mapboxToken": settings.MAPBOX_TOKEN or "",
    }


@hq_login_required
@require_http_methods(["GET", "POST"])
def workflow_list(request: HttpRequest, project_id: int) -> HttpResponse:
    """The project's pages. POST makes the overview dashboard if the project has none, then opens it."""
    try:
        project, dao = _open(request, project_id)
    except _Unavailable as exc:
        return _unavailable(request, exc)
    dao.close()
    if request.method == "POST":
        definition_id = ensure_project_dashboard(request.user, project)
        return redirect("hq:workflow_page", project_id=project.pk, definition_id=definition_id)
    dao = workflow_dao(request.user, project)
    try:
        pages = [d for d in dao.list_definitions() if is_page(d)]
    finally:
        dao.close()
    return render(request, "hqdata/workflows.html", {"project": project, "pages": pages})


@hq_login_required
@require_GET
def workflow_page(request: HttpRequest, project_id: int, definition_id: int) -> HttpResponse:
    try:
        project, dao = _open(request, project_id)
    except _Unavailable as exc:
        return _unavailable(request, exc)
    dao.close()
    loaded = load_page(request.user, project, definition_id)
    if loaded is None:
        raise Http404  # a project's runs are not served here, only its pages
    definition, render_code = loaded
    run = None
    if request.GET.get("run"):
        try:
            run = get_run(request.user, project, definition.id, int(request.GET["run"]))
        except (ValueError, PageNotFound):
            raise Http404 from None  # not a saved run of THIS page in THIS project
    workflow_data = build_workflow_data(
        request,
        project,
        definition,
        render_code,
        data_url=None if run else f"/commcare/projects/{project.pk}/workflows/{definition.id}/pipeline-data/",
        run=run,
    )
    response = render(
        request,
        "hqdata/workflow.html",
        {
            "project": project,
            "definition": definition,
            "render_code": render_code,
            "workflow_data": workflow_data,
            "sandboxed": True,
            "has_context": True,
            "run": run,
            "runs": list_runs(request.user, project, definition.id),
            "is_admin": HQProjectMember.objects.filter(
                project=project, user=request.user, role=HQProjectMember.ROLE_ADMIN
            ).exists(),
        },
    )
    # The enforced policy the workflow page sets: the sandboxed frame may be navigated only to its shell.
    response["Content-Security-Policy"] = host_frame_src(labs_origin(request))
    response.csp_extra_policy = True
    return response


@hq_login_required
@require_http_methods(["POST"])
def workflow_runs(request: HttpRequest, project_id: int, definition_id: int) -> HttpResponse:
    """Save a run of the page (freeze its data now), or delete one; then show it."""
    from django.contrib import messages

    try:
        project, dao = _open(request, project_id)
    except _Unavailable as exc:
        return _unavailable(request, exc)
    dao.close()
    page_url = redirect("hq:workflow_page", project_id=project.pk, definition_id=definition_id)
    try:
        if request.POST.get("action") == "delete":
            is_admin = HQProjectMember.objects.filter(
                project=project, user=request.user, role=HQProjectMember.ROLE_ADMIN
            ).exists()
            delete_run(request.user, project, definition_id, int(request.POST.get("run") or 0), is_admin=is_admin)
            messages.success(request, "Saved run deleted.")
            return page_url
        run = save_run(
            request.user,
            project,
            definition_id,
            period_start=request.POST.get("period_start") or None,
            period_end=request.POST.get("period_end") or None,
            name=request.POST.get("name") or "",
        )
    except PageNotFound:
        raise Http404 from None
    except (PageProblem, PermissionError) as exc:
        messages.error(request, str(exc))
        return page_url
    return redirect(f"{page_url['Location']}?run={run['id']}")


@hq_login_required
@require_GET
def workflow_pipeline_data(request: HttpRequest, project_id: int, definition_id: int) -> JsonResponse:
    try:
        project, dao = _open(request, project_id)
    except _Unavailable as exc:
        return JsonResponse({"error": exc.message}, status=409)
    try:
        definition = dao.get_definition(definition_id)
    finally:
        dao.close()
    if definition is None or not is_page(definition):
        raise Http404
    return JsonResponse(read_project_pipeline_data(request.user, project, definition_id))
