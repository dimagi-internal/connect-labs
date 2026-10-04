# connect_labs/mcp/tools/pipelines.py
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

from connect_labs.labs.analysis.backends.sql.query_builder import generate_sql_preview
from connect_labs.labs.analysis.config import VALID_AGGREGATIONS, field_name_problem, schema_grouping_problems
from connect_labs.workflow.data_access import PipelineDataAccess, serialize_pipeline_row

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


def _fields_suspect(rows: list[dict], schema: dict | None) -> list[dict]:
    """Fields whose numbers cannot be true but are not null, so
    `_fields_all_null` is structurally blind to them.

    `fields_all_null` only fires when a field extracted NOTHING. A field that
    extracted the WRONG thing sails past it, and the two shapes that cost real
    money both do (dimagi-internal/ace#2431, on labs opp 10065):

    * a `count` that equals that row's `total_visits` on every row — the
      signature of counting a value that is present on every visit, e.g. the
      `{}` a JSONB column carries when there is nothing to report. A dashboard
      then shows "137 holds" as 2,207 and reads as plausible.
    * a field carrying a `filter_value` that matched nothing on any row. A
      filter is declared because SOME rows are expected to match; zero
      everywhere means the comparison never had a chance, not that the data is
      clean.

    Both are heuristics, so this is a diagnostic on a preview — reported, never
    enforced. A genuinely all-flagged cohort trips the first; a filter that
    legitimately has no matches in the sample trips the second. That is the
    right trade for a signal whose absence let a wrong number reach a funder.
    """
    if not rows or not schema or not isinstance(schema, dict):
        return []
    fields = [f for f in (schema.get("fields") or []) if isinstance(f, dict) and f.get("name")]
    if not fields:
        return []

    out: list[dict] = []
    for f in fields:
        name = f["name"]
        values = [r.get(name) for r in rows if name in r]
        if not values:
            continue

        if f.get("aggregation") == "count":
            totals = [r.get("total_visits") for r in rows if name in r]
            comparable = [
                (v, t)
                for v, t in zip(values, totals)
                if isinstance(v, int) and isinstance(t, int) and not isinstance(v, bool) and t > 0
            ]
            if comparable and len(comparable) == len(values) and all(v == t for v, t in comparable):
                out.append(
                    {
                        "name": name,
                        "signal": "equals_row_count",
                        "detail": (
                            f"`{name}` equals that row's total_visits on all {len(values)} sampled rows. "
                            "A count over a value that is present on every visit looks like this — "
                            "check whether field.path names a column that is never empty (a JSONB "
                            "column storing `{}` was the reported case)."
                        ),
                    }
                )
                continue

        if f.get("filter_value") and (f.get("filter_path") or f.get("filter_paths")):
            if all(v in (None, 0, "", [], {}) for v in values):
                out.append(
                    {
                        "name": name,
                        "signal": "filter_matched_nothing",
                        "detail": (
                            f"`{name}` declares filter_value={f['filter_value']!r} and matched "
                            f"nothing on all {len(values)} sampled rows. Confirm the filter path "
                            "and the exact stored spelling of the value."
                        ),
                    }
                )
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


_PROGRAM_SCOPE_DOC = (
    "Scope the pipeline record by its owning program instead of an opportunity. "
    "Provide this OR opportunity_id. A program-owned pipeline with a gdrive data_source is "
    "stamped for the PROGRAM: it is read once for the whole program (not per opportunity) "
    "and only members of the program's managing organization may read it."
)


def _record_scope(opportunity_id, program_id) -> dict:
    """Exactly one of opportunity_id / program_id, as PipelineDataAccess kwargs."""
    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")
    return {"program_id": program_id} if program_id is not None else {"opportunity_id": opportunity_id}


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
            "opportunity_id": {"type": "integer", "description": "Owning opportunity. Provide this OR program_id."},
            "program_id": {"type": "integer", "description": _PROGRAM_SCOPE_DOC},
        },
        "required": ["pipeline_id"],
        "additionalProperties": False,
    },
)
def pipeline_get(user, pipeline_id: int, opportunity_id: int = None, program_id: int = None):
    scope = _record_scope(opportunity_id, program_id)
    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, **scope)
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


