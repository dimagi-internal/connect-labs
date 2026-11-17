"""The /commcare/ pages: a person's CommCare projects, their sync, and what is stored."""

from __future__ import annotations

import json

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render as django_render
from django.views.decorators.http import require_POST

from connect_labs.hqdata.access import may_use_project, visible_projects
from connect_labs.hqdata.audit import project_denied
from connect_labs.hqdata.external import SECURE_SSLMODES, check_connection
from connect_labs.hqdata.jobs import queue_sync
from connect_labs.hqdata.models import (
    HQExternalDatabase,
    HQIdentity,
    HQPipeline,
    HQProject,
    HQProjectMember,
    HQSource,
    HQSyncRun,
)
from connect_labs.hqdata.netguard import HostRefused, check_host, check_port
from connect_labs.hqdata.pipelines import PipelineProblem, config_for, run_pipeline
from connect_labs.hqdata.projects import Refused, register_project, set_storage
from connect_labs.hqdata.store_schema import SCHEMA_NAME_RE
from connect_labs.hqdata.store_switch import SwitchRefused, switch_store
from connect_labs.hqdata.web.decorators import ProjectNotYours, hq_login_required


def render(request, template, context=None, **kwargs):
    """Every CommCare page, in the standard labs layout (sidebar, header) for anyone who has a Connect login.

    Only a person who reaches labs through CommCare alone gets the bare layout: labs' header and sidebar read
    their Connect organisations and opportunities, which such a person does not have.
    """
    from connect_labs.hqdata.access import is_commcare_only

    return django_render(
        request, template, {"present_mode": is_commcare_only(request.user), **(context or {})}, **kwargs
    )


def _project_for(request: HttpRequest, project_id: int) -> HQProject:
    """The project, or a 404: a project the person cannot use is indistinguishable from one that is not there."""
    project = HQProject.objects.filter(pk=project_id).first()
    if project is None or not may_use_project(request.user, project):
        project_denied(project_id, request.user, kind="page")
        raise ProjectNotYours
    return project


def _is_admin(request: HttpRequest, project: HQProject) -> bool:
    return HQProjectMember.objects.filter(project=project, user=request.user, role=HQProjectMember.ROLE_ADMIN).exists()


@hq_login_required
def home(request: HttpRequest) -> HttpResponse:
    projects = visible_projects(request.user).order_by("domain")
    identity = HQIdentity.objects.filter(user=request.user).first()
    # Every project CommCare lists for the person that they are not a member of here yet: ones nobody has
    # added AND ones someone else already added (adding one of those joins it; register_project proves
    # the person's own access either way). Offering only unregistered ones left a second person unable
    # to reach a project a colleague had added.
    mine = {p.domain.lower() for p in projects}
    joinable = sorted(d for d in (identity.domains if identity else []) if d.lower() not in mine)
    return render(request, "hqdata/home.html", {"projects": projects, "identity": identity, "unregistered": joinable})


@hq_login_required
@require_POST
def project_create(request: HttpRequest) -> HttpResponse:
    domain = (request.POST.get("domain") or "").strip().lower()
    try:
        project = register_project(request.user, domain, request.hq_token)
    except Refused as exc:
        messages.error(request, str(exc))
        return redirect("hq:home")
    messages.success(request, f"{project.domain} is added. Sync it to bring its data in.")
    return redirect("hq:project_detail", project_id=project.pk)


