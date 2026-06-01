import pytest

from commcare_connect.mcp.models import MCPAccessToken, MCPAuditLog
from commcare_connect.mcp.testing import call_tool
from commcare_connect.mcp.tool_registry import _REGISTRY, register
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_audit_log_stores_all_fields():
    user = User.objects.create(username="audit-test")
    log = MCPAuditLog.objects.create(
        user=user,
        tool_name="workflow_update_render_code",
        is_write=True,
        arguments={"workflow_id": 42, "expected_version": 3},
        success=True,
        version_before=3,
        version_after=4,
    )
    assert log.pk
    assert log.created_at
    assert log.arguments == {"workflow_id": 42, "expected_version": 3}


@pytest.mark.django_db
def test_audit_log_failure_stores_error_code():
    user = User.objects.create(username="audit-fail")
    log = MCPAuditLog.objects.create(
        user=user,
        tool_name="workflow_update_render_code",
        is_write=True,
        arguments={"workflow_id": 42},
        success=False,
        error_code="INVALID_JSX",
    )
    assert log.success is False
    assert log.error_code == "INVALID_JSX"


@pytest.mark.django_db
def test_tools_call_not_found_logs_failure(client):
    user = User.objects.create(username="audit-nf")
    _, raw = MCPAccessToken.create_token(user, name="t")
    call_tool(raw, "no_such_tool", {})
    log = MCPAuditLog.objects.get(user=user, tool_name="no_such_tool")
    assert log.success is False
    assert log.error_code == "NOT_FOUND"


@pytest.fixture
def sample_tool():
    """Register a write-flavored sample tool for the duration of one test."""

    @register(
        name="workflow_update_sample",
        description="Test tool",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        is_write=True,
    )
    def _handler(user, x: int = 0):
        return {"x_doubled": x * 2, "_version_before": 1, "_version_after": 2}

    yield
    _REGISTRY.pop("workflow_update_sample", None)


@pytest.mark.django_db
def test_tools_call_success_logs_version_transition(client, sample_tool):
    user = User.objects.create(username="audit-ok")
    _, raw = MCPAccessToken.create_token(user, name="t")
    resp = call_tool(raw, "workflow_update_sample", {"x": 3})
    assert resp["result"]["isError"] is False
    # Private keys must not leak to the client
    content = resp["result"]["structuredContent"]
    assert content == {"x_doubled": 6}

    log = MCPAuditLog.objects.get(user=user, tool_name="workflow_update_sample")
    assert log.success is True
    assert log.is_write is True
    assert log.version_before == 1
    assert log.version_after == 2
    assert log.arguments == {"x": 3}
