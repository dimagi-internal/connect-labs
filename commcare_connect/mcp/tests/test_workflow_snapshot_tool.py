"""Tests for workflow_save_snapshot MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

from commcare_connect.mcp.tool_registry import get_tool

# Trigger @register
import commcare_connect.mcp.tools.workflow_snapshots  # noqa: F401


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_workflow_save_snapshot_appends_to_saved_runs(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    fake_workflow = MagicMock()
    fake_workflow.id = 100
    fake_workflow.template_key = "llo_weekly_review"
    fake_workflow.data = {"saved_runs": [], "state": {"worker_states": {"asha": "ok"}}}

    fake_client = MagicMock()
    fake_client.get_workflow.return_value = fake_workflow
    fake_client.update_workflow.return_value = fake_workflow

    monkeypatch.setattr(ws, "_workflow_data_access_for_user", lambda u: fake_client)
    monkeypatch.setattr(
        ws,
        "_build_snapshot",
        lambda template_key, workflow: {"name": "ignored", "metrics": {"workers_reviewed": 1}},
    )

    tool = get_tool("workflow_save_snapshot")
    result = tool.handler(
        user=user,
        workflow_id=100,
        snapshot_name="Week 1",
        captured_at="2026-02-07T12:00:00Z",
    )
    assert result["workflow_id"] == 100
    assert result["snapshot_name"] == "Week 1"
    fake_client.update_workflow.assert_called_once()
    saved_payload = fake_client.update_workflow.call_args.kwargs["data"]
    saved_runs = saved_payload["saved_runs"]
    assert saved_runs[-1]["name"] == "Week 1"
    assert saved_runs[-1]["captured_at"] == "2026-02-07T12:00:00Z"
    assert saved_runs[-1]["metrics"]["workers_reviewed"] == 1
