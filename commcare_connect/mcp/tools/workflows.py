"""Workflow tools — live-instance iteration from Claude Code."""

import re

from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data
from commcare_connect.workflow.data_access import PipelineDataAccess, WorkflowDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register


def _collect_user_opportunity_ids(access_token: str) -> set[int]:
    """Return the set of opportunity IDs the caller can see on production Connect.

    Used to validate opportunity_ids before persisting them on a workflow
    definition — matches the validation the Labs web-app does at
    ``workflow/views.py`` so multi-opp writes from the MCP stay in lockstep.
    Returns an empty set if the upstream call fails; the caller is expected to
    treat that as \"cannot validate\" and either error or skip as appropriate.
    """
    data = fetch_user_organization_data(access_token)
    if not data:
        return set()
    return {opp.get("id") for opp in data.get("opportunities") or [] if opp.get("id") is not None}


def _data_access(user, opportunity_id=None, program_id=None, organization_id=None) -> WorkflowDataAccess:
    """Build a WorkflowDataAccess for the user, carrying their Connect token.

    BaseDataAccess accepts access_token as a direct kwarg. The scope IDs are
    also passed at construction time so labs_api is initialised with the correct
    opportunity_id / program_id / organization_id for scoped API calls.
    """
    token = require_connect_token(user)
    return WorkflowDataAccess(
        access_token=token,
        opportunity_id=opportunity_id,
        program_id=program_id,
        organization_id=organization_id,
    )


@register(
    name="workflow_list",
    description=(
        "List workflows visible to the calling user. "
        "Scope by exactly one of: opportunity_id, program_id, organization_id. "
        "Returns minimal metadata; use workflow_get to fetch the full workflow."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
            "organization_id": {"type": "integer"},
        },
        "additionalProperties": False,
    },
)
def workflow_list(user, opportunity_id=None, program_id=None, organization_id=None):
    scope_count = sum(1 for x in (opportunity_id, program_id, organization_id) if x is not None)
    if scope_count != 1:
        raise MCPToolError(
            "INVALID_SCHEMA",
            "workflow_list requires exactly one of opportunity_id / program_id / organization_id.",
        )

    da = _data_access(
        user,
        opportunity_id=opportunity_id,
        program_id=program_id,
        organization_id=organization_id,
    )
    try:
        # list_definitions() uses the scope set at construction time (via labs_api).
        # Scope params are NOT accepted by list_definitions() itself.
        definitions = da.list_definitions()
    finally:
        da.close()

    return {
        "workflows": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "template_type": d.template_type,
                # updated_at is not on LocalLabsRecord; omit rather than error
                "updated_at": None,
                "pipeline_source_count": len(d.pipeline_sources),
            }
            for d in definitions
        ]
    }


