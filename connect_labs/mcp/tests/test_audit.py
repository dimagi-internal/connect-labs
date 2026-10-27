import pytest

from connect_labs.mcp.models import MCPAuditLog
from connect_labs.mcp.testing import call_tool, mint_token
from connect_labs.mcp.tool_registry import _REGISTRY, register
from connect_labs.users.models import User


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
    _, raw = mint_token(user, name="t")
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
    _, raw = mint_token(user, name="t")
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


@pytest.fixture
def scoped_read_tool():
    """A read tool that touches a LabsRecord-style audit event, like real reads do."""
    from connect_labs.audit_trail import service
    from connect_labs.audit_trail.models import Action

    @register(
        name="workflow_scoped_read_sample",
        description="Test tool",
        input_schema={"type": "object", "properties": {"opportunity_id": {}, "program_id": {}}},
    )
    def _handler(user, opportunity_id=None, program_id=None):
        service.record(Action.LIST, resource_type="workflow_definition", opportunity_id=812)
        return {"ok": True}

    yield
    _REGISTRY.pop("workflow_scoped_read_sample", None)


@pytest.mark.django_db
def test_every_call_logs_its_scope_duration_and_the_id_its_data_touches_carry(client, scoped_read_tool):
    """A read's arguments are not kept, but its scope ids are: that is what lets
    agent use break down by organisation / programme / opportunity. The request
    id joins the call to the audit events it caused."""
    from connect_labs.audit_trail.models import AuditEvent

    user = User.objects.create(username="audit-scope")
    _, raw = mint_token(user, name="t")
    call_tool(raw, "workflow_scoped_read_sample", {"opportunity_id": "812", "program_id": 25})

    log = MCPAuditLog.objects.get(user=user, tool_name="workflow_scoped_read_sample")
    assert log.arguments == {}  # reads still keep no arguments
    assert (log.opportunity_ids, log.program_id, log.organization_slug) == ([812], 25, "")
    assert log.duration_ms is not None and log.duration_ms >= 0
    touched = AuditEvent.objects.get(resource_type="workflow_definition")
    assert log.request_id and log.request_id == touched.request_id


def test_scope_ids_keep_only_plain_ids():
    from connect_labs.mcp.server import _scope_ids

    assert _scope_ids(
        {
            "opportunity_id": 5,
            "opportunity_ids": [5, "7", "x", True, 10**12],
            "program_id": "25",
            "organization_id": "acme",
        }
    ) == {"opportunity_ids": [5, 7], "program_id": 25, "organization_slug": "acme"}
    assert _scope_ids({"opportunity_id": {"nested": 1}, "program_id": "drop table"}) == {
        "opportunity_ids": [],
        "program_id": None,
        "organization_slug": "",
    }
