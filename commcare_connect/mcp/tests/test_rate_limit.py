import json

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.mcp.tool_registry import _REGISTRY, register
from commcare_connect.users.models import User


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def write_tool():
    @register(
        name="workflow_update_ratetest",
        description="rate-limit test tool",
        input_schema={"type": "object"},
    )
    def _handler(user):
        return {"ok": True}

    yield
    _REGISTRY.pop("workflow_update_ratetest", None)


def _call(client, raw):
    return client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "workflow_update_ratetest", "arguments": {}},
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )


@pytest.mark.django_db
@override_settings(MCP_WRITE_RATE_LIMIT="2/m")
def test_rate_limit_kicks_in_after_threshold(client, write_tool):
    user = User.objects.create(username="rate")
    _, raw = MCPAccessToken.create_token(user, name="t")

    resp1 = _call(client, raw)
    resp2 = _call(client, raw)
    assert resp1.json()["result"]["isError"] is False
    assert resp2.json()["result"]["isError"] is False

    resp3 = _call(client, raw)
    data = resp3.json()
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "RATE_LIMITED"


@pytest.mark.django_db
@override_settings(MCP_WRITE_RATE_LIMIT="1/m")
def test_rate_limit_is_per_user(client, write_tool):
    alice = User.objects.create(username="alice")
    bob = User.objects.create(username="bob")
    _, a_raw = MCPAccessToken.create_token(alice, name="a")
    _, b_raw = MCPAccessToken.create_token(bob, name="b")

    _call(client, a_raw)  # alice hits her cap
    # Bob should still be allowed
    resp = _call(client, b_raw)
    assert resp.json()["result"]["isError"] is False
