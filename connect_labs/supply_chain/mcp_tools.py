"""MCP adapter. A loop over the operation registry, so every capability the
web pages have is a tool, and a new operation needs no work here.
"""

from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import register
from connect_labs.supply_chain.data_access import SupplyDataAccess

# Procurement's operations register themselves into the shared registry as a side
# effect of importing connect_labs.supply_chain.procurement.operations -- normally
# triggered by SupplyChainConfig.ready(). But connect_labs.mcp is listed BEFORE
# connect_labs.supply_chain in INSTALLED_APPS, so MCPConfig.ready() (which imports
# this module transitively via tools/__init__.py) runs first: without this explicit
# import, all_operations() below would see only the 9 root-level operations and
# silently register no procurement_* tools at all.
from connect_labs.supply_chain.fulfilment import operations as _fulfilment_operations  # noqa: F401
from connect_labs.supply_chain.operations import all_operations, call_operation
from connect_labs.supply_chain.procurement import operations as _procurement_operations  # noqa: F401
from connect_labs.supply_chain.stock import operations as _stock_operations  # noqa: F401


def _make_handler(operation):
    def handler(user, organization_id=None, program_id=None, **payload):
        token = require_connect_token(user)
        access = SupplyDataAccess(
            access_token=token,
            organization_id=organization_id,
            program_id=program_id,
        )
        # Route through call_operation, not operation.handler directly. FastMCP's
        # own schema validation lives in FunctionTool.run, which RegistryTool
        # (connect_labs/mcp/server.py) overrides and never calls -- so
        # call_operation's jsonschema.validate() is the ONLY place the closed
        # schema (money-as-string, enum values, additionalProperties) gets
        # enforced on this surface. organization_id/program_id are already
        # consumed by this handler's own signature above, so they never reach
        # the operation's payload -- the operation's schema does not declare
        # them and would reject them if they did.
        return call_operation(operation.name, access, payload)

    handler.__name__ = f"procurement_{operation.name}"
    return handler


def _schema_with_scope(schema: dict) -> dict:
    """MCP callers have no session, so they pass scope explicitly."""
    scoped = {
        **schema,
        "properties": {
            **schema["properties"],
            "organization_id": {
                "type": "integer",
                "description": (
                    "Organisation that owns the commodity and supplier registries. "
                    "Omitting this on a supplier/commodity/item call does not fall back to "
                    "some org-wide default -- it reads a DIFFERENT, program-scoped registry "
                    "instead, which is empty until something has been written to it. Pass the "
                    "organisation_id whenever one is known, or supplier_list/commodity_list can "
                    "come back empty when the supplier genuinely exists."
                ),
            },
            "program_id": {
                "type": "integer",
                "description": "Programme the rounds, quotes and awards belong to.",
            },
        },
    }
    return scoped


for _operation in all_operations().values():
    register(
        name=f"procurement_{_operation.name}",
        description=_operation.summary,
        input_schema=_schema_with_scope(_operation.input_schema),
        is_write=_operation.is_write,
    )(_make_handler(_operation))