# Deliberately NOT a second list. `VALID_AGGREGATIONS` is derived from the
# `AggregationType` Literal in labs/analysis/config.py, which is the same
# constant the pipeline engine validates against — so an aggregation the SQL
# builder implements can never be rejected here for having been forgotten.
# The hand-maintained copy this replaced had drifted by four names (#1183).


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
        problem = field_name_problem(f.get("name"))
        if problem:
            raise MCPToolError("INVALID_SCHEMA", f"schema.fields[{i}]: {problem}")
        agg = f.get("aggregation")
        if agg and agg not in VALID_AGGREGATIONS:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Unknown aggregation {agg!r} on field {f.get('name', '<unnamed>')!r}. "
                f"Valid: {sorted(VALID_AGGREGATIONS)}",
            )
    # group_by / groupings (entity stage): every key, field and filter must name a
    # declared field (or a base column) -- refused here rather than as a SQL error.
    grouping_problems = schema_grouping_problems(schema)
    if grouping_problems:
        raise MCPToolError("INVALID_SCHEMA", "; ".join(grouping_problems))


def _authorize_drive_source(
    schema: dict,
    opportunity_id: int | None,
    user,
    previous_schema=None,
    force=False,
    *,
    pipeline_id=None,
    program_id: int | None = None,
) -> dict:
    """Settle a gdrive data_source's authorization before save/preview; see gdrive_fetcher.

    The stamp's scope follows the pipeline's: its opportunity, or (opportunity_id
    None) its program."""
    from connect_labs.labs.analysis.backends.sql.gdrive_fetcher import (
        GDriveSourceError,
        authorize_schema_drive_source,
    )

    try:
        return authorize_schema_drive_source(
            schema, opportunity_id, user, previous_schema, force, pipeline_id=pipeline_id, program_id=program_id
        )
    except GDriveSourceError as e:
        raise MCPToolError("PERMISSION_DENIED", str(e))
    except ValueError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e))


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
        "which also reports them in `fields_all_null`.\n\n"
        "A `gdrive` data_source (see WORKFLOW_REFERENCE.md, Google Drive sources) is "
        "authorized here: setting or changing its file_id/folder_id/file_pattern needs "
        "Dimagi staff and stamps it for this pipeline's scope -- its opportunity, or with "
        "program_id its PROGRAM (then read once for the whole program, and only by members "
        "of the program's managing organization). Re-saving an unchanged target "
        "keeps its stamp; pass authorize_drive_source=true to authorize an unchanged, "
        "unstamped target deliberately."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer", "description": "Owning opportunity. Provide this OR program_id."},
            "program_id": {"type": "integer", "description": _PROGRAM_SCOPE_DOC},
            "schema": {"type": "object"},
            "expected_version": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "authorize_drive_source": {"type": "boolean", "default": False},
        },
        "required": ["pipeline_id", "schema", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pipeline_update_schema(
    user,
    pipeline_id: int,
    schema: dict,
    expected_version: int,
    opportunity_id: int = None,
    program_id: int = None,
    name: str = None,
    description: str = None,
    authorize_drive_source: bool = False,
):
    scope = _record_scope(opportunity_id, program_id)
    _validate_pipeline_schema(schema)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, **scope)
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

        schema = _authorize_drive_source(
            schema,
            opportunity_id,
            user,
            previous_schema=current.schema,
            force=authorize_drive_source,
            pipeline_id=pipeline_id,
            program_id=program_id,
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
    name="pipeline_create",
    description=(
        "Create a pipeline definition in an opportunity -- or, with program_id, in a program -- "
        "from a schema, returning its id. Attach it to a workflow with "
        "workflow_add_pipeline_source. A `gdrive` data_source (see WORKFLOW_REFERENCE.md, Google "
        "Drive sources) needs Dimagi staff and a target inside the workflow-data folder; it is "
        "authorized for the new pipeline as part of the create, in the pipeline's scope. A "
        "program-scoped Drive pipeline is read ONCE for the whole program (rows tagged "
        "opportunity_id null), and only members of the program's managing organization can read it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer", "description": "Owning opportunity. Provide this OR program_id."},
            "program_id": {"type": "integer", "description": _PROGRAM_SCOPE_DOC},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "schema": {"type": "object"},
        },
        "required": ["name", "schema"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pipeline_create(
    user, name: str, schema: dict, description: str = "", opportunity_id: int = None, program_id: int = None
):
    scope = _record_scope(opportunity_id, program_id)
    _validate_pipeline_schema(schema)
    is_drive = (schema.get("data_source") or {}).get("type") == "gdrive"
    if is_drive:
        # Refuse BEFORE writing anything: staff, shape and containment are all checked
        # by authorizing once against a placeholder id; the real stamp needs the new id.
        _authorize_drive_source(schema, opportunity_id, user, pipeline_id=0, program_id=program_id)
        source = {k: v for k, v in schema["data_source"].items() if k != "authorization"}
        schema = {**schema, "data_source": source}

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, **scope)
    try:
        created = pda.create_definition(name=name, description=description, schema=schema)
        version = created.version
        if is_drive:
            stamped = _authorize_drive_source(
                schema, opportunity_id, user, pipeline_id=created.id, program_id=program_id
            )
            version = pda.update_definition(definition_id=created.id, schema=stamped).version
        return {"pipeline_id": created.id, "version": version}
    finally:
        if hasattr(pda, "close"):
            pda.close()


