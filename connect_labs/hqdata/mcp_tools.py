"""The ``commcare_*`` MCP tools: a person's CommCare projects, for an agent or a script.

Every tool acts as the signed-in person and passes the same gate as the pages: a verified member of
the project (``access.may_use_project``), and a project the person is not in is "not found", the
same answer as one that does not exist. A token of the ``commcare`` scope reaches exactly these tools
(``token_scopes.COMMCARE_TOOLS``); a test pins that the two lists are the same.
"""

from __future__ import annotations

import re
from datetime import datetime

from django.db.models.fields.json import KeyTransform
from django.utils.dateparse import parse_datetime

from connect_labs.audit_trail.models import Action
from connect_labs.hqdata.access import is_project_admin, may_use_project, visible_projects
from connect_labs.hqdata.audit import project_denied, project_event
from connect_labs.hqdata.computed import ComputedWarming
from connect_labs.hqdata.models import HQProject, HQSyncRun
from connect_labs.hqdata.pipelines import (
    PipelineProblem,
    delete_pipeline,
    find_pipeline,
    list_pipelines,
    run_pipeline,
    save_pipeline,
)
from connect_labs.hqdata.projects import Refused, register_project, set_storage
from connect_labs.hqdata.stores import StoreUnavailable, store_for
from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none
from connect_labs.labs.progress import NULL_PROGRESS
from connect_labs.mcp.tool_registry import MCPToolError, register

MAX_ROWS = 200
_PROP_KEY = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _prove(user) -> None:
    """Re-prove the person's CommCare projects from their own CommCare connection, as the /commcare/ pages do on every
    visit (``web.decorators``). Without it, a person who works only through these tools lost every project a day
    after their last page visit (memberships age out after ``access.MEMBERSHIP_TTL``). ``sync_identity`` asks
    CommCare at most once per ``IDENTITY_MAX_AGE``; no connection, or CommCare not answering, proves nothing."""
    from connect_labs.hqdata.identity import sync_identity
    from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none

    token = cchq_token_or_none(user)
    if token is not None:
        sync_identity(user, token)


def _project(user, project_id) -> HQProject:
    _prove(user)
    project = HQProject.objects.filter(pk=project_id).first() if isinstance(project_id, int) else None
    if project is None or not may_use_project(user, project):
        project_denied(project_id if isinstance(project_id, int) else None, user, kind="mcp")
        raise MCPToolError("NOT_FOUND", "No such CommCare project, or you are not a member of it.")
    return project


def _admin(user, project: HQProject) -> None:
    if not is_project_admin(user, project):
        raise MCPToolError("PERMISSION_DENIED", "Only a project admin can do that.")


def _summary(project: HQProject) -> dict:
    return {
        "id": project.pk,
        "domain": project.domain,
        "storage_mode": project.storage_mode,
        "store": project.store,
        "status": project.status,
        "last_synced_at": project.last_synced_at.isoformat() if project.last_synced_at else None,
        "last_error": project.last_error,
    }


def _when(value, name: str) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise MCPToolError("INVALID_SCHEMA", f"{name} must be an ISO 8601 date and time.")
    return parsed


def _limit(value) -> int:
    try:
        return max(1, min(int(value if value is not None else 50), MAX_ROWS))
    except (TypeError, ValueError):
        raise MCPToolError("INVALID_SCHEMA", "limit must be a number.")


def _offset(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        raise MCPToolError("INVALID_SCHEMA", "offset must be a number.")


def _path(obj, path: str):
    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _fields(fields) -> list[str]:
    if fields is None:
        return []
    if not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields) or len(fields) > 50:
        raise MCPToolError("INVALID_SCHEMA", "fields must be a list of up to 50 dotted paths.")
    return fields


_PROJECT_ID = {"type": "integer", "description": "The project's id, from commcare_project_list."}


@register(
    name="commcare_project_list",
    description="The CommCare projects you belong to: id, domain, where its data is kept and how its sync is going.",
    input_schema={"type": "object", "properties": {}},
)
def commcare_project_list(user, **_):
    _prove(user)
    return {"projects": [_summary(p) for p in visible_projects(user).order_by("domain")]}


