"""Tests for workflow_save_snapshot MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

from commcare_connect.mcp.tool_registry import MCPToolError, get_tool

# Trigger @register
import commcare_connect.mcp.tools.workflow_snapshots  # noqa: F401


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_workflow_save_snapshot_completes_run(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    fake_run = MagicMock()
    fake_run.id = 100
    fake_run.opportunity_id = 4242
    fake_run.is_completed = False
    fake_run.data = {
        "definition_id": 999,
        "state": {"worker_states": {"asha": "ok"}, "spawned_tasks": {}},
    }

    fake_definition = MagicMock()
    fake_definition.template_type = "performance_review"
    fake_definition.opportunity_id = 4242
    fake_definition.opportunity_ids = []  # falls back to [opportunity_id]

    fake_completed = MagicMock()

    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.get_pipeline_data.return_value = {"flw_kpis": {"rows": []}}
    fake_wda.get_workers.return_value = [{"username": "asha"}]
    fake_wda.complete_run.return_value = fake_completed

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)
    # Replace TEMPLATES at the call site so the template lookup succeeds.
    import commcare_connect.workflow.templates as templates_mod

    monkeypatch.setitem(
        templates_mod.TEMPLATES,
        "performance_review",
        {"supports_saved_runs": True},
    )
    monkeypatch.setattr(
        templates_mod,
        "build_snapshot_for_template",
        lambda **kwargs: {
            "metrics": {"workers_reviewed": 1},
            "state": kwargs["state"],
        },
    )

    tool = get_tool("workflow_save_snapshot")
    result = tool.handler(
        user=user,
        run_id=100,
        snapshot_name="Week 1",
        captured_at="2026-02-07T12:00:00Z",
    )

    assert result["run_id"] == 100
    assert result["snapshot_name"] == "Week 1"
    fake_wda.complete_run.assert_called_once()
    call_args = fake_wda.complete_run.call_args
    # First positional should be run_id (100)
    assert call_args.args[0] == 100
    # Second positional should be the snapshot payload
    snapshot = call_args.args[1]
    assert snapshot["name"] == "Week 1"
    assert snapshot["captured_at"] == "2026-02-07T12:00:00Z"
    assert snapshot["metrics"]["workers_reviewed"] == 1
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_save_snapshot_404s_on_missing_run(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    fake_wda = MagicMock()
    fake_wda.get_run.return_value = None

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            run_id=12345,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_workflow_save_snapshot_409s_on_completed_run(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    fake_run = MagicMock()
    fake_run.is_completed = True
    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            run_id=999,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "VERSION_CONFLICT"