_PIPELINE_PREVIEW_MAX_ROWS = 200


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
        "re-previewing. `fields_suspect` is its sibling for fields that "
        "extracted the WRONG value rather than none: a count equal to the row "
        "count, or a filter that matched nothing on any row. Neither is an "
        "error — both are numbers to re-derive before publishing. "
        "This is the iteration hot path: read → tweak → preview → save. "
        "A program-owned pipeline previews with program_id in place of opportunity_id; that "
        "works for a program-scoped Google Drive pipeline (read once for the program, rows "
        "tagged opportunity_id null, caller must be in the program's managing organization)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer", "description": "Provide this OR program_id."},
            "program_id": {"type": "integer", "description": _PROGRAM_SCOPE_DOC},
            "sample_size": {"type": "integer", "default": 50, "minimum": 1, "maximum": _PIPELINE_PREVIEW_MAX_ROWS},
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
        "required": ["pipeline_id"],
        "additionalProperties": False,
    },
)
def pipeline_preview(
    user,
    pipeline_id: int,
    opportunity_id: int = None,
    sample_size: int = 50,
    schema_override: dict = None,
    opportunity_ids: list[int] = None,
    program_id: int = None,
):
    scope = _record_scope(opportunity_id, program_id)
    if not 1 <= sample_size <= _PIPELINE_PREVIEW_MAX_ROWS:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"sample_size must be between 1 and {_PIPELINE_PREVIEW_MAX_ROWS}. Got {sample_size}.",
        )
    if schema_override is not None:
        _validate_pipeline_schema(schema_override)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, **scope)

    # Decide which opps to fan out across. Caller-supplied opportunity_ids
    # always includes the primary opp implicitly. A program-scoped preview has no
    # opportunity: it reads once, as None (see the program check below).
    target_opps: list = []
    seen: set = set()
    for oid in [opportunity_id] + list(opportunity_ids or []):
        if oid in seen or (oid is None and program_id is None):
            continue
        seen.add(oid)
        target_opps.append(oid)

    def _single_opp_preview(opp_id: int | None) -> dict:
        """Run the preview against one opp. Returns {"rows": [...], "metadata": {...}}.
        Never raises on execution error — failures come back as metadata.error,
        same contract as execute_pipeline."""
        if schema_override is not None:
            # Use the override schema directly via the lower-level API.
            # _schema_to_config converts a schema dict → AnalysisPipelineConfig.
            # We then call AnalysisPipeline directly, bypassing execute_pipeline
            # (which reads the schema from the definition record, not from our override).
            from connect_labs.labs.analysis.pipeline import AnalysisPipeline

            try:
                config = pda._schema_to_config(schema_override, pipeline_id)
                pipeline = AnalysisPipeline(access_token=token)
                raw_result = pipeline.stream_analysis_ignore_events(config, opp_id)
            except Exception as e:
                return {"rows": [], "metadata": {"error": str(e)}}

            # Same shared serializer as the dashboard payloads. This block used
            # to hand-roll its own row dict and omitted `entity_id`,
            # `entity_name`, `status` and `flagged` — so previewing a
            # visit_level pipeline reported no review outcome, which is exactly
            # the defect ace#1657 hit on the live dashboard payload, on the
            # surface an author uses to check a pipeline BEFORE wiring a
            # dashboard to it.
            rows = []
            if hasattr(raw_result, "rows"):
                rows = [serialize_pipeline_row(row) for row in raw_result.rows]

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

        if schema_override is not None:
            # Against the SAVED schema: an unchanged Drive target previews under its
            # stored stamp; a new one needs Dimagi staff (stamped for this preview only).
            schema_override = _authorize_drive_source(
                schema_override,
                opportunity_id,
                user,
                previous_schema=(definition.data or {}).get("schema"),
                pipeline_id=pipeline_id,
                program_id=program_id,
            )

        # Execution schema used for error-hint generation (override wins when
        # provided; otherwise the saved schema).
        error_hint_schema = schema_override if schema_override is not None else (definition.data or {}).get("schema")

        # A program-scoped Drive source belongs to the program: read ONCE, with no
        # opportunity, whatever opportunity_ids were passed. Nothing else can be read
        # in a program scope, which has no opportunity's data to read.
        from connect_labs.workflow.data_access import program_drive_scope

        try:
            program_read = program_drive_scope(pda._schema_to_config(error_hint_schema or {}, pipeline_id))
        except Exception:  # noqa: BLE001 -- a malformed schema is reported by the run below
            program_read = None
        if program_read is not None:
            target_opps = [None]
        elif program_id is not None:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"pipeline {pipeline_id} is not a program-scoped Google Drive pipeline, so it has no "
                "program-level data to preview; pass opportunity_id (and opportunity_ids) instead.",
            )

        # Without access to user visit data, only the opportunity's own visits may be
        # read -- generated ones, which visit_access has already checked. Every other
        # data source (a Connect export endpoint, OCS sessions under the server's key,
        # CommCare HQ forms/cases) reads a REAL system that the opportunity check
        # cannot see, whatever opportunity the call names.
        from ..visit_access import caller_restricted

        if caller_restricted():
            source_type = ((error_hint_schema or {}).get("data_source") or {}).get("type") or "connect_csv"
            if source_type != "connect_csv":
                raise MCPToolError(
                    "PERMISSION_DENIED",
                    f"This pipeline reads data_source.type={source_type!r}, which is real data outside the "
                    "opportunity; without access to user visit data a preview may only read the "
                    "opportunity's own (generated) visits.",
                )

        merged_rows: list[dict] = []
        per_opp_metadata: dict[str, dict] = {}
        first_error: str | None = None

        for oid in target_opps:
            res = _single_opp_preview(oid)
            md = res.get("metadata") or {}
            per_opp_metadata[f"program:{program_read}" if oid is None else str(oid)] = md
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
            if "Google Drive source: " in first_error:
                # The user's to fix (authorize, share, membership, file shape) -- not an
                # upstream failure, and no SQL hint applies.
                denied = any(
                    m in first_error
                    for m in ("authorized", "not a member", "who is reading", "manages program", "no opportunity")
                )
                raise MCPToolError("PERMISSION_DENIED" if denied else "BAD_REQUEST", first_error)
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
        # The sibling signal for fields that extracted the WRONG thing rather
        # than nothing — invisible to fields_all_null by construction.
        fields_suspect = _fields_suspect(merged_rows, exec_schema)

        return {
            "pipeline_id": pipeline_id,
            "opportunity_id": opportunity_id,
            "opportunity_ids": target_opps if len(target_opps) > 1 else None,
            **({"program_id": program_read, "read_once_for_program": True} if program_read is not None else {}),
            "rows": merged_rows[:sample_size],
            "row_count_before_sample": len(merged_rows),
            "used_schema_override": schema_override is not None,
            "per_opp_metadata": per_opp_metadata,
            "fields_all_null": fields_all_null,
            "fields_all_null_hint": (
                (
                    "These custom fields extracted null for every row. Usually means "
                    "field.path is wrong — use `get_form_json_paths` on the local "
                    "`commcare_hq_mcp` server to look up the real JSON path, then "
                    "re-preview with schema_override."
                )
                if fields_all_null
                else None
            ),
            "fields_suspect": fields_suspect,
            "fields_suspect_hint": (
                (
                    "These fields returned values that are not null but cannot be right — a count "
                    "equal to the row count, or a filter that matched nothing anywhere. Re-check "
                    "field.path and filter_value against the real data before trusting the numbers."
                )
                if fields_suspect
                else None
            ),
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
            "opportunity_id": {"type": "integer", "description": "Owning opportunity. Provide this OR program_id."},
            "program_id": {"type": "integer", "description": "Owning program, for a program-owned pipeline."},
        },
        "required": ["pipeline_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pipeline_delete(user, pipeline_id: int, opportunity_id: int = None, program_id: int = None):
    scope = _record_scope(opportunity_id, program_id)
    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, **scope)
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
    name="pipeline_set_shared",
    description=(
        "Share (or unshare) a pipeline: sets its record PUBLIC, so any signed-in user can "
        "read it -- while edits still need the pipeline's own scope. A workflow in another "
        "scope, e.g. a synthetic report running on the real report's pipeline, can then "
        "reference it with workflow_add_pipeline_source(home_scope={public: true}) and every "
        "viewer can load it, whatever their memberships. Pipelines are definitions, not "
        "data: sharing one exposes its extraction rules, never any visits."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer", "description": "The pipeline's own (owning) opportunity."},
            "shared": {"type": "boolean"},
        },
        "required": ["pipeline_id", "opportunity_id", "shared"],
        "additionalProperties": False,
    },
    is_write=True,
)
def pipeline_set_shared(user, pipeline_id: int, opportunity_id: int, shared: bool):
    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        if pda.get_definition(pipeline_id) is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id} in opportunity {opportunity_id}")
        updated = pda.share_pipeline(pipeline_id) if shared else pda.unshare_pipeline(pipeline_id)
        if updated is None:
            raise MCPToolError("UPSTREAM_ERROR", f"pipeline {pipeline_id} could not be updated")
        return {"pipeline_id": pipeline_id, "shared": bool(shared)}
    finally:
        if hasattr(pda, "close"):
            pda.close()


