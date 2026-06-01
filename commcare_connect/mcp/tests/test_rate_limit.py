import pytest
from django.core.cache import cache
from django.test import override_settings

from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.mcp.testing import call_tool
from commcare_connect.mcp.tool_registry import _REGISTRY, register
from commcare_connect.users.models import User


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _require_working_cache_backend():
    """Skip when the cache backend silently swallows reads/writes.

    The rate-limit enforcement leans entirely on Django's cache. In production
    the cache is redis with ``IGNORE_EXCEPTIONS=True`` (settings/base.py), so
    a dead backend silently turns the cap into a no-op — and these tests, which
    exercise the cap end-to-end, would fail in a confusing way (third write
    still goes through instead of being rejected). Round-trip a sentinel key
    to detect that case and skip with a clear message instead of failing.
    """
    cache.set("_rate_limit_test_probe", "ok", 5)
    if cache.get("_rate_limit_test_probe") != "ok":
        pytest.skip("Cache backend not reachable; rate-limit enforcement is a no-op")


@pytest.fixture
def write_tool():
    @register(
        name="workflow_update_ratetest",
        description="rate-limit test tool",
        input_schema={"type": "object"},
        is_write=True,
    )
    def _handler(user):
        return {"ok": True}

    yield
    _REGISTRY.pop("workflow_update_ratetest", None)


def _call(client, raw):
    # client unused — call_tool drives the FastMCP path in-process. Returns a
    # JSON-RPC-shaped dict directly (no .json() needed).
    return call_tool(raw, "workflow_update_ratetest", {})


@pytest.mark.django_db
@override_settings(MCP_WRITE_RATE_LIMIT="2/m")
def test_rate_limit_kicks_in_after_threshold(client, write_tool):
    user = User.objects.create(username="rate")
    _, raw = MCPAccessToken.create_token(user, name="t")

    resp1 = _call(client, raw)
    resp2 = _call(client, raw)
    assert resp1["result"]["isError"] is False
    assert resp2["result"]["isError"] is False

    data = _call(client, raw)
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
    assert resp["result"]["isError"] is False


@pytest.fixture
def read_only_tool():
    @register(
        name="workflow_get_readtest",
        description="read tool",
        input_schema={"type": "object"},
        is_write=False,
    )
    def _handler(user):
        return {"ok": True}

    yield
    _REGISTRY.pop("workflow_get_readtest", None)


@pytest.mark.django_db
@override_settings(MCP_WRITE_RATE_LIMIT="0/m")
def test_read_tool_bypasses_rate_limit(client, read_only_tool):
    """Reads are not rate-limited even if writes cap is 0."""
    user = User.objects.create(username="read-nolimit")
    _, raw = MCPAccessToken.create_token(user, name="t")
    resp = call_tool(raw, "workflow_get_readtest", {})
    assert resp["result"]["isError"] is False
