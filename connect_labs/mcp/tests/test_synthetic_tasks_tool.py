from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

# Trigger @register side effect
import connect_labs.mcp.tools.synthetic_tasks  # noqa: F401
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_task_create_synthetic_persists_via_labs_api(user, monkeypatch):
    SyntheticOpportunity.objects.create(opportunity_id=10_4242, gdrive_folder_id="", labs_only=True, created_by=user)
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

    from connect_labs.mcp.tools import synthetic_tasks

    captured = {}

    def _fake_factory(u, opp_id):
        captured["opp_id"] = opp_id
        return fake_client

    monkeypatch.setattr(synthetic_tasks, "_labs_api_for_user", _fake_factory)

    tool = get_tool("task_create_synthetic")
    result = tool.handler(
        user=user,
        opportunity_id=10_4242,
        assigned_to="asha",
        subject="Coaching feedback for asha",
        ocs_conversation=[{"role": "bot", "text": "Hi", "ts": "2026-03-01T09:00:00Z"}],
    )
    assert result["id"] == 5001
    # The client must be constructed with opportunity_id so production-side
    # membership checks fire on the upstream POST.
    assert captured["opp_id"] == 10_4242
    fake_client.create_record.assert_called_once()
    call_kwargs = fake_client.create_record.call_args.kwargs
    assert call_kwargs["experiment"] == "task"
    assert call_kwargs["type"] == "synthetic_task"
    assert call_kwargs["data"]["assigned_to"] == "asha"
    assert call_kwargs["data"]["ocs_conversation"][0]["role"] == "bot"
    # opportunity_id is no longer duplicated inside `data`.
    assert "opportunity_id" not in call_kwargs["data"]


def _call(user, opportunity_id):
    return get_tool("task_create_synthetic").handler(
        user=user,
        opportunity_id=opportunity_id,
        assigned_to="asha",
        subject="s",
        ocs_conversation=[{"role": "bot", "text": "Hi", "ts": "2026-03-01T09:00:00Z"}],
    )


@pytest.mark.django_db
def test_task_create_synthetic_refuses_a_real_opportunity(user, monkeypatch):
    """Invented coaching conversations never go into production Connect's records."""
    from connect_labs.mcp.tools import synthetic_tasks

    built = MagicMock()
    monkeypatch.setattr(synthetic_tasks, "_labs_api_for_user", built)
    with pytest.raises(MCPToolError) as exc:
        _call(user, 4242)
    assert exc.value.code == "INVALID_ARGUMENT"
    built.assert_not_called()


@pytest.mark.django_db
def test_task_create_synthetic_refuses_a_labs_only_opp_the_caller_cannot_see(user, monkeypatch):
    from connect_labs.mcp.tools import synthetic_tasks

    user.email = "bob@external.com"
    user.save()
    SyntheticOpportunity.objects.create(
        opportunity_id=10_4243, gdrive_folder_id="", labs_only=True, allowed_domains=["@partner.org"]
    )
    built = MagicMock()
    monkeypatch.setattr(synthetic_tasks, "_labs_api_for_user", built)
    with pytest.raises(MCPToolError) as exc:
        _call(user, 10_4243)
    assert exc.value.code == "PERMISSION_DENIED"
    built.assert_not_called()
