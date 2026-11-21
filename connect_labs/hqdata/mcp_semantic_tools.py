"""The ``commcare_registry_*`` and ``commcare_indicators`` MCP tools: semantic indicators over a CommCare project.

The same registries, validation and compiler as the ``semantic_registry_*`` tools -- each tool here is that tool,
run in the project's scope (``project_id`` instead of an opportunity, programme or organisation), so a CommCare-only
person reaches it with a ``commcare`` token and nothing from Connect is read. A page uses a saved registry through an
indicator source (``hqdata.indicators``); ``commcare_indicators`` evaluates one on the spot, for working on a registry
before a page shows it.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from connect_labs.audit_trail.models import Action
from connect_labs.hqdata.audit import project_event
from connect_labs.hqdata.computed import ComputedWarming
from connect_labs.hqdata.mcp_tools import _PROJECT_ID, _project
from connect_labs.mcp.tool_registry import MCPToolError, get_tool, register
from connect_labs.mcp.tools import semantic

_SCOPE_KEYS = ("opportunity_id", "program_id", "organization_id")


def _schema_like(tool_name: str, **extra) -> dict:
    """``tool_name``'s input schema with its Connect scope swapped for ``project_id``."""
    schema = get_tool(tool_name).input_schema
    properties = {k: v for k, v in schema.get("properties", {}).items() if k not in _SCOPE_KEYS}
    return {
        **schema,
        "properties": {"project_id": _PROJECT_ID, **properties, **extra},
        "required": ["project_id", *schema.get("required", [])],
    }


def _scoped(project) -> dict:
    return {"opportunity_id": project.scope_id}


_SAME = "Same as {tool}, in a CommCare project's scope."


@register(
    name="commcare_registry_list",
    description="The semantic registries (indicator definitions) saved in a CommCare project, plus shared ones.",
    input_schema=_schema_like("semantic_registry_list"),
)
def commcare_registry_list(user, project_id=None, **_):
    return semantic.semantic_registry_list(user, **_scoped(_project(user, project_id)))


@register(
    name="commcare_registry_get",
    description=_SAME.format(tool="semantic_registry_get") + " The registry's full documents.",
    input_schema=_schema_like("semantic_registry_get"),
)
def commcare_registry_get(user, project_id=None, registry_id=None, **_):
    return semantic.semantic_registry_get(user, registry_id, **_scoped(_project(user, project_id)))


@register(
    name="commcare_registry_validate",
    description=_SAME.format(tool="semantic_registry_validate") + " Checks a registry without saving it.",
    input_schema=_schema_like("semantic_registry_validate"),
)
def commcare_registry_validate(user, project_id=None, properties_doc=None, indicators_doc=None, deployment=None, **_):
    _project(user, project_id)
    return semantic.semantic_registry_validate(user, properties_doc, indicators_doc, deployment)


@register(
    name="commcare_registry_create",
    description=(
        _SAME.format(tool="semantic_registry_create")
        + " The registry's `pipelines.entity` names the alias a page gives the project pipeline Layer 1 is"
        " generated from (and `extra_fields` / `lookups` other aliases of the same page)."
    ),
    input_schema=_schema_like("semantic_registry_create"),
    is_write=True,
)
def commcare_registry_create(user, project_id=None, **fields):
    project = _project(user, project_id)
    out = semantic.semantic_registry_create(user, **{**fields, **_scoped(project)})
    project_event(Action.CREATE, project.pk, user=user, kind="registry")
    return out


@register(
    name="commcare_registry_update",
    description=_SAME.format(tool="semantic_registry_update"),
    input_schema=_schema_like("semantic_registry_update"),
    is_write=True,
)
def commcare_registry_update(user, project_id=None, registry_id=None, **fields):
    project = _project(user, project_id)
    out = semantic.semantic_registry_update(user, registry_id, **{**fields, **_scoped(project)})
    project_event(Action.UPDATE, project.pk, user=user, kind="registry")
    return out


def _pages_binding(user, project, registry_id: int) -> list[dict]:
    """The project's pages with an indicator source on ``registry_id``."""
    from connect_labs.hqdata.workflows import workflow_dao

    dao = workflow_dao(user, project)
    try:
        return [
            {"workflow_id": d.id, "name": (d.data or {}).get("name")}
            for d in dao.list_definitions()
            if any(
                isinstance(s, dict) and (s.get("indicators") or {}).get("registry_id") == registry_id
                for s in (d.data or {}).get("pipeline_sources") or []
            )
        ]
    finally:
        dao.close()


