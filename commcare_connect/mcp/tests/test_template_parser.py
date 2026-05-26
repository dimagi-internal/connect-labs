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


def test_parses_sidecar_render_code():
    source = """
from pathlib import Path

DEFINITION = {"name": "Z", "statuses": [], "pipeline_sources": []}

RENDER_CODE = (Path(__file__).parent / "z_render.js").read_text(encoding="utf-8")

TEMPLATE = {"key": "z", "definition": DEFINITION, "render_code": RENDER_CODE}
"""
    result = parse_template_source(
        source,
        sidecar_files={"z_render.js": "function WorkflowUI() { return 'z'; }"},
    )
    assert result.render_code == "function WorkflowUI() { return 'z'; }"


def test_missing_sidecar_raises():
    source = """
from pathlib import Path

DEFINITION = {"name": "Z", "statuses": [], "pipeline_sources": []}
RENDER_CODE = (Path(__file__).parent / "missing.js").read_text(encoding="utf-8")
TEMPLATE = {"key": "z", "definition": DEFINITION, "render_code": RENDER_CODE}
"""
    with pytest.raises(TemplateParseError, match="missing.js"):
        parse_template_source(source, sidecar_files={})


def test_disallows_arbitrary_call():
    source = """
RENDER_CODE = open("/etc/passwd").read()
DEFINITION = {}
TEMPLATE = {"key": "k"}
"""
    with pytest.raises(TemplateParseError, match="unsupported expression"):
        parse_template_source(source, sidecar_files={})


def test_round_trips_real_mbw_template():
    """Parser must handle the actual on-disk template that triggered this work."""
    from pathlib import Path

    base = Path("commcare_connect/workflow/templates")
    py_source = (base / "mbw_auditing_v4.py").read_text()
    sidecar = (base / "mbw_auditing_v4_render.js").read_text()

    result = parse_template_source(py_source, sidecar_files={"mbw_auditing_v4_render.js": sidecar})

    assert result.template_key == "mbw_auditing_v4"
    assert result.render_code == sidecar
    assert result.definition["name"] == "MBW Auditing V4"
    assert len(result.pipeline_schemas) == 4
    assert {ps["alias"] for ps in result.pipeline_schemas} == {
        "visits",
        "visits_agg",
        "registrations",
        "gs_forms",
    }


# Templates that use Python features beyond the parser's literal-with-names grammar
# (function calls in DEFINITION, list comprehensions, starred unpacking, subscript access).
# They cannot be sync'd via workflow_sync_from_template_file today — either the template
# needs refactoring to pure-literal form, or the parser needs extending.
_PARSER_UNSUPPORTED_TEMPLATES = {
    "kmc_longitudinal",
    "kmc_project_metrics",
    "llo_weekly_review",
    "mbw_monitoring_v3",
    "program_admin_report",
    "sam_followup",
}


@pytest.mark.parametrize(
    "template_basename",
    [
        "audit_with_ai_review",
        "bulk_image_audit",
        "kmc_flw_flags",
        "kmc_longitudinal",
        "kmc_project_metrics",
        "llo_weekly_review",
        "mbw_auditing_v4",
        "mbw_monitoring_v2",
        "mbw_monitoring_v3",
        "ocs_outreach",
        "performance_review",
        "program_admin_report",
        "sam_followup",
    ],
)
def test_parser_handles_every_shipped_template(template_basename):
    """Round-trip each shipped template; xfail the ones that need parser extension."""
    from pathlib import Path

    base = Path("commcare_connect/workflow/templates")
    py_path = base / f"{template_basename}.py"
    if not py_path.exists():
        pytest.skip(f"{py_path} not present on this branch")
    sidecar_files = {}
    sidecar_path = base / f"{template_basename}_render.js"
    if sidecar_path.exists():
        sidecar_files[sidecar_path.name] = sidecar_path.read_text()

    if template_basename in _PARSER_UNSUPPORTED_TEMPLATES:
        with pytest.raises(TemplateParseError):
            parse_template_source(py_path.read_text(), sidecar_files=sidecar_files)
        return

    result = parse_template_source(py_path.read_text(), sidecar_files=sidecar_files)
    assert result.template_key
    assert isinstance(result.definition, dict)
    assert isinstance(result.render_code, str) and result.render_code
