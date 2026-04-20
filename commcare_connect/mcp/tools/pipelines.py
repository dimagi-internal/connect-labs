# commcare_connect/mcp/tools/pipelines.py
"""Pipeline tools for live-instance iteration from Claude Code.

Follows the same auth + data-access pattern as workflow tools:
1. Resolve the user's Connect OAuth token via require_connect_token.
2. Build a PipelineDataAccess scoped to the opportunity.
3. Do the work. Return JSON-serializable dict.

Write tools return _version_before / _version_after private keys so the
transport captures the version transition in the audit log.
"""

from commcare_connect.labs.analysis.backends.sql.query_builder import generate_sql_preview
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


@register(
    name="pipeline_preview",
    description=(
        "Run the pipeline against real opportunity data and return sample rows. "
        "schema_override previews an unsaved schema without persisting. "
        "This is the iteration hot path: read → tweak → preview → save."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "sample_size": {"type": "integer", "default": 50},
            "schema_override": {"type": "object"},
        },
        "required": ["pipeline_id", "opportunity_id"],
        "additionalProperties": False,
    },
)
def pipeline_preview(
    user,
    pipeline_id: int,
    opportunity_id: int,
    sample_size: int = 50,
    schema_override: dict = None,
):
    if schema_override is not None:
        _validate_pipeline_schema(schema_override)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        definition = pda.get_definition(pipeline_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")

        if schema_override is not None:
            # Use the override schema directly via the lower-level API.
            # _schema_to_config converts a schema dict → AnalysisPipelineConfig.
            # We then call AnalysisPipeline directly, bypassing execute_pipeline
            # (which reads the schema from the definition record, not from our override).
            # This guarantees the override is never persisted.
            from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

            config = pda._schema_to_config(schema_override, pipeline_id)
            pipeline = AnalysisPipeline(access_token=token)
            raw_result = pipeline.stream_analysis_ignore_events(config, opportunity_id)

            rows = []
            if hasattr(raw_result, "rows"):
                for row in raw_result.rows:

                    def format_date(d):
                        if d and hasattr(d, "isoformat"):
                            return d.isoformat()
                        return str(d) if d else None

                    row_dict = {
                        "id": getattr(row, "id", None),
                        "username": getattr(row, "username", None),
                        "visit_date": format_date(getattr(row, "visit_date", None)),
                        "total_visits": getattr(row, "total_visits", 0),
                        "approved_visits": getattr(row, "approved_visits", 0),
                        "pending_visits": getattr(row, "pending_visits", 0),
                        "rejected_visits": getattr(row, "rejected_visits", 0),
                        "flagged_visits": getattr(row, "flagged_visits", 0),
                        "first_visit_date": format_date(getattr(row, "first_visit_date", None)),
                        "last_visit_date": format_date(getattr(row, "last_visit_date", None)),
                    }
                    custom = getattr(row, "custom_fields", None) or getattr(row, "computed", None)
                    if custom:
                        row_dict.update(custom)
                    rows.append(row_dict)

            metadata = {
                "row_count": len(rows),
                "from_cache": getattr(raw_result, "from_cache", False),
                "pipeline_name": definition.name,
            }
            result = {"rows": rows, "metadata": metadata}
        else:
            result = pda.execute_pipeline(pipeline_id, opportunity_id)

        metadata = result.get("metadata") or {}
        if metadata.get("error"):
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"Pipeline execution error: {metadata['error']}",
                details={"metadata": metadata},
            )
        rows = result.get("rows", []) or []
        return {
            "pipeline_id": pipeline_id,
            "opportunity_id": opportunity_id,
            "rows": rows[:sample_size],
            "row_count_before_sample": len(rows),
            "used_schema_override": schema_override is not None,
            "metadata": metadata,
        }
    finally:
        if hasattr(pda, "close"):
            pda.close()


@register(
    name="pipeline_sql",
    description=(
        "Return the SQL the pipeline would execute, without running it. "
        "Useful for debugging. schema_override previews unsaved changes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "schema_override": {"type": "object"},
        },
        "required": ["pipeline_id", "opportunity_id"],
        "additionalProperties": False,
    },
)
def pipeline_sql(
    user,
    pipeline_id: int,
    opportunity_id: int,
    schema_override: dict = None,
):
    if schema_override is not None:
        _validate_pipeline_schema(schema_override)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        definition = pda.get_definition(pipeline_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")

        schema = schema_override if schema_override is not None else definition.schema
        config = pda._schema_to_config(schema, pipeline_id)

        sql_info = generate_sql_preview(config, opportunity_id)
        return {
            "pipeline_id": pipeline_id,
            "opportunity_id": opportunity_id,
            "sql": sql_info,
            "used_schema_override": schema_override is not None,
        }
    finally:
        if hasattr(pda, "close"):
            pda.close()
