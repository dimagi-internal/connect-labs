from unittest.mock import MagicMock, patch

from connect_labs.mcp import tool_registry
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.supply_chain.mcp_tools import _make_handler
from connect_labs.supply_chain.operations import all_operations


def test_every_operation_is_exposed_as_an_mcp_tool():
    import connect_labs.supply_chain.mcp_tools  # noqa: F401  -- triggers registration

    for operation_name in all_operations():
        assert get_tool(f"procurement_{operation_name}") is not None, operation_name


def test_the_mcp_tool_count_matches_the_operation_registry():
    """Guards the load-order hazard directly: if a sub-component's operations

    (e.g. a future `tracking`) register into the shared operations registry
    too late for mcp_tools.py's registration loop to see them -- exactly what
    happened to procurement here, because connect_labs.mcp precedes
    connect_labs.supply_chain in INSTALLED_APPS -- this catches it as a count
    mismatch even if every operation that DID make it in looks individually
    fine.
    """
    import connect_labs.supply_chain.mcp_tools  # noqa: F401

    registered_procurement_tools = {
        t["name"] for t in tool_registry.list_tools() if t["name"].startswith("procurement_")
    }
    expected = {f"procurement_{name}" for name in all_operations()}
    assert registered_procurement_tools == expected


def test_mcp_write_flags_match_the_operation_registry():
    import connect_labs.supply_chain.mcp_tools  # noqa: F401

    for name, operation in all_operations().items():
        tool = get_tool(f"procurement_{name}")
        assert tool.is_write == operation.is_write, name


def test_mcp_schemas_add_only_the_two_scope_properties():
    """Plain equality can't hold: _schema_with_scope deliberately adds
    organization_id/program_id to every advertised schema, because an MCP
    caller has no Django session to carry scope. So parity here means the
    tool schema is the operation schema PLUS exactly those two properties,
    unchanged otherwise -- which still catches the injection mangling a
    property, dropping a required field, or adding a third undeclared key.
    """
    import connect_labs.supply_chain.mcp_tools  # noqa: F401

    for name, operation in all_operations().items():
        tool = get_tool(f"procurement_{name}")
        op_schema = operation.input_schema
        tool_schema = tool.input_schema

        # Every operation property survives into the tool schema, unchanged.
        for prop_name, prop_schema in op_schema["properties"].items():
            assert tool_schema["properties"].get(prop_name) == prop_schema, (name, prop_name)

        # The only properties added are the two scope fields -- nothing else leaked in.
        added = set(tool_schema["properties"]) - set(op_schema["properties"])
        assert added == {"organization_id", "program_id"}, name

        # Scope is never required -- required carries through unchanged.
        assert tool_schema["required"] == op_schema["required"], name


def test_mcp_handler_does_not_forward_scope_into_the_operation_payload():
    """organization_id/program_id are consumed by the generated handler's own
    signature to build SupplyDataAccess -- they must never reach
    operation.handler's **payload, which the operation's own (closed) schema
    does not declare them in.
    """
    captured = {}

    class _FakeOperation:
        name = "fake_probe"

        @staticmethod
        def handler(access, **payload):
            captured["access"] = access
            captured["payload"] = payload
            return {"ok": True}

    handler = _make_handler(_FakeOperation)

    with patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"):
        handler(user=MagicMock(), organization_id=7, program_id=9, foo="bar")

    assert captured["payload"] == {"foo": "bar"}
    assert captured["access"].organization_id == 7
    assert captured["access"].program_id == 9
