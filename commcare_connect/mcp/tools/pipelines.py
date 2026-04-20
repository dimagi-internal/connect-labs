# commcare_connect/mcp/tools/pipelines.py
"""Pipeline tools for live-instance iteration from Claude Code.

Follows the same auth + data-access pattern as workflow tools:
1. Resolve the user's Connect OAuth token via require_connect_token.
2. Build a PipelineDataAccess scoped to the opportunity.
3. Do the work. Return JSON-serializable dict.

Write tools return _version_before / _version_after private keys so the
transport captures the version transition in the audit log.
"""

from commcare_connect.workflow.data_access import PipelineDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register


@register(
    name="pipeline_list",
    description=(
        "List pipelines visible to the calling user. "
        "Scope by exactly one of: opportunity_id, program_id, organization_id. "
        "Returns minimal metadata; use pipeline_get to fetch the full pipeline."
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
def pipeline_list(user, opportunity_id=None, program_id=None, organization_id=None):
    scope_count = sum(1 for x in (opportunity_id, program_id, organization_id) if x is not None)
    if scope_count != 1:
        raise MCPToolError(
            "INVALID_SCHEMA",
            "pipeline_list requires exactly one of opportunity_id / program_id / organization_id.",
        )

    token = require_connect_token(user)
    pda = PipelineDataAccess(
        access_token=token,
        opportunity_id=opportunity_id,
        program_id=program_id,
        organization_id=organization_id,
    )
    try:
        definitions = pda.list_definitions()
    finally:
        pda.close()

    return {
        "pipelines": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "updated_at": d.data.get("updated_at"),
                "version": d.version,
            }
            for d in definitions
        ]
    }


@register(
    name="pipeline_get",
    description=(
        "Fetch a pipeline's full schema and metadata. "
        "The schema describes fields, aggregations, transforms, and groupings."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
        },
        "required": ["pipeline_id", "opportunity_id"],
        "additionalProperties": False,
    },
)
def pipeline_get(user, pipeline_id: int, opportunity_id: int):
    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        definition = pda.get_definition(pipeline_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")
        return {
            "id": definition.id,
            "name": definition.name,
            "description": definition.description,
            "schema": definition.schema,
            "version": definition.version,
        }
    finally:
        pda.close()


_VALID_AGGREGATIONS = {"sum", "count", "count_distinct", "avg", "min", "max", "first", "last"}


def _validate_pipeline_schema(schema: dict) -> None:
    """Heuristic schema validation. Rejects unknown aggregations to avoid
    runtime SQL errors during preview."""
    if not isinstance(schema, dict):
        raise MCPToolError("INVALID_SCHEMA", "schema must be a dict")
    fields = schema.get("fields")
    if fields is None or not isinstance(fields, list):
        raise MCPToolError("INVALID_SCHEMA", "schema.fields must be a list")
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise MCPToolError("INVALID_SCHEMA", f"schema.fields[{i}] must be a dict")
        agg = f.get("aggregation")
        if agg and agg not in _VALID_AGGREGATIONS:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Unknown aggregation {agg!r} on field {f.get('name', '<unnamed>')!r}. "
                f"Valid: {sorted(_VALID_AGGREGATIONS)}",
            )


@register(
    name="pipeline_update_schema",
    description=(
        "Replace a pipeline's schema. Validates aggregations against an allow-list. "
        "Uses expected_version for optimistic concurrency — re-fetch via pipeline_get "
        "on VERSION_CONFLICT. Optionally updates name/description at the same time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "schema": {"type": "object"},
            "expected_version": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["pipeline_id", "opportunity_id", "schema", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pipeline_update_schema(
    user,
    pipeline_id: int,
    opportunity_id: int,
    schema: dict,
    expected_version: int,
    name: str = None,
    description: str = None,
):
    _validate_pipeline_schema(schema)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = pda.get_definition(pipeline_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")

        current_version = current.version
        if current_version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"pipeline is at version {current_version}, not {expected_version}. "
                "Call pipeline_get to re-read and retry.",
                details={"server_version": current_version, "expected": expected_version},
            )

        updated = pda.update_definition(
            definition_id=pipeline_id,
            name=name,
            description=description,
            schema=schema,
        )
        new_version = updated.version
        return {
            "pipeline_id": pipeline_id,
            "new_version": new_version,
            "_version_before": expected_version,
            "_version_after": new_version,
        }
    finally:
        if hasattr(pda, "close"):
            pda.close()
