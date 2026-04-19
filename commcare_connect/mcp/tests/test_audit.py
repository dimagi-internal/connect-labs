import pytest

from commcare_connect.mcp.models import MCPAuditLog
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