@register(
    name="commcare_project_get",
    description=(
        "One CommCare project: its sources and how far each has been read, recent syncs, "
        "and how many forms and cases are stored."
    ),
    input_schema={"type": "object", "properties": {"project_id": _PROJECT_ID}, "required": ["project_id"]},
)
def commcare_project_get(user, project_id=None, **_):
    project = _project(user, project_id)
    try:
        with store_for(project).session() as store:
            counts = {
                "forms": store.forms(project.pk).filter(deleted_at__isnull=True).count(),
                "cases": store.cases(project.pk).filter(deleted_at__isnull=True).count(),
            }
    except StoreUnavailable as exc:
        counts = {"unavailable": str(exc)}
    runs = HQSyncRun.objects.filter(source__project=project).select_related("source").order_by("-started_at")[:10]
    return {
        **_summary(project),
        "stored": counts,
        "sources": [
            {
                "id": s.pk,
                "kind": s.kind,
                "app_id": s.app_id,
                "xmlns": s.xmlns,
                "case_type": s.case_type,
                "enabled": s.enabled,
                "read_up_to": s.watermark.isoformat() if s.watermark else None,
                "first_read_complete": s.backfill_complete,
            }
            for s in project.sources.order_by("kind", "id")
        ],
        "recent_syncs": [
            {
                "started_at": r.started_at.isoformat(),
                "source": str(r.source),
                "mode": r.mode,
                "status": r.status,
                "rows": r.fetched,
                "error": r.error,
            }
            for r in runs
        ],
    }


@register(
    name="commcare_project_create",
    description=(
        "Add a CommCare project you belong to. CommCare must list it among your projects and let your account read "
        "its forms. Needs you to have signed in with CommCare at /commcare/login/ (or connected CommCare in labs) "
        "once."
    ),
    input_schema={"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
    is_write=True,
)
def commcare_project_create(user, domain="", **_):
    token = cchq_token_or_none(user)
    if token is None:
        raise MCPToolError("PERMISSION_DENIED", "Sign in with CommCare at /commcare/login/ first, then try again.")
    try:
        project = register_project(user, domain, token)
    except Refused as exc:
        raise MCPToolError("PERMISSION_DENIED", str(exc))
    return _summary(project)


@register(
    name="commcare_project_sync",
    description=(
        "Bring a project up to date from CommCare. Runs as a background job that a labs deploy does not lose: a "
        "large first read carries on as further jobs until it is done. Returns the result if it finishes within "
        "about half a minute, otherwise a job_id to follow with job_status."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "mode": {
                "type": "string",
                "enum": ["delta", "full", "reconcile"],
                "description": (
                    "delta (default) reads what changed; reconcile re-reads everything "
                    "and marks what CommCare deleted."
                ),
            },
        },
        "required": ["project_id"],
    },
    is_write=True,
    long_running=True,
    wants_progress=True,
)
def commcare_project_sync(user, project_id=None, mode=None, progress=NULL_PROGRESS, **_):
    from connect_labs.hqdata.jobs import run_sync

    project = HQProject.objects.filter(pk=project_id).first() if isinstance(project_id, int) else None
    # The person whose CommCare sign-in the project syncs with may always run it (that sign-in, which
    # CommCare checks on every call, is the authority, not a 24 hour membership stamp). Anyone else
    # must be a member.
    if project is None or (project.sync_owner_id != user.pk and not may_use_project(user, project)):
        project_denied(project_id if isinstance(project_id, int) else None, user, kind="sync")
        raise MCPToolError("NOT_FOUND", "No such CommCare project, or you are not a member of it.")
    if mode not in (None, "delta", "full", "reconcile"):
        raise MCPToolError("INVALID_SCHEMA", "mode must be delta, full or reconcile.")
    if mode in ("full", "reconcile"):
        _admin(user, project)
    if project.sync_owner_id is None or project.status == HQProject.STATUS_NEEDS_REAUTH:
        raise MCPToolError(
            "PERMISSION_DENIED",
            "Nobody's CommCare sign-in is attached to this project. Use the project page to attach yours.",
        )
    return {**_summary(project), **run_sync(project, user, mode, progress)}


@register(
    name="commcare_project_set_storage",
    description=(
        "Choose whether a project is kept and refreshed on a schedule ('persistent', using your CommCare sign-in) "
        "or fetched only when asked ('live'). Project admins only."
    ),
    input_schema={
        "type": "object",
        "properties": {"project_id": _PROJECT_ID, "storage_mode": {"type": "string", "enum": ["live", "persistent"]}},
        "required": ["project_id", "storage_mode"],
    },
    is_write=True,
)
def commcare_project_set_storage(user, project_id=None, storage_mode=None, **_):
    project = _project(user, project_id)
    _admin(user, project)
    try:
        return _summary(set_storage(project, user, storage_mode or ""))
    except Refused as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))


