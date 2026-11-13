"""The /hq/ pages: a person's CommCare projects, their sync, and what is stored."""

from __future__ import annotations

import json

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render as django_render
from django.views.decorators.http import require_POST

from connect_labs.hqdata.access import (
    DOMAIN_RE,
    ProjectAccessDenied,
    create_project,
    may_use_project,
    visible_projects,
)
from connect_labs.hqdata.identity import hq_host
from connect_labs.hqdata.models import HQIdentity, HQPipeline, HQProject, HQProjectMember, HQSource, HQSyncRun
from connect_labs.hqdata.pipelines import PipelineProblem, config_for, run_pipeline
from connect_labs.hqdata.tasks import sync_project_task
from connect_labs.hqdata.web.decorators import hq_login_required
from connect_labs.labs.integrations.commcare.api_client import CommCareDataAccess


def render(request, template, context=None, **kwargs):
    """Every /hq/ page without the Connect shell: no sidenav, no Connect context selector."""
    return django_render(request, template, {"present_mode": True, **(context or {})}, **kwargs)


def _project_for(request: HttpRequest, project_id: int) -> HQProject:
    """The project, or a 404: a project the person cannot use is indistinguishable from one that is not there."""
    project = HQProject.objects.filter(pk=project_id).first()
    if project is None or not may_use_project(request.user, project):
        raise Http404
    return project


def _is_admin(request: HttpRequest, project: HQProject) -> bool:
    return HQProjectMember.objects.filter(project=project, user=request.user, role=HQProjectMember.ROLE_ADMIN).exists()


def _ensure_default_sources(project: HQProject) -> None:
    for kind in (HQSource.KIND_FORMS, HQSource.KIND_CASES):
        if not project.sources.filter(kind=kind).exists():
            HQSource.objects.create(project=project, kind=kind)


@hq_login_required
def home(request: HttpRequest) -> HttpResponse:
    projects = visible_projects(request.user).order_by("domain")
    identity = HQIdentity.objects.filter(user=request.user).first()
    registered = set(HQProject.objects.filter(hq_host=hq_host()).values_list("domain", flat=True))
    unregistered = sorted(d for d in (identity.domains if identity else []) if d.lower() not in registered)
    return render(
        request, "hqdata/home.html", {"projects": projects, "identity": identity, "unregistered": unregistered}
    )


@hq_login_required
@require_POST
def project_create(request: HttpRequest) -> HttpResponse:
    domain = (request.POST.get("domain") or "").strip().lower()
    if not DOMAIN_RE.match(domain):
        messages.error(request, "Enter a CommCare project name: lower-case letters, digits and hyphens.")
        return redirect("hq:home")
    identity = HQIdentity.objects.filter(user=request.user).first()
    if identity is None or domain not in {d.lower() for d in identity.domains}:
        # Cheap and before any call to HQ: only a domain CommCare lists for this person is even tried.
        messages.error(request, f"CommCare does not list {domain} among your projects.")
        return redirect("hq:home")
    try:
        project = create_project(
            request.user,
            domain,
            hq_host=hq_host(),
            can_read_domain=lambda: CommCareDataAccess(
                None, domain, cchq_access_token=request.hq_token
            ).verify_hq_access(),
        )
    except ProjectAccessDenied:
        messages.error(
            request,
            f"CommCare would not let your account read forms in {domain}. "
            "Reading data needs the Edit Data permission.",
        )
        return redirect("hq:home")
    _ensure_default_sources(project)
    messages.success(request, f"{domain} is added. Sync it to bring its data in.")
    return redirect("hq:project_detail", project_id=project.pk)


@hq_login_required
def project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    project = _project_for(request, project_id)
    sources = list(project.sources.order_by("kind", "app_id", "xmlns", "case_type"))
    runs = HQSyncRun.objects.filter(source__project=project).select_related("source").order_by("-started_at")[:15]
    from connect_labs.hqdata.models import HQCase, HQForm

    return render(
        request,
        "hqdata/project.html",
        {
            "project": project,
            "sources": sources,
            "runs": runs,
            "is_admin": _is_admin(request, project),
            "members": project.members.select_related("user").order_by("role", "user__name"),
            "form_count": HQForm.objects.filter(project_id=project.pk, deleted_at__isnull=True).count(),
            "case_count": HQCase.objects.filter(project_id=project.pk, deleted_at__isnull=True).count(),
            "sync_owner_is_me": project.sync_owner_id == request.user.pk,
        },
    )


