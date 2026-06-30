"""Tests for AIAgentsListAPIView config_fields surfacing."""
import time

import pytest
from django.test import Client


@pytest.fixture
def labs_client(db):
    from commcare_connect.users.models import User

    user, _ = User.objects.update_or_create(username="testuser", defaults={"email": "testuser@example.com"})
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "tok", "expires_at": time.time() + 3600}
    session.save()
    return client


def test_agents_list_includes_config_fields(labs_client):
    resp = labs_client.get("/audit/api/ai-agents/")
    assert resp.status_code == 200
    agents = {a["agent_id"]: a for a in resp.json()["agents"]}

    # Every agent exposes a config_fields list
    for agent in agents.values():
        assert isinstance(agent["config_fields"], list)

    # Scale agent declares the comparison_field form-field setting
    scale = agents["scale_validation"]
    keys = [f["key"] for f in scale["config_fields"]]
    assert "comparison_field" in keys
    cf = next(f for f in scale["config_fields"] if f["key"] == "comparison_field")
    assert cf["type"] == "form_field"
    assert cf["required"] is True
    assert cf["label"] == "Manual Scale Value"

    # MUAC agent declares no settings
    assert agents["muac_overzoom"]["config_fields"] == []