@hq_login_required
def project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    project = _project_for(request, project_id)
    sources = list(project.sources.order_by("kind", "app_id", "xmlns", "case_type"))
    runs = HQSyncRun.objects.filter(source__project=project).select_related("source").order_by("-started_at")[:15]
    from connect_labs.hqdata.stores import StoreUnavailable, store_for

    try:
        with store_for(project).session() as store:
            form_count = store.forms(project.pk).filter(deleted_at__isnull=True).count()
            case_count = store.cases(project.pk).filter(deleted_at__isnull=True).count()
        store_problem = ""
    except StoreUnavailable as exc:
        form_count = case_count = None
        store_problem = str(exc)

    return render(
        request,
        "hqdata/project.html",
        {
            "project": project,
            "sources": sources,
            "runs": runs,
            "is_admin": _is_admin(request, project),
            "members": project.members.select_related("user").order_by("role", "user__name"),
            "form_count": form_count,
            "case_count": case_count,
            "store_problem": store_problem,
            "external": HQExternalDatabase.objects.filter(project=project).first(),
            "db_modes": HQExternalDatabase.SSLMODE_CHOICES,
            "sync_owner_is_me": project.sync_owner_id == request.user.pk,
        },
    )


def _queue(request: HttpRequest, project: HQProject, success: str, mode: str | None = None) -> None:
    if project.sync_owner_id is None or project.status == HQProject.STATUS_NEEDS_REAUTH:
        messages.error(request, "Nobody's CommCare sign-in is attached to this project. Use mine, below.")
        return
    try:
        reply = queue_sync(project, user=request.user, mode=mode, actor="page")
    except Refused as exc:
        messages.error(request, str(exc))
        return
    messages.success(
        request, "A sync is already running for this project." if reply.get("already_running") else success
    )


