"""Unit tests for the template-source AST parser."""

from commcare_connect.mcp.tools._template_parser import parse_template_source


def test_parses_literal_render_code():
    source = """
RENDER_CODE = "function WorkflowUI() { return null; }"

DEFINITION = {"name": "X", "statuses": [], "pipeline_sources": []}

TEMPLATE = {"key": "x", "definition": DEFINITION, "render_code": RENDER_CODE}
"""
    result = parse_template_source(source, sidecar_files={})

    assert result.render_code == "function WorkflowUI() { return null; }"
    assert result.definition == {"name": "X", "statuses": [], "pipeline_sources": []}
    assert result.template_key == "x"
    assert result.pipeline_schemas == []
