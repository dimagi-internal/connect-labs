"""Unit tests for the template-source AST parser.

The parser walks a Python module's top-level assignments and extracts the
four artifacts a workflow template exposes: RENDER_CODE, DEFINITION,
PIPELINE_SCHEMAS, and TEMPLATE. It deliberately rejects anything outside a
small literal-with-names grammar — no exec(), no eval(), no arbitrary calls.
"""

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
