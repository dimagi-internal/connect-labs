from unittest.mock import MagicMock, patch

import pytest

from connect_labs.mcp import tool_registry
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.supply_chain import operations as operations_module
from connect_labs.supply_chain.mcp_tools import _make_handler
from connect_labs.supply_chain.operations import all_operations, get_operation


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
    call_operation's payload, which the operation's own (closed) schema does
    not declare them in.

    Routed through the real registry (via patch.dict, restored automatically)
    rather than a bare handler stub: _make_handler now calls call_operation by
    name (finding 1's fix), which does its own get_operation(name) lookup, so
    a fake operation object with no real schema no longer exercises the real
    path. Assertions are unchanged from before that fix.
    """
    captured = {}

    def _fake_handler(access, **payload):
        captured["access"] = access
        captured["payload"] = payload
        return {"ok": True}

    fake_operation = operations_module.Operation(
        name="fake_probe",
        summary="test probe for scope-stripping, not a real capability",
        input_schema=operations_module._obj({"foo": {"type": "string"}}),
        handler=_fake_handler,
    )

    class _FakeOperationRef:
        name = "fake_probe"

    with patch.dict(operations_module._REGISTRY, {"fake_probe": fake_operation}):
        handler = _make_handler(_FakeOperationRef)
        with patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"):
            handler(user=MagicMock(), organization_id=7, program_id=9, foo="bar")

    assert captured["payload"] == {"foo": "bar"}
    assert captured["access"].organization_id == 7
    assert captured["access"].program_id == 9


# --- Finding 1: the MCP path must enforce the same schema the HTTP API does -----
#
# Before the fix, _make_handler called operation.handler(access, **payload)
# directly, skipping call_operation's jsonschema.validate() entirely. FastMCP's
# own validation lives in FunctionTool.run, which RegistryTool overrides and
# never calls -- so nothing downstream validated either. These three pin the
# proven repro: a float money amount, a value outside an enum, and an
# undeclared top-level key all used to sail through on this surface while the
# identical payload 400s on the HTTP API.


def test_the_mcp_path_rejects_a_float_money_amount():
    handler = _make_handler(get_operation("quote_record"))
    with patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"):
        with pytest.raises(Exception):
            handler(
                user=MagicMock(),
                program_id=9,
                data={
                    "round_id": 1,
                    "supplier_id": 2,
                    "commodity_slug": "rutf",
                    "as_quoted_unit": "per_pack",
                    "as_quoted_amount": 12.50,
                },
            )


def test_the_mcp_path_rejects_a_value_outside_the_enum():
    handler = _make_handler(get_operation("quote_record"))
    with patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"):
        with pytest.raises(Exception):
            handler(
                user=MagicMock(),
                program_id=9,
                data={
                    "round_id": 1,
                    "supplier_id": 2,
                    "commodity_slug": "rutf",
                    "as_quoted_unit": "per_banana",
                    "as_quoted_amount": "12.50",
                },
            )


def test_the_mcp_path_rejects_an_undeclared_top_level_key():
    """The exact proven repro: additionalProperties: False on the operation's
    top-level schema must bind on the MCP surface too."""
    handler = _make_handler(get_operation("quote_record"))
    with patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"):
        with pytest.raises(Exception):
            handler(
                user=MagicMock(),
                program_id=9,
                data={
                    "round_id": 1,
                    "supplier_id": 2,
                    "commodity_slug": "rutf",
                    "as_quoted_unit": "per_pack",
                    "as_quoted_amount": "12.50",
                },
                undeclared_key="x",
            )
