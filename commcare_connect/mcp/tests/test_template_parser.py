"""Unit tests for the template-source AST parser."""

import pytest

from commcare_connect.mcp.tools._template_parser import TemplateParseError, parse_template_source


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


def test_parses_pipeline_schemas_with_name_references():
    source = """
RENDER_CODE = "ui"

_PATHS = ["form.a", "form.b"]

VISITS_SCHEMA = {"fields": [{"name": "x", "paths": _PATHS}]}

DEFINITION = {"name": "Y", "statuses": [], "pipeline_sources": []}

PIPELINE_SCHEMAS = [
    {"alias": "visits", "name": "Visits", "schema": VISITS_SCHEMA},
]

TEMPLATE = {"key": "y", "definition": DEFINITION, "render_code": RENDER_CODE,
            "pipeline_schemas": PIPELINE_SCHEMAS}
"""
    result = parse_template_source(source, sidecar_files={})

    assert result.pipeline_schemas == [
        {
            "alias": "visits",
            "name": "Visits",
            "schema": {"fields": [{"name": "x", "paths": ["form.a", "form.b"]}]},
        }
    ]


def test_unknown_name_raises():
    source = 'RENDER_CODE = NOT_DEFINED\nDEFINITION = {}\nTEMPLATE = {"key": "k"}'
    with pytest.raises(TemplateParseError, match="unknown name NOT_DEFINED"):
        parse_template_source(source, sidecar_files={})


def test_parses_tuples_sets_and_negative_numbers():
    source = """
RENDER_CODE = "ui"
DEFINITION = {
    "name": "Z",
    "statuses": [],
    "pipeline_sources": [],
    "limits": (-1, 0, 1),
    "tags": {"a", "b"},
}
TEMPLATE = {"key": "z", "definition": DEFINITION, "render_code": RENDER_CODE}
"""
    result = parse_template_source(source, sidecar_files={})
    assert result.definition["limits"] == (-1, 0, 1)
    assert result.definition["tags"] == {"a", "b"}
