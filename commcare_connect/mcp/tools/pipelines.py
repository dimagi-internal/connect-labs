# commcare_connect/mcp/tools/pipelines.py
"""Pipeline tools for live-instance iteration from Claude Code.

Follows the same auth + data-access pattern as workflow tools:
1. Resolve the user's Connect OAuth token via require_connect_token.
2. Build a PipelineDataAccess scoped to the opportunity.
3. Do the work. Return JSON-serializable dict.

Write tools return _version_before / _version_after private keys so the
transport captures the version transition in the audit log.
"""

import logging
import re

from commcare_connect.labs.analysis.backends.sql.query_builder import generate_sql_preview
from commcare_connect.workflow.data_access import PipelineDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)


def _hint_for_sql_error(err: str, schema: dict | None) -> str | None:
    """Best-effort: map common Postgres / pipeline-engine error strings to a
    pointer at the schema field most likely at fault. Returns None if no
    useful hint can be extracted — callers should then just surface the raw
    error.
    """
    if not err:
        return None
    # Unknown aggregation errors carry the offending name already.
    m = re.search(r"Unknown aggregation '([^']+)' on field '([^']+)'", err)
    if m:
        return f"Field '{m.group(2)}' uses aggregation '{m.group(1)}', which the SQL builder does not support."
    # Correlated-subquery / GROUP BY errors point at a first/last aggregation.
    if "ungrouped column" in err and schema and isinstance(schema, dict):
        offenders = [
            f.get("name")
            for f in (schema.get("fields") or [])
            if isinstance(f, dict) and f.get("aggregation") in {"first", "last"}
        ]
        if offenders:
            return (
                "Postgres rejected a correlated subquery — typically emitted by `first`/`last` "
                "aggregations. Fields using those: " + ", ".join(map(str, offenders)) + "."
            )
    # "path does not exist" style errors sometimes name the expression.
    m = re.search(r"column \"([^\"]+)\" does not exist", err)
    if m:
        return (
            f"Column '{m.group(1)}' isn't a known extract target — double-check field.path. "
            "To discover real JSON paths for a form, use `get_form_json_paths` from the "
            "local `commcare_hq_mcp` server (this MCP has no HQ API key and cannot resolve "
            "paths itself)."
        )
    return None


def _fields_all_null(rows: list[dict], schema: dict | None) -> list[str]:
    """Return the names of custom fields (from the schema) that came back
    null / empty for every row in the sample. These are the loudest possible
    diagnostic signal that a field.path is wrong: SQL succeeded, but nothing
    extracted. The fix is almost always to look up the real path via
    `get_form_json_paths` on the `commcare_hq_mcp` server.
    """
    if not rows or not schema or not isinstance(schema, dict):
        return []
    field_names = [f.get("name") for f in (schema.get("fields") or []) if isinstance(f, dict) and f.get("name")]
    if not field_names:
        return []
    # Built-in FLW columns (total_visits etc.) aren't custom — skip them so we
    # don't flag legitimately-empty counts.
    builtin = {
        "id",
        "username",
        "visit_date",
        "total_visits",
        "approved_visits",
        "pending_visits",
        "rejected_visits",
        "flagged_visits",
        "first_visit_date",
        "last_visit_date",
        "opportunity_id",
    }
    candidates = [n for n in field_names if n not in builtin]
    out = []
    for name in candidates:
        if all((r.get(name) in (None, "", [], {})) for r in rows):
            out.append(name)
    return out


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


# Kept in lockstep with the SQL query_builder's `_aggregation_to_sql`. Add
# here AND in the SQL builder when extending; the builder raises on unknown
# aggregations so a stale MCP allow-list would surface as a server error
# rather than a silent default.
_VALID_AGGREGATIONS = {
    "sum",
    "count",
    "count_distinct",
    "count_unique",
    "avg",
    "min",
    "max",
    "first",
    "last",
    "list",
}


