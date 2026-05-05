"""Tests for the list_templates MCP tool."""

import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.fixture
def auth_user(db):
    user = User.objects.create(username="tpltest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    resp = client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_pat}",
    )
    return resp.json()


@pytest.mark.django_db
def test_list_templates_returns_registered_templates(client, auth_user):
    """Covers the known built-in templates. Spot-check fields that callers
    rely on when choosing a template_key."""
    _, raw = auth_user
    data = _call_tool(client, raw, "list_templates", {})
    assert data["result"]["isError"] is False, data
    templates = data["result"]["structuredContent"]["templates"]
    assert isinstance(templates, list) and len(templates) > 0

    by_key = {t["key"]: t for t in templates}
    # performance_review is the canonical multi_opp + saved-runs template —
    # if either flag flips we want to hear about it in test.
    assert "performance_review" in by_key
    pr = by_key["performance_review"]
    assert pr["multi_opp"] is True
    assert pr["supports_saved_runs"] is True
    assert pr["name"]
    assert pr["description"]

    # An action-shaped template should explicitly report supports_saved_runs=False.
    assert by_key["bulk_image_audit"]["supports_saved_runs"] is False