@register(
    name="commcare_registry_delete",
    description=(
        "Delete a semantic registry saved in a CommCare project. IRREVERSIBLE. Refuses while a page of the project "
        "reads it through an indicator source, and names those pages."
    ),
    input_schema=_schema_like("semantic_registry_get"),
    is_write=True,
)
def commcare_registry_delete(user, project_id=None, registry_id=None, **_):
    project = _project(user, project_id)
    bound = _pages_binding(user, project, registry_id)
    if bound:
        raise MCPToolError(
            "CONFLICT",
            f"registry {registry_id} is read by page(s) {', '.join(str(b['workflow_id']) for b in bound)}; change "
            "their indicator sources first. Not deleted.",
            details={"workflows": bound},
        )
    access = semantic._access(user, **_scoped(project))
    try:
        record = access.get_registry_in_scope(registry_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"No semantic registry {registry_id} saved in this project.")
        access.delete_registry(registry_id)
    finally:
        access.close()
    project_event(Action.DELETE, project.pk, user=user, kind="registry")
    return {"registry_id": registry_id, "name": record.name, "deleted": True}


@register(
    name="commcare_indicators",
    description=(
        "Evaluate a semantic registry over a CommCare project's own pipelines, now, without a page: the figures an "
        "indicator source would give a page. `pipelines` maps each alias the registry names (its pipelines.entity, "
        "extra_fields, lookups) to a pipeline saved in the project. Registry by `registry_id` (`public: true` for "
        "another's shared one) or built-in `registry_name`. Returns one row per scope key and the measure catalog "
        "(titles, units, bands). Each pipeline's data is first brought up to date with the project's syncs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "registry_id": {"type": "integer"},
            "public": {"type": "boolean"},
            "registry_name": {"type": "string"},
            "pipelines": {
                "type": "object",
                "description": 'alias -> pipeline id, e.g. {"visits": 41}',
                "additionalProperties": {"type": "integer"},
            },
            "scopes": {"type": "array", "items": {"type": "string"}},
            "series": {"type": "string"},
            "as_of": {"type": "string", "description": "ISO date the figures are as of; today if omitted."},
            "max_rows": {"type": "integer", "description": "Rows returned (default 50); row_count is the total."},
        },
        "required": ["project_id", "pipelines"],
    },
)
def commcare_indicators(
    user,
    project_id=None,
    registry_id=None,
    public=False,
    registry_name=None,
    pipelines=None,
    scopes=None,
    series=None,
    as_of=None,
    max_rows=50,
    **_,
):
    from connect_labs.hqdata.indicators import IndicatorProblem, evaluate_source
    from connect_labs.hqdata.pipelines import find_pipeline

    project = _project(user, project_id)
    if as_of and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(as_of)):
        raise MCPToolError("INVALID_SCHEMA", "as_of is an ISO date (YYYY-MM-DD).")
    if not isinstance(pipelines, dict) or not pipelines:
        raise MCPToolError("INVALID_SCHEMA", "pipelines maps the registry's aliases to pipeline ids.")
    for alias, pipeline_id in pipelines.items():
        if find_pipeline(user, project, pipeline_id=pipeline_id) is None:
            raise MCPToolError("NOT_FOUND", f"Pipeline {pipeline_id} ({alias}) is not saved in this project.")
    spec = {"registry_id": registry_id} if registry_id is not None else {"name": registry_name}
    if registry_id is not None and public:
        spec["public"] = True
    source = {"alias": "indicators", "indicators": spec, "scopes": scopes or [], "series": series}
    page = SimpleNamespace(pipeline_sources=[{"pipeline_id": p, "alias": a} for a, p in pipelines.items()])
    try:
        result = evaluate_source(user, project, page, source, as_of=f"DATE '{as_of}'" if as_of else "CURRENT_DATE")
    except IndicatorProblem as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc)) from exc
    except ComputedWarming as exc:
        raise MCPToolError("UPSTREAM_ERROR", f"{exc.message} Ask again in a few minutes.") from exc
    project_event(Action.READ, project.pk, user=user, kind="indicators")
    limit = max(1, min(int(max_rows or 50), 1000))
    return {
        "rows": result["rows"][:limit],
        "row_count": len(result["rows"]),
        "truncated": len(result["rows"]) > limit,
        **{k: v for k, v in result["metadata"].items() if k not in ("kind", "row_count", "opportunity_ids")},
    }
