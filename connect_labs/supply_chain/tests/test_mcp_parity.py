from connect_labs.mcp.tool_registry import get_tool
from connect_labs.supply_chain.operations import all_operations


def test_every_operation_is_exposed_as_an_mcp_tool():
    import connect_labs.supply_chain.mcp_tools  # noqa: F401  -- triggers registration

    for operation_name in all_operations():
        assert get_tool(f"procurement_{operation_name}") is not None, operation_name


def test_mcp_write_flags_match_the_operation_registry():
    import connect_labs.supply_chain.mcp_tools  # noqa: F401

    for name, operation in all_operations().items():
        tool = get_tool(f"procurement_{name}")
        assert tool.is_write == operation.is_write, name


def test_mcp_schemas_are_the_operation_schemas():
    import connect_labs.supply_chain.mcp_tools  # noqa: F401

    for name, operation in all_operations().items():
        tool = get_tool(f"procurement_{name}")
        assert tool.input_schema == operation.input_schema, name