@register(
    name="pipeline_sql",
    description=(
        "Return the SQL the pipeline would execute, without running it. "
        "Useful for debugging. schema_override previews unsaved changes. "
        "A pipeline with group_by / groupings also gets `sql.grouped_aggregation_sql`: the "
        "one extraction pass and each grouping's GROUP BY. A program-owned pipeline takes "
        "program_id in place of opportunity_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "integer"},
            "opportunity_id": {"type": "integer", "description": "Provide this OR program_id."},
            "program_id": {"type": "integer", "description": _PROGRAM_SCOPE_DOC},
            "schema_override": {"type": "object"},
        },
        "required": ["pipeline_id"],
        "additionalProperties": False,
    },
)
def pipeline_sql(
    user,
    pipeline_id: int,
    opportunity_id: int = None,
    schema_override: dict = None,
    program_id: int = None,
):
    scope = _record_scope(opportunity_id, program_id)
    if schema_override is not None:
        _validate_pipeline_schema(schema_override)

    token = require_connect_token(user)
    pda = PipelineDataAccess(access_token=token, **scope)
    try:
        definition = pda.get_definition(pipeline_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No pipeline with id {pipeline_id}")

        schema = schema_override if schema_override is not None else definition.schema
        try:
            config = pda._schema_to_config(schema, pipeline_id)
        except ValueError as e:
            raise MCPToolError("INVALID_SCHEMA", str(e))

        # The SQL's opportunity scope is the one the rows are cached under: a
        # program-scoped Drive source's program key, else the opportunity.
        from connect_labs.labs.analysis.backends.sql.gdrive_fetcher import drive_cache_scope, program_cache_scope

        cache_scope = drive_cache_scope(config.data_source, opportunity_id)
        if cache_scope is None:
            cache_scope = program_cache_scope(program_id)
        sql_info = generate_sql_preview(config, cache_scope)
        return {
            "pipeline_id": pipeline_id,
            "opportunity_id": opportunity_id,
            **({"program_id": program_id} if program_id is not None else {}),
            "sql": sql_info,
            "used_schema_override": schema_override is not None,
        }
    finally:
        if hasattr(pda, "close"):
            pda.close()