@register(
    name="workflow_get",
    description=(
        "Fetch everything needed to iterate on a workflow in one call: "
        "definition (name, description, statuses, config), latest render_code "
        "with its version number, and linked pipeline metadata. "
        "Set include_render_code=false for a lighter response when you only "
        "need metadata (render_code can be 20+ KB)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "include_render_code": {
                "type": "boolean",
                "description": (
                    "When false, render_code is omitted and render_code_version "
                    "is still returned. Defaults to true."
                ),
            },
        },
        "required": ["workflow_id", "opportunity_id"],
        "additionalProperties": False,
    },
)
def workflow_get(user, workflow_id: int, opportunity_id: int, include_render_code: bool = True):
    """Fetch one workflow with all the context needed to edit it."""
    token = require_connect_token(user)

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        definition = wda.get_definition(workflow_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")

        # get_render_code is the actual method name (not get_latest_render_code)
        render_code = wda.get_render_code(workflow_id)
        pipeline_sources = definition.data.get("pipeline_sources", [])

        # Fetch each linked pipeline's summary.
        pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
        try:
            enriched_sources = []
            for src in pipeline_sources:
                pid = src.get("pipeline_id")
                pdef = pda.get_definition(pid) if pid else None
                enriched_sources.append(
                    {
                        "pipeline_id": pid,
                        "alias": src.get("alias"),
                        "name": pdef.name if pdef else None,
                        "schema_summary": {
                            "field_count": len(pdef.data.get("schema", {}).get("fields", [])) if pdef else 0,
                        },
                    }
                )
        finally:
            pda.close()
    finally:
        wda.close()

    out = {
        "id": definition.id,
        "name": definition.name,
        "description": definition.description,
        "statuses": definition.data.get("statuses", []),
        "config": definition.data.get("config", {}),
        "template_type": definition.template_type,
        "render_code_version": render_code.version if render_code else None,
        "pipeline_sources": enriched_sources,
    }
    if include_render_code:
        out["render_code"] = render_code.component_code if render_code else None
    return out


_MAX_RENDER_CODE_BYTES = 512 * 1024  # 512 KB


def _validate_render_code(jsx: str) -> None:
    """Minimal validation for render_code. The browser runs the real syntax
    check via Babel standalone at render time, so the server's job is just
    to reject obvious non-submissions (empty / oversized) and let everything
    else through. Policy constraints like `var`-only or naming conventions
    used to live here but were removed — they block valid modern JS (``let``,
    ``const``, arrow-function components) and give no benefit the client
    can't provide at render time with a clearer error.
    """
    if not jsx or not jsx.strip():
        raise MCPToolError("INVALID_JSX", "render_code is empty")
    if len(jsx.encode("utf-8")) > _MAX_RENDER_CODE_BYTES:
        raise MCPToolError(
            "INVALID_JSX",
            f"render_code exceeds {_MAX_RENDER_CODE_BYTES // 1024} KB. Split it into helper "
            "workflows or move data to pipelines.",
        )


from commcare_connect.workflow.templates import (  # noqa: E402
    create_workflow_from_template as _create_workflow_from_template,
)

_DEFINITION_PATCH_ALLOWED = {"name", "description", "statuses", "config"}


@register(
    name="workflow_update_definition",
    description=(
        "Update fields on a workflow definition. Accepts a patch dict. "
        "Allowed keys: name, description, statuses, config. `statuses` replaces "
        "wholesale; `config` shallow-merges. Unknown keys rejected with INVALID_SCHEMA. "
        "Uses expected_version for optimistic concurrency."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "patch": {"type": "object"},
            "expected_version": {"type": "integer"},
        },
        "required": ["workflow_id", "opportunity_id", "patch", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_update_definition(
    user,
    workflow_id: int,
    opportunity_id: int,
    patch: dict,
    expected_version: int,
):
    unknown_keys = set(patch) - _DEFINITION_PATCH_ALLOWED
    if unknown_keys:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"Unknown patch keys: {sorted(unknown_keys)}. " f"Allowed: {sorted(_DEFINITION_PATCH_ALLOWED)}",
        )

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_definition(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")
        current_version = current.data.get("version", 1)
        if current_version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"workflow definition is at version {current_version}, not {expected_version}. "
                "Call workflow_get to re-read and retry.",
                details={"server_version": current_version, "expected": expected_version},
            )

        # Build the updated payload. Keep existing data, then apply patch per rules.
        # Real update_definition(definition_id, data) takes only id + full data dict.
        new_data = dict(current.data)  # shallow copy
        if "name" in patch:
            new_data["name"] = patch["name"]
        if "description" in patch:
            new_data["description"] = patch["description"]
        if "statuses" in patch:
            new_data["statuses"] = patch["statuses"]  # replace wholesale
        if "config" in patch:
            merged_config = dict(new_data.get("config", {}))
            merged_config.update(patch["config"])
            new_data["config"] = merged_config
        new_data["version"] = expected_version + 1

        updated = wda.update_definition(
            definition_id=workflow_id,
            data=new_data,
        )
        new_version = updated.data.get("version", expected_version + 1)
        return {
            "workflow_id": workflow_id,
            "new_version": new_version,
            "_version_before": expected_version,
            "_version_after": new_version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


@register(
    name="workflow_update_opportunity_ids",
    description=(
        "Replace the opportunity_ids list on a multi-opportunity workflow "
        "definition. Every id must be an opportunity the caller has access "
        "to (validated against /export/opp_org_program_list/). Pass an empty "
        "list to revert the workflow to single-opportunity behaviour. Uses "
        "expected_version for optimistic concurrency; other definition data "
        "(name, statuses, config, etc.) is preserved. This is the only way to "
        "set opportunity_ids via the MCP — workflow_update_definition does "
        "not accept it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {
                "type": "integer",
                "description": (
                    "The owning opportunity (scoping the Labs record), not one "
                    "of the opportunity_ids being set. Usually the primary opp "
                    "the workflow was created under."
                ),
            },
            "opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "expected_version": {"type": "integer"},
        },
        "required": [
            "workflow_id",
            "opportunity_id",
            "opportunity_ids",
            "expected_version",
        ],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_update_opportunity_ids(
    user,
    workflow_id: int,
    opportunity_id: int,
    opportunity_ids: list[int],
    expected_version: int,
):
    # De-dupe while preserving order; reject non-int entries early.
    seen: set[int] = set()
    cleaned: list[int] = []
    for oid in opportunity_ids:
        if not isinstance(oid, int) or isinstance(oid, bool):
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"opportunity_ids must be a list of ints. Got {oid!r}.",
            )
        if oid not in seen:
            seen.add(oid)
            cleaned.append(oid)

    token = require_connect_token(user)

    if cleaned:
        # Validate every id is something the caller can actually access. Mirrors
        # the check Labs does at workflow/views.py so we don't persist ids the
        # user couldn't otherwise set via the UI.
        user_opp_ids = _collect_user_opportunity_ids(token)
        if not user_opp_ids:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                "Could not fetch caller's opportunities from production Connect to validate opportunity_ids.",
            )
        invalid = [oid for oid in cleaned if oid not in user_opp_ids]
        if invalid:
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"Caller has no access to opportunity_ids {sorted(invalid)}.",
                details={"invalid_opportunity_ids": sorted(invalid)},
            )

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_definition(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")

        current_version = current.data.get("version", 1)
        if current_version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"workflow definition is at version {current_version}, not {expected_version}. "
                "Call workflow_get to re-read and retry.",
                details={"server_version": current_version, "expected": expected_version},
            )

        # Build the updated payload. update_opportunity_ids() preserves other
        # data but doesn't bump version — do that ourselves so the concurrency
        # check on the next write is consistent with workflow_update_definition.
        new_data = {**current.data, "opportunity_ids": list(cleaned)}
        new_data["version"] = expected_version + 1

        updated = wda.update_definition(definition_id=workflow_id, data=new_data)
        new_version = updated.data.get("version", expected_version + 1)
        return {
            "workflow_id": workflow_id,
            "opportunity_ids": list(cleaned),
            "new_version": new_version,
            "_version_before": expected_version,
            "_version_after": new_version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


@register(
    name="workflow_create_from_template",
    description=(
        "Create a new workflow from a built-in Python seed template. "
        "template_key is one of the registered templates in "
        "commcare_connect/workflow/templates/*.py (e.g. 'performance_review'); "
        "call list_templates to enumerate. Returns the new workflow_id. "
        "If the template declares a pipeline_schema (or pipeline_schemas), "
        "the pipeline(s) are created and linked automatically. For multi-opp "
        "templates, pass opportunity_ids to attach multiple opportunities in "
        "one call — each must be accessible to the caller."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_key": {"type": "string"},
            "opportunity_id": {
                "type": "integer",
                "description": "The primary/owning opportunity for the new workflow record.",
            },
            "name": {
                "type": "string",
                "description": "Optional override for the workflow name.",
            },
            "opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Optional list of opportunities the workflow should merge data from "
                    "(multi-opp templates only). Each id is validated against the caller's "
                    "access; single-opp templates silently ignore this."
                ),
            },
        },
        "required": ["template_key", "opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_create_from_template(
    user,
    template_key: str,
    opportunity_id: int,
    name: str = None,
    opportunity_ids: list[int] = None,
):
    token = require_connect_token(user)

    # Validate opportunity_ids up-front so we don't leave a half-created workflow
    # around if the caller tries to attach an opp they can't access. Mirrors the
    # check in workflow_update_opportunity_ids for consistency.
    cleaned_opp_ids: list[int] = []
    if opportunity_ids:
        seen: set[int] = set()
        for oid in opportunity_ids:
            if not isinstance(oid, int) or isinstance(oid, bool):
                raise MCPToolError(
                    "INVALID_SCHEMA",
                    f"opportunity_ids must be a list of ints. Got {oid!r}.",
                )
            if oid not in seen:
                seen.add(oid)
                cleaned_opp_ids.append(oid)
        user_opp_ids = _collect_user_opportunity_ids(token)
        if not user_opp_ids:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                "Could not fetch caller's opportunities from production Connect to validate opportunity_ids.",
            )
        invalid = [oid for oid in cleaned_opp_ids if oid not in user_opp_ids]
        if invalid:
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"Caller has no access to opportunity_ids {sorted(invalid)}.",
                details={"invalid_opportunity_ids": sorted(invalid)},
            )

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        try:
            # request=None means we go through the access_token path.
            # Pipelines are created via data_access.access_token forwarding.
            definition, render_code, pipeline = _create_workflow_from_template(
                data_access=wda,
                template_key=template_key,
                request=None,
                opportunity_ids=cleaned_opp_ids or None,
            )
        except ValueError as e:
            # create_workflow_from_template raises ValueError on unknown template.
            raise MCPToolError("NOT_FOUND", str(e))

        # If an override name was passed, apply it via a second update.
        if name and name != definition.name:
            new_data = dict(definition.data)
            new_data["name"] = name
            wda.update_definition(
                definition_id=definition.id,
                data=new_data,
            )

        return {
            "workflow_id": definition.id,
            "render_code_version": render_code.version if render_code else None,
            "pipeline_id": pipeline.id if pipeline else None,
            "opportunity_ids": list(cleaned_opp_ids),
            "_version_before": None,
            "_version_after": 1,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


_TEMPLATE_SCOPE_PATTERN = re.compile(r"^(global|org:\d+|program:\d+)$")


def _validate_template_scope(scope: str, user) -> None:
    """Validate the template_scope string and check caller permissions.

    Scope can be 'global', 'org:<id>', or 'program:<id>'. 'global' requires
    labs admin role (user.is_staff as a stand-in until we have a real role).
    """
    if not _TEMPLATE_SCOPE_PATTERN.match(scope):
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"template_scope must match 'global' or 'org:<id>' or 'program:<id>'. Got {scope!r}.",
        )
    if scope == "global" and not getattr(user, "is_staff", False):
        raise MCPToolError(
            "PERMISSION_DENIED",
            "Only labs admins can set template_scope='global'.",
        )