_ADMIN_ONLY = {
    "take_over",
    "set_storage",
    "pause",
    "resume",
    "add_source",
    "toggle_source",
    "delete_project",
    "full_resync",
    "save_database",
    "check_database",
    "use_database",
    "use_labs",
    "remove_database",
}
_CONNECTION_FIELDS = ("host", "port", "dbname", "schema", "username")


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
        _queue(request, project, "Sync queued. Refresh in a moment.")
    elif action == "full_resync":
        _queue(
            request,
            project,
            "A full re-read is queued. It also marks anything CommCare has since deleted.",
            mode=HQSyncRun.MODE_RECONCILE,
        )
    elif action == "take_over":
        # Taking over is the way out of "needs sign-in", never out of a pause an admin chose.
        changes = {"sync_owner": request.user}
        if project.status != HQProject.STATUS_PAUSED:
            changes["status"] = HQProject.STATUS_ACTIVE
        HQProject.objects.filter(pk=project.pk).update(**changes)
        messages.success(request, "Scheduled syncs will now use your CommCare sign-in.")
    elif action == "set_storage":
        try:
            set_storage(project, request.user, request.POST.get("storage_mode") or "")
        except Refused as exc:
            messages.error(request, str(exc))
            return back
        messages.success(
            request,
            (
                "Kept and refreshed on a schedule, using your CommCare sign-in."
                if request.POST.get("storage_mode") == HQProject.STORAGE_PERSISTENT
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
    elif action == "save_database":
        _save_database(request, project)
    elif action == "check_database":
        record = HQExternalDatabase.objects.filter(project=project).first()
        if record is None:
            messages.error(request, "Save the database details first.")
        else:
            for check in check_connection(record):
                (messages.success if check.ok else messages.error)(
                    request, f"{check.name}{': ' + check.detail if check.detail else ''}"
                )
    elif action in ("use_database", "use_labs"):
        try:
            outcome = switch_store(
                project, HQProject.STORE_EXTERNAL if action == "use_database" else HQProject.STORE_LABS
            )
        except SwitchRefused as exc:
            messages.error(request, str(exc))
        else:
            if outcome["changed"]:
                _queue(
                    request,
                    project,
                    "The project now keeps its data "
                    + ("in your database" if action == "use_database" else "in labs")
                    + ". Everything is being read from CommCare again; this takes a while for a large project.",
                )
    elif action == "remove_database":
        record = HQExternalDatabase.objects.filter(project=project).first()
        if record is None:
            messages.error(request, "There is no database to remove.")
        elif project.store == HQProject.STORE_EXTERNAL:
            messages.error(request, "The project is keeping its data there. Move it back to labs first.")
        else:
            record.delete()
            messages.success(request, "The database details are removed.")
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


def _save_database(request: HttpRequest, project: HQProject) -> None:
    post = request.POST
    record = HQExternalDatabase.objects.filter(project=project).first()
    fresh = {
        "host": (post.get("host") or "").strip().lower(),
        "port": (post.get("port") or "5432").strip(),
        "dbname": (post.get("dbname") or "").strip(),
        "schema": (post.get("schema") or "labs_commcare").strip().lower(),
        "username": (post.get("username") or "").strip(),
        "sslmode": post.get("sslmode") or "verify-full",
    }
    password = post.get("password") or ""
    try:
        port = check_port(fresh["port"])
        check_host(fresh["host"], allow_private=bool(record and record.allow_private_network))
    except HostRefused as exc:
        messages.error(request, str(exc))
        return
    if not SCHEMA_NAME_RE.match(fresh["schema"]):
        messages.error(request, "The schema name may only use lower-case letters, digits and underscores.")
        return
    if not (fresh["dbname"] and fresh["username"]):
        messages.error(request, "The database name and user are needed.")
        return
    if fresh["sslmode"] not in SECURE_SSLMODES:
        messages.error(request, "Labs only connects with the database's certificate verified.")
        return
    if record is None and not password:
        messages.error(request, "Enter the database password.")
        return
    if (
        record is not None
        and project.store == HQProject.STORE_EXTERNAL
        and any(str(getattr(record, f)) != str(port if f == "port" else fresh[f]) for f in _CONNECTION_FIELDS)
    ):
        messages.error(
            request,
            "The project is keeping its data in this database, so only the password and the "
            "certificate setting can change. Move it back to labs first to point it somewhere else.",
        )
        return
    record = record or HQExternalDatabase(project=project)
    record.host, record.port, record.dbname = fresh["host"], port, fresh["dbname"]
    record.schema, record.username, record.sslmode = fresh["schema"], fresh["username"], fresh["sslmode"]
    if password:
        record.password = password
    record.verified_at = None
    record.save()
    messages.success(request, "Saved. Run the check to make sure labs can use it.")


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
            if (
                problem is None
                and HQPipeline.objects.filter(project=project, name=pipeline.name).exclude(pk=pipeline.pk).exists()
            ):
                problem = "A pipeline with that name already exists."
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


# -- API tokens ---------------------------------------------------------------


@hq_login_required
def tokens(request: HttpRequest) -> HttpResponse:
    """Mint and revoke the person's CommCare-scope MCP tokens (``token_scopes.COMMCARE``).

    A token made here reaches the ``commcare_*`` tools and nothing else, so it is something a person with
    no Connect login may hold. The raw token is shown once, in the response that creates it.
    """
    from connect_labs.mcp import token_scopes
    from connect_labs.mcp.models import MCPAccessToken

    raw = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = (request.POST.get("name") or "").strip()[:100] or "CommCare access"
            try:
                days = int(request.POST.get("days") or 90)
            except ValueError:
                days = 90
            days = max(1, min(days, MCPAccessToken.MAX_TTL_DAYS))
            _token, raw = MCPAccessToken.create_token(request.user, name, days, scope=token_scopes.COMMCARE)
        elif action == "revoke":
            MCPAccessToken.objects.filter(
                pk=request.POST.get("token_id"), user=request.user, scope=token_scopes.COMMCARE
            ).update(is_active=False)
            messages.success(request, "Token revoked.")
    mine = MCPAccessToken.objects.filter(user=request.user, scope=token_scopes.COMMCARE, is_active=True).order_by(
        "-created_at"
    )
    return render(
        request,
        "hqdata/tokens.html",
        {
            "tokens": mine,
            "raw_token": raw,
            "mcp_url": request.build_absolute_uri("/mcp/"),
            "max_days": MCPAccessToken.MAX_TTL_DAYS,
        },
    )
