"""Tests for workflow_save_snapshot MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

# Trigger @register
import commcare_connect.mcp.tools.workflow_snapshots  # noqa: F401
from commcare_connect.mcp.tool_registry import MCPToolError, get_tool


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
    # Real dict so resolve_snapshot_contract sees no instance manifest and
    # falls back to the (patched) template registry.
    fake_definition.data = {"name": "Performance Review", "config": {"templateType": "performance_review"}}

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
        "build_snapshot_for_contract",
        lambda contract, **kwargs: {
            "metrics": {"workers_reviewed": 1},
            "state": kwargs["state"],
        },
    )

    tool = get_tool("workflow_save_snapshot")
    result = tool.handler(
        user=user,
        run_id=100,
        opportunity_id=4242,
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
def test_workflow_save_snapshot_passes_opportunity_id_to_wda(user, monkeypatch):
    """Confirms the WDA is constructed with opportunity_id — without this scope
    the upstream GET would only return public records and the run would 404."""
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    captured_kwargs = {}

    def _fake_wda_factory(u, opportunity_id=None):
        captured_kwargs["opportunity_id"] = opportunity_id
        fake_wda = MagicMock()
        fake_wda.get_run.return_value = None  # bail out early; we only care about scope
        return fake_wda

    monkeypatch.setattr(ws, "_wda_for_user", _fake_wda_factory)

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError):
        tool.handler(
            user=user,
            run_id=1,
            opportunity_id=7777,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert captured_kwargs["opportunity_id"] == 7777


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
            opportunity_id=4242,
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
            opportunity_id=4242,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "VERSION_CONFLICT"


@pytest.mark.django_db
def test_workflow_save_snapshot_rejects_mismatched_opp(user, monkeypatch):
    """If the run's opportunity_id doesn't match the param, fail loud rather than
    silently snapshotting under the wrong scope."""
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    fake_run = MagicMock()
    fake_run.id = 100
    fake_run.opportunity_id = 4242  # the run's actual opp
    fake_run.is_completed = False
    fake_run.data = {"definition_id": 999, "state": {}}

    fake_definition = MagicMock()
    fake_definition.template_type = "performance_review"
    fake_definition.opportunity_id = 4242
    fake_definition.opportunity_ids = []

    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run
    fake_wda.get_definition.return_value = fake_definition

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)
    import commcare_connect.workflow.templates as templates_mod

    monkeypatch.setitem(
        templates_mod.TEMPLATES,
        "performance_review",
        {"supports_saved_runs": True},
    )

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            run_id=100,
            opportunity_id=9999,  # mismatch
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "INVALID_SCHEMA"
    assert "9999" in str(exc.value.message)
    fake_wda.complete_run.assert_not_called()
