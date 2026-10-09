"""Workflow sources: a page (or workflow) reading another workflow's runs, as the viewer.

Declared on the definition, beside `pipeline_sources` and `supply_sources`:

    "workflow_sources": [
        {"alias": "review", "workflow": 8481, "read": "latest_run", "opportunity_id": 10113},
        {"alias": "report", "workflow": 8301, "read": "saved_runs", "program_id": 10112},
    ]

`read` is `latest_run` (the newest run, open or completed), `saved_runs` (completed
runs, newest first) or `summary` (the workflow's name and description, its run count
and newest run). The owner scope (`opportunity_id` / `program_id` / `organization_id`)
says where the target's records live; without one the request's own scope is used.

Every read runs as the viewer, through the target's own LabsRecord access: a page
can show a report's latest run only to someone who could open that report. A
source the viewer cannot read comes back as `{"error": ...}` under its alias, so
one unreadable source never blanks the page. A completed run carries its
`snapshot_summary` when it has one -- never the snapshot itself, which is fetched
only by the run's own page.

Served by `api/<id>/workflow-data/` (every source, the `workflows` prop) and
`api/<id>/workflow-query/` (one source, `actions.queryWorkflow`). Both refuse an
alias the definition does not declare.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from connect_labs.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)

READS = ("latest_run", "saved_runs", "summary")
OWNER_KEYS = ("opportunity_id", "program_id", "organization_id")
MAX_RUNS = 20


class WorkflowSourceError(ValueError):
    pass


def declared(definition) -> dict[str, dict]:
    data = getattr(definition, "data", definition) or {}
    out = {}
    for source in data.get("workflow_sources") or []:
        if isinstance(source, dict) and source.get("alias") and source.get("workflow"):
            out[str(source["alias"])] = source
    return out


def find(definition, alias: str) -> dict:
    sources = declared(definition)
    if alias not in sources:
        known = ", ".join(sorted(sources)) or "none"
        raise WorkflowSourceError(f"this workflow declares no workflow source {alias!r} (declared: {known})")
    return sources[alias]


def _owner(source: dict) -> dict:
    owner = {k: source[k] for k in OWNER_KEYS if source.get(k) not in (None, "")}
    if "organization_id" in owner and not str(owner["organization_id"]).isdigit():
        # A labs-only organisation (slug) owns no LabsRecords; read in the request's scope.
        owner.pop("organization_id")
    return {k: int(v) for k, v in owner.items()}


def _run_url(definition_id, run) -> str:
    scope = (
        f"opportunity_id={run.opportunity_id}"
        if getattr(run, "opportunity_id", None)
        else (f"program_id={run.program_id}" if getattr(run, "program_id", None) else "")
    )
    return f"/labs/workflow/{definition_id}/run/?{scope}&run_id={run.id}".replace("?&", "?")


def _run(definition_id, run) -> dict:
    data = getattr(run, "data", None) or {}
    completed = getattr(run, "status", "") == "completed"
    return {
        "id": run.id,
        "name": getattr(run, "name", "") or "",
        "status": getattr(run, "status", ""),
        "period_start": getattr(run, "period_start", None),
        "period_end": getattr(run, "period_end", None),
        "completed_at": getattr(run, "completed_at", None),
        "url": _run_url(definition_id, run),
        # The summary a completed run keeps beside its stored snapshot (run_snapshot_store.py);
        # the snapshot itself is never sent.
        "summary": (data.get("snapshot_summary") if completed and hasattr(data, "get") else None),
    }


def read(request, source: dict) -> dict:
    """One source's data as the viewer, or `{"error": ...}`."""
    target = int(source["workflow"])
    how = source.get("read") or "latest_run"
    if how not in READS:
        return {"error": f"read must be one of {', '.join(READS)}, not {how!r}"}
    access = WorkflowDataAccess(request=request, **_owner(source))
    try:
        definition = access.get_definition(target)
        if not definition:
            return {"error": f"workflow {target} is not there, or you cannot open it"}
        runs = sorted(access.list_runs(target), key=lambda r: r.id, reverse=True)
    except Exception:  # noqa: BLE001 -- one source failing never blanks the page
        logger.warning("workflow source %s could not be read", target, exc_info=True)
        return {"error": f"workflow {target} could not be read"}
    finally:
        access.close()
    out = {
        "workflow": {
            "id": definition.id,
            "name": definition.name,
            "description": (definition.data or {}).get("description", ""),
            "url": f"/labs/workflow/{definition.id}/run/",
        }
    }
    if how == "saved_runs":
        out["runs"] = [_run(target, r) for r in runs if r.status == "completed"][:MAX_RUNS]
    else:
        out["latest_run"] = _run(target, runs[0]) if runs else None
    if how == "summary":
        out["run_count"] = len(runs)
    return out


def _definition(request, definition_id):
    access = WorkflowDataAccess(request=request)
    try:
        return access.get_definition(definition_id)
    finally:
        access.close()


@login_required
@require_GET
def workflow_data_api(request, definition_id):
    definition = _definition(request, definition_id)
    if not definition:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    return JsonResponse({"workflows": {alias: read(request, src) for alias, src in declared(definition).items()}})


@login_required
@require_POST
def workflow_query_api(request, definition_id):
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)
    definition = _definition(request, definition_id)
    if not definition:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    try:
        source = find(definition, str(body.get("alias") or ""))
    except WorkflowSourceError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse(read(request, source))