@register(
    name="commcare_forms",
    description=(
        f"Form submissions a project has stored, newest last, up to {MAX_ROWS} a call. Narrow by xmlns, app_id, "
        "username or received_on; `fields` picks values out of each form by dotted path "
        "(form.meta.username, form.question)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "xmlns": {"type": "string"},
            "app_id": {"type": "string"},
            "username": {"type": "string"},
            "received_after": {"type": "string", "description": "ISO 8601"},
            "received_before": {"type": "string", "description": "ISO 8601"},
            "include_archived": {"type": "boolean", "default": False},
            "fields": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 50},
            "offset": {"type": "integer", "default": 0},
        },
        "required": ["project_id"],
    },
)
def commcare_forms(
    user,
    project_id=None,
    xmlns="",
    app_id="",
    username="",
    received_after=None,
    received_before=None,
    include_archived=False,
    fields=None,
    limit=None,
    offset=None,
    **_,
):
    project = _project(user, project_id)
    paths, limit, offset = _fields(fields), _limit(limit), _offset(offset)
    try:
        with store_for(project).session() as store:
            qs = store.forms(project.pk).filter(deleted_at__isnull=True)
            if not include_archived:
                qs = qs.filter(archived=False)
            if xmlns:
                qs = qs.filter(xmlns=xmlns)
            if app_id:
                qs = qs.filter(app_id=app_id)
            if username:
                qs = qs.filter(username=username)
            after, before = _when(received_after, "received_after"), _when(received_before, "received_before")
            if after:
                qs = qs.filter(received_on__gte=after)
            if before:
                qs = qs.filter(received_on__lt=before)
            total = qs.count()
            rows = list(qs.order_by("received_on", "form_id")[offset : offset + limit])
    except StoreUnavailable as exc:
        raise MCPToolError("UPSTREAM_ERROR", str(exc))
    project_event(Action.LIST, project.pk, user=user, kind="forms", record_count=len(rows))
    return {
        "total": total,
        "offset": offset,
        "forms": [
            {
                "form_id": r.form_id,
                "xmlns": r.xmlns,
                "app_id": r.app_id,
                "username": r.username,
                "received_on": r.received_on.isoformat() if r.received_on else None,
                "archived": r.archived,
                "values": {p: _path(r.form_json, p) for p in paths} if paths else None,
            }
            for r in rows
        ],
    }


@register(
    name="commcare_cases",
    description=(
        f"Cases a project has stored, up to {MAX_ROWS} a call. Narrow by case_type, owner_id, closed, or exact "
        "case properties; `fields` picks values out of each case by dotted path "
        "(properties.age, indices.parent.case_id)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "case_type": {"type": "string"},
            "owner_id": {"type": "string"},
            "closed": {"type": "boolean"},
            "properties": {"type": "object", "description": 'Exact matches, e.g. {"village": "X"}'},
            "fields": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 50},
            "offset": {"type": "integer", "default": 0},
        },
        "required": ["project_id"],
    },
)
def commcare_cases(
    user,
    project_id=None,
    case_type="",
    owner_id="",
    closed=None,
    properties=None,
    fields=None,
    limit=None,
    offset=None,
    **_,
):
    project = _project(user, project_id)
    paths, limit, offset = _fields(fields), _limit(limit), _offset(offset)
    if properties is not None and (not isinstance(properties, dict) or len(properties) > 10):
        raise MCPToolError("INVALID_SCHEMA", "properties must be an object of up to 10 exact matches.")
    for key in properties or {}:
        if not _PROP_KEY.match(key):
            raise MCPToolError("INVALID_SCHEMA", f"{key!r} is not a usable property name.")
    try:
        with store_for(project).session() as store:
            qs = store.cases(project.pk).filter(deleted_at__isnull=True)
            if case_type:
                qs = qs.filter(case_type=case_type)
            if owner_id:
                qs = qs.filter(owner_id=owner_id)
            if closed is not None:
                qs = qs.filter(closed=bool(closed))
            for i, (key, value) in enumerate((properties or {}).items()):
                # KeyTransform, not ``properties__<key>``: a property named "in", "regex" or
                # "exact" would otherwise be read as a lookup on the whole JSON column.
                qs = qs.annotate(**{f"_prop{i}": KeyTransform(key, "properties")}).filter(**{f"_prop{i}": value})
            total = qs.count()
            rows = list(qs.order_by("date_opened", "case_id")[offset : offset + limit])
    except StoreUnavailable as exc:
        raise MCPToolError("UPSTREAM_ERROR", str(exc))
    out = []
    for r in rows:
        case = {
            "case_id": r.case_id,
            "case_type": r.case_type,
            "case_name": r.case_name,
            "owner_id": r.owner_id,
            "closed": r.closed,
            "date_opened": r.date_opened.isoformat() if r.date_opened else None,
            "properties": r.properties,
            "indices": r.indices,
        }
        if paths:
            case = {**case, "values": {p: _path(case, p) for p in paths}}
        out.append(case)
    project_event(Action.LIST, project.pk, user=user, kind="cases", record_count=len(out))
    return {"total": total, "offset": offset, "cases": out}


