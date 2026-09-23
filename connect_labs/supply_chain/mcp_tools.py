"""MCP adapter. A loop over the operation registry, so every capability the
web pages have is a tool, and a new operation needs no work here.
"""

from connect_labs.labs.access.scopes import Caller
from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import register

# Procurement's operations register themselves into the shared registry as a side
# effect of importing connect_labs.supply_chain.procurement.operations -- normally
# triggered by SupplyChainConfig.ready(). But connect_labs.mcp is listed BEFORE
# connect_labs.supply_chain in INSTALLED_APPS, so MCPConfig.ready() (which imports
# this module transitively via tools/__init__.py) runs first: without this explicit
# import, all_operations() below would see only the 9 root-level operations and
# silently register no supply_* tools at all.
from connect_labs.supply_chain.alerts import operations as _alert_operations  # noqa: F401
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.fulfilment import operations as _fulfilment_operations  # noqa: F401
from connect_labs.supply_chain.operations import agent_operations, call_operation
from connect_labs.supply_chain.procurement import operations as _procurement_operations  # noqa: F401
from connect_labs.supply_chain.stock import operations as _stock_operations  # noqa: F401
from connect_labs.supply_chain.update_links import operations as _update_link_operations  # noqa: F401


def _make_handler(operation):
    def handler(user, organization_id=None, program_id=None, **payload):
        token = require_connect_token(user)
        access = SupplyDataAccess(
            access_token=token,
            organization_id=organization_id,
            program_id=program_id,
            # There is no session on this route, so identity resolution goes
            # through the user's Connect token (cached with a TTL, so a write
            # is not a round trip). Without this an MCP write could only
            # believe whatever organisation the payload claimed.
            user=user,
            # ...and the same resolution decides whether the organisation and
            # programme named above may be used at all. One construction
            # authorises all ~70 generated tools.
            caller=Caller(user=user, access_token=token),
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

    handler.__name__ = f"{TOOL_PREFIX}{operation.name}"
    return handler


# The domain is supply, and only a third of it is procurement. The tools were
# prefixed `procurement_` when sourcing was all there was, and the name stuck
# through fulfilment, stock and distribution -- so `procurement_stock_on_hand`
# and `procurement_distribution_record` told a reader the opposite of what
# they do. Renaming is a breaking change to 73 tool names, taken once, rather
# than carrying a lie in every one of them.
#
# `supply_` alone would collide: the OES demo app already registers
# `supply_demo_reseed`, and the parity test -- which asserts that every tool
# under this prefix came from this registry -- caught it immediately. The app
# label is `supply_chain`, so that is the prefix, and it is one character
# longer than the wrong name it replaces.
TOOL_PREFIX = "supply_chain_"


def _schema_with_scope(schema: dict) -> dict:
    """MCP callers have no session, so they pass scope explicitly."""
    scoped = {
        **schema,
        "properties": {
            **schema["properties"],
            "organization_id": {
                "type": "integer",
                "description": (
                    "Connect organisation, carried as context. It does NOT select a registry: "
                    "commodities, trade items and suppliers are scoped to the programme. This "
                    "description used to say the opposite and tell you to pass it whenever one "
                    "was known -- which was the advice that made commodity_list and "
                    "supplier_list come back empty against a programme whose data was there."
                ),
            },
            "program_id": {
                "type": "integer",
                "description": (
                    "Programme everything belongs to: the rounds, quotes and awards, and also "
                    "the commodity, trade item and supplier registries. Required in practice — "
                    "without it a read refuses rather than guessing a scope."
                ),
            },
        },
    }
    return scoped


# `agent_operations`, not `all_operations`: seeds, bulk imports and ingests are
# run by an engineer through a management command, and advertising them to every
# MCP client puts a bulk data load one mistaken tool call away.
for _operation in agent_operations().values():
    register(
        name=f"{TOOL_PREFIX}{_operation.name}",
        description=_operation.summary,
        input_schema=_schema_with_scope(_operation.input_schema),
        is_write=_operation.is_write,
    )(_make_handler(_operation))