def _validate_pipeline_schema(schema: dict) -> None:
    """Minimal schema validation. Only rejects things the SQL builder will
    definitely reject (unknown aggregations, non-dict payloads) so that the
    error surfaces at MCP call time with a pointed message instead of as a
    generic SQL error during preview. Everything else — field paths,
    transforms, bucket definitions — is left to the pipeline engine.
    """
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
        "on VERSION_CONFLICT. Optionally updates name/description at the same time.\n\n"
        "IMPORTANT: when adding or changing field paths, use "
        "`get_form_json_paths` from the local `commcare_hq_mcp` server "
        "to discover the exact JSON path for each form question. This "
        "MCP (connect_labs) intentionally has no CommCare HQ API key, so "
        "it cannot resolve paths itself. Wrong paths silently extract "
        "null; callers then see all-null columns in pipeline_preview, "
        "which also reports them in `fields_all_null`."
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
        "opportunity_ids (optional) runs the pipeline against each opp and "
        "merges the rows with an opportunity_id tag on each — mirrors what "
        "a multi-opp workflow sees at runtime. Errors from the SQL engine "
        "are wrapped with a 'hint' pointing at the likely offending field "
        "when one can be inferred. The response also includes "
        "`fields_all_null`: custom field names that extracted null for every "
        "row — the loudest signal that field.path is wrong. When you see a "
        "field flagged there, resolve the correct path with "
        "`get_form_json_paths` on the local `commcare_hq_mcp` server before "
        "re-previewing. This is the iteration hot path: "
        "read → tweak → preview → save."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "sample_size": {"type": "integer", "default": 50},
            "schema_override": {"type": "object"},
            "opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Optional list of opps to fan the preview across (multi-opp "
                    "workflows). Results from each opp are merged; rows gain an "
                    "opportunity_id key."
                ),
            },
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
    opportunity_ids: list[int] = None,
):
    if schema_override is not None:
        _validate_pipeline_schema(schema_override)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)

    # Decide which opps to fan out across. Caller-supplied opportunity_ids
    # always includes the primary opp implicitly.
    target_opps: list[int] = []
    seen: set[int] = set()
    for oid in [opportunity_id] + list(opportunity_ids or []):
        if oid in seen:
            continue
        seen.add(oid)
        target_opps.append(oid)

    def _single_opp_preview(opp_id: int) -> dict:
        """Run the preview against one opp. Returns {"rows": [...], "metadata": {...}}.
        Never raises on execution error — failures come back as metadata.error,
        same contract as execute_pipeline."""
        if schema_override is not None:
            # Use the override schema directly via the lower-level API.
            # _schema_to_config converts a schema dict → AnalysisPipelineConfig.
            # We then call AnalysisPipeline directly, bypassing execute_pipeline
            # (which reads the schema from the definition record, not from our override).
            from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

            try:
                config = pda._schema_to_config(schema_override, pipeline_id)
                pipeline = AnalysisPipeline(access_token=token)
                raw_result = pipeline.stream_analysis_ignore_events(config, opp_id)
            except Exception as e:
                return {"rows": [], "metadata": {"error": str(e)}}

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

            return {
                "rows": rows,
                "metadata": {
                    "row_count": len(rows),
                    "from_cache": getattr(raw_result, "from_cache", False),
                    "pipeline_name": definition.name,
                },
            }
        return pda.execute_pipeline(pipeline_id, opp_id)

    try:
        definition = pda.get_definition(pipeline_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")

        # Execution schema used for error-hint generation (override wins when
        # provided; otherwise the saved schema).
        error_hint_schema = schema_override if schema_override is not None else (definition.data or {}).get("schema")

        merged_rows: list[dict] = []
        per_opp_metadata: dict[str, dict] = {}
        first_error: str | None = None

        for oid in target_opps:
            res = _single_opp_preview(oid)
            md = res.get("metadata") or {}
            per_opp_metadata[str(oid)] = md
            if md.get("error"):
                # Record the first error but continue fanning out; callers often
                # want to see partial results across the other opps. The first
                # error becomes the top-level error if no opp succeeded.
                if first_error is None:
                    first_error = md["error"]
                continue
            for row in res.get("rows", []) or []:
                # Tag each row so downstream UI (multi-opp workflows) can see
                # which opp it came from. Preserve an existing opportunity_id
                # if the row already has one (shouldn't, but harmless).
                if "opportunity_id" not in row:
                    row = {**row, "opportunity_id": oid}
                merged_rows.append(row)

        # If every opp errored, surface the first error with a hint.
        if not merged_rows and first_error:
            # Special-case the "this pipeline uses cchq_forms but we have no
            # web session" failure. Without this, callers see a generic
            # UPSTREAM_ERROR and can't tell that the problem is structural
            # (the pipeline simply cannot run via MCP today) rather than a
            # transient failure worth retrying.
            if "headless context" in first_error or "cchq_forms" in first_error.lower():
                raise MCPToolError(
                    "UPSTREAM_ERROR",
                    f"Pipeline execution error: {first_error}",
                    details={
                        "per_opp": per_opp_metadata,
                        "headless_cchq_forms": True,
                        "remediation": (
                            "Run the preview from the web UI (which has the "
                            "CommCare HQ OAuth session), or change the "
                            "pipeline's data_source.type to 'connect_csv'."
                        ),
                    },
                )
            hint = _hint_for_sql_error(first_error, error_hint_schema)
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"Pipeline execution error: {first_error}" + (f"  Hint: {hint}" if hint else ""),
                details={
                    "per_opp": per_opp_metadata,
                    "hint": hint,
                },
            )

        # Top-level metadata mirrors the old single-opp shape as closely as
        # possible; pipeline_name is pulled safely (definition may be a Mock
        # in tests). per_opp_metadata carries the detailed breakdown.
        top_meta = {"row_count": len(merged_rows)}
        pname = getattr(definition, "name", None)
        if isinstance(pname, str):
            top_meta["pipeline_name"] = pname
        top_meta["opps_with_errors"] = [oid for oid, m in per_opp_metadata.items() if m.get("error")]

        # Flag custom fields that extracted null for every row — almost always
        # a wrong field.path. Use the executed schema (override when set, the
        # saved schema otherwise) so the names match what the caller sent.
        exec_schema = schema_override if schema_override is not None else (definition.data or {}).get("schema")
        fields_all_null = _fields_all_null(merged_rows, exec_schema)

        return {
            "pipeline_id": pipeline_id,
            "opportunity_id": opportunity_id,
            "opportunity_ids": target_opps if len(target_opps) > 1 else None,
            "rows": merged_rows[:sample_size],
            "row_count_before_sample": len(merged_rows),
            "used_schema_override": schema_override is not None,
            "per_opp_metadata": per_opp_metadata,
            "fields_all_null": fields_all_null,
            "fields_all_null_hint": (
                "These custom fields extracted null for every row. Usually means "
                "field.path is wrong — use `get_form_json_paths` on the local "
                "`commcare_hq_mcp` server to look up the real JSON path, then "
                "re-preview with schema_override."
            )
            if fields_all_null
            else None,
            "metadata": top_meta,
        }
    finally:
        if hasattr(pda, "close"):
            pda.close()


@register(
    name="pipeline_delete",
    description=(
        "Delete a pipeline definition and its render code. Workflows "
        "referencing the pipeline will be left with a dangling "
        "pipeline_sources entry — clean those up separately or use "
        "workflow_delete. IRREVERSIBLE."
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
    is_write=True,
)
def pipeline_delete(user, pipeline_id: int, opportunity_id: int):
    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        existing = pda.get_definition(pipeline_id)
        if existing is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")
        pda.delete_definition(pipeline_id)
        return {"pipeline_id": pipeline_id, "deleted": True}
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