_ADMIN_ONLY = {"set_storage", "pause", "resume", "add_source", "toggle_source", "delete_project", "full_resync"}


@hq_login_required
@require_POST
def project_action(request: HttpRequest, project_id: int) -> HttpResponse:
    project = _project_for(request, project_id)
    action = request.POST.get("action", "")
    if action in _ADMIN_ONLY and not _is_admin(request, project):
        messages.error(request, "Only a project admin can do that.")
        return redirect("hq:project_detail", project_id=project.pk)
    back = redirect("hq:project_detail", project_id=project.pk)

    if action == "sync_now":
        if project.sync_owner_id is None or project.status == HQProject.STATUS_NEEDS_REAUTH:
            messages.error(request, "Nobody's CommCare sign-in is attached to this project. Use mine, below.")
            return back
        sync_project_task.delay(project.pk)
        messages.success(request, "Sync queued. Refresh in a moment.")
    elif action == "full_resync":
        sync_project_task.delay(project.pk, HQSyncRun.MODE_RECONCILE)
        messages.success(request, "A full re-read is queued. It also marks anything CommCare has since deleted.")
    elif action == "take_over":
        HQProject.objects.filter(pk=project.pk).update(sync_owner=request.user, status=HQProject.STATUS_ACTIVE)
        messages.success(request, "Scheduled syncs will now use your CommCare sign-in.")
    elif action == "set_storage":
        mode = request.POST.get("storage_mode")
        if mode not in (HQProject.STORAGE_LIVE, HQProject.STORAGE_PERSISTENT):
            messages.error(request, "Unknown storage choice.")
            return back
        updates = {"storage_mode": mode}
        if mode == HQProject.STORAGE_PERSISTENT:
            # Whose token a scheduled sync uses must be said out loud, and it is the person turning it on.
            updates["sync_owner"] = request.user
            if project.status == HQProject.STATUS_NEEDS_REAUTH:
                updates["status"] = HQProject.STATUS_ACTIVE
            _ensure_default_sources(project)
        HQProject.objects.filter(pk=project.pk).update(**updates)
        messages.success(
            request,
            (
                "Kept and refreshed on a schedule, using your CommCare sign-in."
                if mode == HQProject.STORAGE_PERSISTENT
                else "Fetched when asked. Anything already stored stays until you delete it."
            ),
        )
    elif action in ("pause", "resume"):
        HQProject.objects.filter(pk=project.pk).update(
            status=HQProject.STATUS_PAUSED if action == "pause" else HQProject.STATUS_ACTIVE
        )
    elif action == "add_source":
        kind = request.POST.get("kind")
        fields = {"kind": kind, "app_id": "", "xmlns": "", "case_type": ""}
        if kind == HQSource.KIND_FORMS:
            fields["app_id"] = (request.POST.get("app_id") or "").strip()
            fields["xmlns"] = (request.POST.get("xmlns") or "").strip()
        elif kind == HQSource.KIND_CASES:
            fields["case_type"] = (request.POST.get("case_type") or "").strip()
        else:
            messages.error(request, "Choose forms or cases.")
            return back
        try:
            with transaction.atomic():
                HQSource.objects.create(project=project, **fields)
        except IntegrityError:
            messages.error(request, "That source is already set up.")
        else:
            messages.success(request, "Source added.")
    elif action == "toggle_source":
        source = project.sources.filter(pk=request.POST.get("source_id")).first()
        if source is None:
            raise Http404
        source.enabled = not source.enabled
        source.save(update_fields=["enabled"])
    elif action == "delete_project":
        if (request.POST.get("confirm_domain") or "").strip().lower() != project.domain:
            messages.error(request, "Type the project name to confirm. Nothing was deleted.")
            return back
        project.delete()
        messages.success(request, f"{project.domain} and everything stored for it is deleted.")
        return redirect("hq:home")
    else:
        messages.error(request, "Unknown action.")
    return back


