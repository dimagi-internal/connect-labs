"""Workflow tools — live-instance iteration from Claude Code."""

from commcare_connect.workflow.data_access import WorkflowDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register


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
