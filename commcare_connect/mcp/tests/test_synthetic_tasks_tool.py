from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

# Trigger @register side effect
import commcare_connect.mcp.tools.synthetic_tasks  # noqa: F401
from commcare_connect.mcp.tool_registry import get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_task_create_synthetic_persists_via_labs_api(user, monkeypatch):
    fake_record = MagicMock()
    fake_record.id = 5001
    fake_record.experiment = "task"
    fake_record.type = "synthetic_task"
    fake_record.data = {
        "title": "Coaching feedback for asha",
        "assigned_to": "asha",
        "ocs_conversation": [{"role": "bot", "text": "Hi", "ts": "2026-03-01T09:00:00Z"}],
        "status": "completed",
    }

    fake_client = MagicMock()
    fake_client.create_record.return_value = fake_record

    from commcare_connect.mcp.tools import synthetic_tasks

    captured = {}

    def _fake_factory(u, opp_id):
        captured["opp_id"] = opp_id
        return fake_client

    monkeypatch.setattr(synthetic_tasks, "_labs_api_for_user", _fake_factory)

    tool = get_tool("task_create_synthetic")
    result = tool.handler(
        user=user,
        opportunity_id=4242,
        assigned_to="asha",
        subject="Coaching feedback for asha",
        ocs_conversation=[{"role": "bot", "text": "Hi", "ts": "2026-03-01T09:00:00Z"}],
    )
    assert result["id"] == 5001
    # The client must be constructed with opportunity_id so production-side
    # membership checks fire on the upstream POST.
    assert captured["opp_id"] == 4242
    fake_client.create_record.assert_called_once()
    call_kwargs = fake_client.create_record.call_args.kwargs
    assert call_kwargs["experiment"] == "task"
    assert call_kwargs["type"] == "synthetic_task"
    assert call_kwargs["data"]["assigned_to"] == "asha"
    assert call_kwargs["data"]["ocs_conversation"][0]["role"] == "bot"
    # opportunity_id is no longer duplicated inside `data`.
    assert "opportunity_id" not in call_kwargs["data"]