def _pipeline(user, project: HQProject, pipeline_id=None, name=None):
    record = find_pipeline(user, project, pipeline_id=pipeline_id, name=name)
    if record is None:
        raise MCPToolError("NOT_FOUND", "No such pipeline in this project.")
    return record


@register(
    name="commcare_pipeline_list",
    description="The pipelines saved for a CommCare project.",
    input_schema={"type": "object", "properties": {"project_id": _PROJECT_ID}, "required": ["project_id"]},
)
def commcare_pipeline_list(user, project_id=None, **_):
    project = _project(user, project_id)
    return {
        "pipelines": [
            {
                "id": p.id,
                "name": p.name,
                "store_kind": p.store_kind,
                "app_id": p.app_id,
                "xmlns": p.xmlns,
                "case_type": p.case_type,
                "schema": p.schema,
            }
            for p in list_pipelines(user, project)
        ]
    }


@register(
    name="commcare_pipeline_save",
    description=(
        "Create or replace a pipeline over a project's forms or cases (the schema shape workflows use: fields with "
        "a path and an aggregation, a grouping_key, a terminal_stage). Field paths start form. for forms and "
        "case. for cases. Where the data comes from is the project and the narrowing given here; a "
        "data_source in the schema is ignored."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "name": {"type": "string"},
            "store_kind": {"type": "string", "enum": ["forms", "cases"]},
            "schema": {"type": "object"},
            "app_id": {"type": "string"},
            "xmlns": {"type": "string"},
            "case_type": {"type": "string"},
        },
        "required": ["project_id", "name", "store_kind", "schema"],
    },
    is_write=True,
)
def commcare_pipeline_save(
    user, project_id=None, name="", store_kind="", schema=None, app_id="", xmlns="", case_type="", **_
):
    project = _project(user, project_id)
    try:
        record = save_pipeline(
            user,
            project,
            name=name,
            store_kind=store_kind,
            schema=schema,
            app_id=app_id,
            xmlns=xmlns,
            case_type=case_type,
        )
    except PipelineProblem as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    return {"id": record.id, "name": record.name}


@register(
    name="commcare_pipeline_run",
    description=(
        "Run a saved pipeline over a project's stored data and return its rows (up to 500). "
        "Name the pipeline by id or name."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "pipeline_id": {"type": "integer"},
            "name": {"type": "string"},
            "fresh": {"type": "boolean", "default": False, "description": "Recompute instead of using a kept result."},
        },
        "required": ["project_id"],
    },
)
def commcare_pipeline_run(user, project_id=None, pipeline_id=None, name=None, fresh=False, **_):
    project = _project(user, project_id)
    pipeline = _pipeline(user, project, pipeline_id, name)
    try:
        result = run_pipeline(user, project, pipeline, force_refresh=bool(fresh))
    except PipelineProblem as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    except StoreUnavailable as exc:
        raise MCPToolError("UPSTREAM_ERROR", str(exc))
    except ComputedWarming as exc:
        raise MCPToolError("UPSTREAM_ERROR", f"{exc.message} Ask again in a few minutes.")
    project_event(Action.READ, project.pk, user=user, kind="pipeline", record_count=result["count"])
    return {
        "count": result["count"],
        "truncated": result["truncated"],
        "columns": result["columns"],
        "rows": result["rows"],
    }


@register(
    name="commcare_pipeline_delete",
    description="Delete a saved pipeline. Its author or a project admin only.",
    input_schema={
        "type": "object",
        "properties": {"project_id": _PROJECT_ID, "pipeline_id": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["project_id"],
    },
    is_write=True,
)
def commcare_pipeline_delete(user, project_id=None, pipeline_id=None, name=None, **_):
    project = _project(user, project_id)
    pipeline = _pipeline(user, project, pipeline_id, name)
    try:
        delete_pipeline(user, project, pipeline, is_admin=is_project_admin(user, project))
    except PermissionError as exc:
        raise MCPToolError("PERMISSION_DENIED", str(exc))
    return {"deleted": True}
