"""A page, for an agent: what it is built from, read as the caller.

A page (``kind: "page"``, workflow/page_mode.py) is drawn in a sandboxed frame
(workflow/sandbox.py). When it shares with the canopy agent (``config.agent.share``)
its panel names the page and its scope in the page state's filters, and the agent
reads the substance here: the page's name, the scope (names, and what is under it
that the caller can see), the Settings it declares in ``config_reads``, and its
workflow sources' run summaries -- built by the same functions the page view uses.

Refuses a definition that is not a page, and one that does not share, whatever the
token carries (the same rule the ``workflow_*`` run tools follow for canopy).

Not reachable by a restricted ("no user visit data") caller: a workflow source's run
summary can carry indicator values computed from visits (token_scopes.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

OWNER_KEYS = ("opportunity_id", "program_id", "organization_id")


def _request_for(user, token: str, owner: dict) -> SimpleNamespace:
    """A request-shaped object the page functions read: the caller, their org tree, the owner scope."""
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    org_data = fetch_user_organization_data(token, owner=getattr(user, "username", None))
    if org_data is None:
        raise MCPToolError("UPSTREAM_ERROR", "Your organisations could not be read from Connect just now.")
    return SimpleNamespace(
        user=user,
        session={"labs_oauth": {"access_token": token, "organization_data": org_data}},
        labs_context=dict(owner),
    )


@register(
    name="page_context",
    description=(
        "What a labs PAGE is built from, as you (the caller) can see it: its name, the scope it is "
        "about (the organisation, programme or opportunity, with the programmes and opportunities "
        "under it you can see), the Settings it reads, and its workflow sources' latest runs. On a "
        "page's agent panel, read every argument from the page state's filters (definition_id, "
        "scope_type, scope_key and the owner: opportunity_id, program_id or organization_id). "
        "Refuses a workflow that is not a page, or a page that does not share with the agent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer"},
            "scope_type": {"type": "string", "enum": ["organization", "program", "opportunity"]},
            "scope_key": {
                "type": "string",
                "description": "An organisation's slug, or a programme's or opportunity's id.",
            },
            "opportunity_id": {
                "type": "integer",
                "description": "The owner the page's record lives in (one of three).",
            },
            "program_id": {"type": "integer"},
            "organization_id": {"type": "integer"},
        },
        "required": ["definition_id", "scope_type", "scope_key"],
        "additionalProperties": False,
    },
)
def page_context(
    user, *, definition_id, scope_type, scope_key, opportunity_id=None, program_id=None, organization_id=None
) -> dict[str, Any]:
    from connect_labs.labs.access.scopes import Caller
    from connect_labs.labs.synthetic.access import labs_only_scope_denied_reason
    from connect_labs.scope_config import service
    from connect_labs.scope_config.scopes import Scope
    from connect_labs.workflow import workflow_sources
    from connect_labs.workflow.agent_sharing import definition_shares_with_agent
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.page_mode import is_page, page_config, page_scope

    owner = {
        k: int(v)
        for k, v in (
            ("opportunity_id", opportunity_id),
            ("program_id", program_id),
            ("organization_id", organization_id),
        )
        if v not in (None, "")
    }
    if len(owner) != 1:
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id / organization_id.")
    try:
        scope = Scope.of(scope_type, scope_key)
    except ValueError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc)) from None

    token = require_connect_token(user)
    if service.refusal(Caller(user=user, access_token=token), scope):
        raise MCPToolError("PERMISSION_DENIED", "That scope is not one you can use.")
    if labs_only_scope_denied_reason(
        user, opportunity_id=owner.get("opportunity_id"), program_id=owner.get("program_id")
    ):
        raise MCPToolError("NOT_FOUND", f"workflow {definition_id} is not there, or you cannot open it")

    access = WorkflowDataAccess(access_token=token, **owner)
    try:
        definition = access.get_definition(int(definition_id))
    finally:
        access.close()
    if definition is None:
        raise MCPToolError("NOT_FOUND", f"workflow {definition_id} is not there, or you cannot open it")
    if not is_page(definition):
        raise MCPToolError("INVALID_SCHEMA", f"workflow {definition_id} is not a page; use the workflow_* tools.")
    if not definition_shares_with_agent(definition):
        raise MCPToolError(
            "PERMISSION_DENIED",
            "This page does not share with the embedded agent (its definition's config.agent.share is off).",
        )
    from .workflow_run import _delegated_token

    if _delegated_token() is not None:
        # Canopy for a visitor: its workflow sources' run summaries are computed from
        # visits, so over real data only a cleared agent (labs/canopy_panel.py) -- the
        # rule that also keeps the panel off such a page.
        from connect_labs.labs import canopy_panel

        clearance = canopy_panel.clearance_for(canopy_panel.page_reads(definition, token))
        if not clearance.granted:
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"Through canopy, this page is read only when its sources hold generated data: {clearance.reason}.",
            )

    request = _request_for(user, token, owner)
    data = definition.data or {}
    return {
        "page": {
            "id": definition.id,
            "name": definition.name,
            "description": data.get("description", ""),
            "slug": (data.get("page") or {}).get("slug"),
        },
        "scope": page_scope(request, scope),
        "config": page_config(request, definition, scope),
        "workflows": {
            alias: workflow_sources.read(request, src) for alias, src in workflow_sources.declared(definition).items()
        },
    }