# -- pipelines ----------------------------------------------------------------

_EXAMPLE_SCHEMA = {
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}],
}


def _pipeline_for(request: HttpRequest, project: HQProject, pipeline_id: int) -> HQPipeline:
    pipeline = project.pipelines.filter(pk=pipeline_id).first()
    if pipeline is None:
        raise Http404
    return pipeline


def _apply_pipeline_form(request: HttpRequest, pipeline: HQPipeline) -> str | None:
    """Copy the posted fields onto ``pipeline``. Returns what is wrong with them, or None."""
    name = (request.POST.get("name") or "").strip()
    kind = request.POST.get("store_kind") or pipeline.store_kind
    if not name:
        return "Give the pipeline a name."
    if kind not in (HQSource.KIND_FORMS, HQSource.KIND_CASES):
        return "Choose forms or cases."
    try:
        schema = json.loads(request.POST.get("schema") or "")
    except json.JSONDecodeError as exc:
        return f"The schema is not valid JSON: {exc}"
    if not isinstance(schema, dict):
        return "The schema must be a JSON object."
    pipeline.name, pipeline.store_kind, pipeline.schema = name, kind, schema
    pipeline.app_id = (request.POST.get("app_id") or "").strip() if kind == HQSource.KIND_FORMS else ""
    pipeline.xmlns = (request.POST.get("xmlns") or "").strip() if kind == HQSource.KIND_FORMS else ""
    pipeline.case_type = (request.POST.get("case_type") or "").strip() if kind == HQSource.KIND_CASES else ""
    try:
        config_for(pipeline)
    except PipelineProblem as exc:
        return str(exc)
    return None


@hq_login_required
def pipeline_list(request: HttpRequest, project_id: int) -> HttpResponse:
    project = _project_for(request, project_id)
    draft = HQPipeline(project=project, store_kind=HQSource.KIND_FORMS, schema=_EXAMPLE_SCHEMA)
    if request.method == "POST":
        problem = _apply_pipeline_form(request, draft)
        if problem is None and project.pipelines.filter(name=draft.name).exists():
            problem = "A pipeline with that name already exists."
        if problem is None:
            draft.created_by = request.user
            draft.save()
            messages.success(request, "Pipeline saved.")
            return redirect("hq:pipeline_detail", project_id=project.pk, pipeline_id=draft.pk)
        messages.error(request, problem)
    return render(
        request,
        "hqdata/pipelines.html",
        {
            "project": project,
            "pipelines": project.pipelines.all(),
            "draft": draft,
            "draft_schema": request.POST.get("schema") or json.dumps(_EXAMPLE_SCHEMA, indent=2),
        },
    )


@hq_login_required
def pipeline_detail(request: HttpRequest, project_id: int, pipeline_id: int) -> HttpResponse:
    project = _project_for(request, project_id)
    pipeline = _pipeline_for(request, project, pipeline_id)
    result, error = None, None
    schema_text = json.dumps(pipeline.schema, indent=2)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            if not (_is_admin(request, project) or pipeline.created_by_id == request.user.pk):
                messages.error(request, "Only the person who saved it, or a project admin, can delete it.")
            else:
                pipeline.delete()
                messages.success(request, "Pipeline deleted.")
                return redirect("hq:pipeline_list", project_id=project.pk)
        elif action in ("save", "run"):
            schema_text = request.POST.get("schema") or schema_text
            problem = _apply_pipeline_form(request, pipeline)
            if problem is not None:
                error = problem
                pipeline.refresh_from_db()
            else:
                if action == "save":
                    pipeline.save()
                    messages.success(request, "Pipeline saved.")
                    return redirect("hq:pipeline_detail", project_id=project.pk, pipeline_id=pipeline.pk)
                try:
                    result = run_pipeline(request.user, pipeline, force_refresh=bool(request.POST.get("fresh")))
                except PipelineProblem as exc:
                    error = str(exc)
                except Exception as exc:  # shown, not swallowed: the person wrote this schema
                    error = f"{type(exc).__name__}: {exc}"
    return render(
        request,
        "hqdata/pipeline.html",
        {"project": project, "pipeline": pipeline, "schema_text": schema_text, "result": result, "error": error},
    )
