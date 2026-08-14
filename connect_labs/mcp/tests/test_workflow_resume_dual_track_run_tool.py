"""Tests for the workflow_resume_dual_track_run MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

import connect_labs.mcp.tools.workflow_resume_dual_track_run  # noqa: F401  -- triggers @register
from connect_labs.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


def _def(template_type="weekly_dual_track_audit"):
    d = MagicMock()
    d.template_type = template_type
    return d


@pytest.mark.django_db
def test_resume_happy_path_opp_owned(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_definition = _def()
    fake_run = MagicMock()
    fake_run.definition_id = 100
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.get_run.return_value = fake_run
    fake_wda.access_token = "tok"

    monkeypatch.setattr(m, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    fake_resume = MagicMock(return_value={"run_id": 13364, "task_id": "celery-abc", "status": "running"})
    monkeypatch.setattr("connect_labs.workflow.audit_generation.resume_batch_run", fake_resume)

    tool = get_tool("workflow_resume_dual_track_run")
    result = tool.handler(user=user, run_id=13364, definition_id=100, opportunity_id=4242)

    assert result == {"run_id": 13364, "task_id": "celery-abc", "status": "running"}
    fake_resume.assert_called_once_with(fake_definition, fake_run, access_token="tok")
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_resume_happy_path_program_owned(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_definition = _def()
    fake_run = MagicMock()
    fake_run.definition_id = 12705
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.get_run.return_value = fake_run
    fake_wda.access_token = "tok"

    captured_scope = {}

    def _fake_wda_for_user(u, opportunity_id=None, program_id=None):
        captured_scope["opportunity_id"] = opportunity_id
        captured_scope["program_id"] = program_id
        return fake_wda

    monkeypatch.setattr(m, "_wda_for_user", _fake_wda_for_user)
    fake_resume = MagicMock(return_value={"run_id": 13364, "task_id": "celery-def", "status": "running"})
    monkeypatch.setattr("connect_labs.workflow.audit_generation.resume_batch_run", fake_resume)

    tool = get_tool("workflow_resume_dual_track_run")
    result = tool.handler(user=user, run_id=13364, definition_id=12705, program_id=217)

    assert result == {"run_id": 13364, "task_id": "celery-def", "status": "running"}
    assert captured_scope == {"opportunity_id": None, "program_id": 217}


@pytest.mark.django_db
def test_resume_rejects_neither_scope(user):
    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=1, definition_id=100)
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_resume_rejects_both_scopes(user):
    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=1, definition_id=100, opportunity_id=1, program_id=1)
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_resume_404s_when_definition_missing(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = None
    monkeypatch.setattr(m, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=1, definition_id=999, opportunity_id=4242)
    assert exc.value.code == "NOT_FOUND"
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_resume_rejects_wrong_template_type(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = _def(template_type="bulk_image_audit")
    monkeypatch.setattr(m, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=1, definition_id=100, opportunity_id=4242)
    assert exc.value.code == "INVALID_SCHEMA"
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_resume_404s_when_run_missing(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = _def()
    fake_wda.get_run.return_value = None
    monkeypatch.setattr(m, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=404, definition_id=100, opportunity_id=4242)
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_resume_rejects_run_belonging_to_a_different_definition(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_run = MagicMock()
    fake_run.definition_id = 555  # not 100
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = _def()
    fake_wda.get_run.return_value = fake_run
    monkeypatch.setattr(m, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=1, definition_id=100, opportunity_id=4242)
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_resume_maps_value_error_to_invalid_schema(user, monkeypatch):
    """resume_batch_run raises ValueError when the run has no persisted
    window -- surfaced as INVALID_SCHEMA, same convention as
    workflow_run_default's unsupported-template mapping."""
    from connect_labs.mcp.tools import workflow_resume_dual_track_run as m

    fake_run = MagicMock()
    fake_run.definition_id = 100
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = _def()
    fake_wda.get_run.return_value = fake_run
    fake_wda.access_token = "tok"
    monkeypatch.setattr(m, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    monkeypatch.setattr(
        "connect_labs.workflow.audit_generation.resume_batch_run",
        MagicMock(side_effect=ValueError("run 1 has no window_start/window_end in state; nothing to resume")),
    )

    tool = get_tool("workflow_resume_dual_track_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, run_id=1, definition_id=100, opportunity_id=4242)
    assert exc.value.code == "INVALID_SCHEMA"
    fake_wda.close.assert_called_once()


def test_workflow_resume_dual_track_run_is_registered():
    from connect_labs.mcp.tool_registry import _REGISTRY

    assert "workflow_resume_dual_track_run" in _REGISTRY