@register(
    name="workflow_set_template_flag",
    description=(
        "Mark a workflow as a template (or unmark it). Templates can be cloned "
        "via workflow_clone. Scope controls who sees the template in the clone "
        "picker: 'global' (all labs users, admin-only to set), 'org:<id>' "
        "(org members), or 'program:<id>' (program members)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "is_template": {"type": "boolean"},
            "template_scope": {"type": "string"},
        },
        "required": ["workflow_id", "opportunity_id", "is_template"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_set_template_flag(
    user,
    workflow_id: int,
    opportunity_id: int,
    is_template: bool,
    template_scope: str = None,
):
    if is_template:
        if not template_scope:
            raise MCPToolError(
                "INVALID_SCHEMA",
                "template_scope is required when is_template=true.",
            )
        _validate_template_scope(template_scope, user)

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_definition(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")

        new_data = dict(current.data)
        if is_template:
            new_data["is_template"] = True
            new_data["template_scope"] = template_scope
        else:
            new_data.pop("is_template", None)
            new_data.pop("template_scope", None)

        wda.update_definition(definition_id=workflow_id, data=new_data)
        return {
            "workflow_id": workflow_id,
            "is_template": is_template,
            "template_scope": template_scope if is_template else None,
        }
    finally:
        wda.close()


@register(
    name="workflow_clone",
    description=(
        "Create a new workflow by cloning any existing workflow the caller can "
        "read. Used both for generic duplication and for instantiating DB-backed "
        "templates (workflows flagged with is_template=true). The new workflow's "
        "is_template flag is always false — cloning from a template produces a "
        "regular workflow. Copies the definition (statuses, config), the latest "
        "render_code, and the pipeline_sources list verbatim. Does NOT clone "
        "linked pipelines — the new workflow references the same pipelines as "
        "the source."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_workflow_id": {"type": "integer"},
            "source_opportunity_id": {"type": "integer"},
            "target_opportunity_id": {"type": "integer"},
            "new_name": {"type": "string"},
        },
        "required": [
            "source_workflow_id",
            "source_opportunity_id",
            "target_opportunity_id",
        ],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_clone(
    user,
    source_workflow_id: int,
    source_opportunity_id: int,
    target_opportunity_id: int,
    new_name: str = None,
):
    token = require_connect_token(user)

    src_wda = WorkflowDataAccess(access_token=token, opportunity_id=source_opportunity_id)
    try:
        source_def = src_wda.get_definition(source_workflow_id)
        if source_def is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {source_workflow_id}")
        source_render = src_wda.get_render_code(source_workflow_id)
    finally:
        src_wda.close()

    # Build the new workflow's data dict, stripping template flags so clones are
    # always regular workflows.
    new_data = dict(source_def.data)
    new_data.pop("is_template", None)
    new_data.pop("template_scope", None)
    new_data["version"] = 1
    cloned_name = new_name or f"{source_def.name} (copy)"

    dst_wda = WorkflowDataAccess(access_token=token, opportunity_id=target_opportunity_id)
    try:
        # create_definition(name, description, **kwargs) — pass statuses, config,
        # pipeline_sources, and opportunity_ids as explicit kwargs so they override
        # the defaults inside create_definition. Pass the full cleaned new_data dict
        # as **kwargs to forward any extra fields the source may carry.
        new_def = dst_wda.create_definition(
            name=cloned_name,
            description=source_def.description,
            statuses=new_data.get("statuses"),
            config=new_data.get("config"),
            pipeline_sources=new_data.get("pipeline_sources", []),
            opportunity_ids=new_data.get("opportunity_ids", []),
        )
        render_code_version = None
        if source_render is not None:
            new_render = dst_wda.save_render_code(
                definition_id=new_def.id,
                component_code=source_render.component_code,
                version=1,
            )
            render_code_version = new_render.version
    finally:
        dst_wda.close()

    return {
        "new_workflow_id": new_def.id,
        "source_workflow_id": source_workflow_id,
        "name": cloned_name,
        "render_code_version": render_code_version,
        "_version_before": None,
        "_version_after": 1,
    }


@register(
    name="workflow_update_render_code",
    description=(
        "Replace a workflow's render_code (the JSX UI). Server-side validation "
        "is intentionally minimal: rejects empty payloads and oversized ones "
        "(> 512 KB). Real syntax checking happens in the browser via Babel "
        "standalone at render time, where errors are surfaced with full stack "
        "traces. Uses expected_version for optimistic concurrency — re-fetch "
        "via workflow_get on VERSION_CONFLICT."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "component_code": {"type": "string"},
            "expected_version": {"type": "integer"},
        },
        "required": ["workflow_id", "opportunity_id", "component_code", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_update_render_code(
    user,
    workflow_id: int,
    opportunity_id: int,
    component_code: str,
    expected_version: int,
):
    _validate_render_code(component_code)

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_render_code(workflow_id)
        if current is None:
            raise MCPToolError(
                "NOT_FOUND",
                f"No render_code for workflow {workflow_id}. "
                "Create the workflow first via workflow_create_from_template.",
            )
        if current.version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"render_code is at version {current.version}, not {expected_version}. "
                "Call workflow_get to re-read and retry.",
                details={"server_version": current.version, "expected": expected_version},
            )
        new_record = wda.save_render_code(
            definition_id=workflow_id,
            component_code=component_code,
            version=expected_version + 1,
        )
        return {
            "workflow_id": workflow_id,
            "new_version": new_record.version,
            "_version_before": expected_version,
            "_version_after": new_record.version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


@register(
    name="workflow_patch_render_code",
    description=(
        "Apply a search/replace patch to a workflow's render_code without "
        "re-sending the whole file. `search` must match exactly once; "
        "otherwise we refuse the patch (no silent ambiguity). Dramatically "
        "cheaper than workflow_update_render_code for small tweaks. Uses "
        "expected_version for optimistic concurrency — re-fetch via "
        "workflow_get on VERSION_CONFLICT. Same 512 KB size cap applies to "
        "the resulting code."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "search": {
                "type": "string",
                "description": "Exact substring to find. Must match once.",
            },
            "replace": {
                "type": "string",
                "description": "Replacement string (can be empty to delete).",
            },
            "expected_version": {"type": "integer"},
        },
        "required": ["workflow_id", "opportunity_id", "search", "replace", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_patch_render_code(
    user,
    workflow_id: int,
    opportunity_id: int,
    search: str,
    replace: str,
    expected_version: int,
):
    if not search:
        raise MCPToolError("INVALID_JSX", "search must not be empty")

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_render_code(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No render_code for workflow {workflow_id}.")
        if current.version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"render_code is at version {current.version}, not {expected_version}. "
                "Call workflow_get to re-read and retry.",
                details={"server_version": current.version, "expected": expected_version},
            )

        existing = current.component_code or ""
        occurrences = existing.count(search)
        if occurrences == 0:
            raise MCPToolError(
                "NOT_FOUND",
                "search string did not match any substring of the current render_code.",
                details={"occurrences": 0},
            )
        if occurrences > 1:
            raise MCPToolError(
                "INVALID_JSX",
                f"search string matched {occurrences} times — refusing to patch ambiguously. "
                "Provide a longer search that is unique in the file.",
                details={"occurrences": occurrences},
            )

        patched = existing.replace(search, replace, 1)
        _validate_render_code(patched)

        new_record = wda.save_render_code(
            definition_id=workflow_id,
            component_code=patched,
            version=expected_version + 1,
        )
        return {
            "workflow_id": workflow_id,
            "new_version": new_record.version,
            "chars_before": len(existing),
            "chars_after": len(patched),
            "_version_before": expected_version,
            "_version_after": new_record.version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


@register(
    name="workflow_delete",
    description=(
        "Delete a workflow definition and its associated render_code + chat "
        "history. By default, runs and their audit sessions are preserved "
        "(they are historical records); set delete_linked=true to cascade "
        "into runs and audit sessions too. Returns counts of deleted records. "
        "IRREVERSIBLE — use with care."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "delete_linked": {
                "type": "boolean",
                "description": "If true, also delete runs and linked audit sessions. Defaults to false.",
            },
        },
        "required": ["workflow_id", "opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_delete(user, workflow_id: int, opportunity_id: int, delete_linked: bool = False):
    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        existing = wda.get_definition(workflow_id)
        if existing is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")
        counts = wda.delete_definition(workflow_id, delete_linked=delete_linked)
        return {"workflow_id": workflow_id, "deleted": counts}
    finally:
        if hasattr(wda, "close"):
            wda.close()
