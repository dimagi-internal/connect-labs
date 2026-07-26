import pytest
from django.db import ProgrammingError, transaction

from connect_labs.audit_trail import service
from connect_labs.audit_trail.context import AuditContext, audit_context, reset_audit_context, set_audit_context
from connect_labs.audit_trail.models import Action, AuditEvent, Outcome, Source


@pytest.mark.django_db
def test_record_immediate_write_without_context():
    service.record(Action.READ, resource_type="thing", resource_id=42, record_count=1)
    event = AuditEvent.objects.get()
    assert event.action == Action.READ
    assert event.resource_type == "thing"
    assert event.resource_id == "42"
    assert event.source == Source.SYSTEM
    assert event.outcome == Outcome.SUCCESS


@pytest.mark.django_db
def test_record_with_audit_context_attribution(user):
    with audit_context(user=user, source="celery", request_id="celery:abc"):
        service.record(Action.EXPORT, resource_type="user_visits", record_count=250, opportunity_id=765)
    event = AuditEvent.objects.get()
    assert event.user_id == user.pk
    assert event.username == user.username
    assert event.source == "celery"
    assert event.request_id == "celery:abc"
    assert event.record_count == 250
    assert event.opportunity_id == 765


@pytest.mark.django_db
def test_buffered_context_defers_until_flush(user):
    ctx = AuditContext(source=Source.WEB, buffer=[])
    token = set_audit_context(ctx)
    try:
        service.record(Action.LIST, resource_type="task", record_count=3)
        assert AuditEvent.objects.count() == 0
        assert len(ctx.buffer) == 1
    finally:
        reset_audit_context(token)
    service.flush_buffer(ctx, status_code=200)
    event = AuditEvent.objects.get()
    assert event.status_code == 200
    assert event.record_count == 3


@pytest.mark.django_db
def test_explicit_user_wins_over_context(user, django_user_model):
    other = django_user_model.objects.create(username="other-user", email="other@example.com")
    with audit_context(user=user, source="celery"):
        service.record(Action.LOGIN, resource_type="auth", user=other)
    event = AuditEvent.objects.get()
    assert event.user_id == other.pk
    assert event.username == "other-user"


@pytest.mark.django_db
def test_mcp_shaped_request_id_persists_intact(user):
    """MCP stamps "mcp:<tool_name>:<8 hex>" — the longest tool names blow past 36 chars."""
    request_id = "mcp:workflow_sync_from_template_file:34f6c02c"
    with audit_context(user=user, source="mcp", request_id=request_id):
        service.record(Action.UPDATE, resource_type="workflow_render_code", resource_id="3963")
    assert AuditEvent.objects.get().request_id == request_id


def test_clamp_covers_every_char_field():
    """Widths come off the model, so a newly added field is covered for free."""
    limits = service._char_limits()
    assert set(limits) >= {"username", "user_email", "user_agent", "request_id", "path", "query_string"}

    clamped = service._clamp({name: "x" * 2000 for name in limits} | {"record_count": 5, "metadata": {}})
    for name, limit in limits.items():
        assert len(clamped[name]) == limit, f"{name} not clamped to its column width"
    # Non-string values pass through untouched.
    assert clamped["record_count"] == 5
    assert clamped["metadata"] == {}


@pytest.mark.django_db
def test_overlong_context_values_still_produce_a_row(user):
    """An over-long value must cost us that value's tail, never the whole row."""
    overflow = "x" * 2000
    ctx = AuditContext(
        source=Source.MCP, user_agent=overflow, request_id=overflow, path=overflow, query_string=overflow
    )
    token = set_audit_context(ctx)
    try:
        service.record(Action.UPDATE, resource_type=overflow, resource_id=overflow, user=user)
    finally:
        reset_audit_context(token)

    event = AuditEvent.objects.get()
    limits = service._char_limits()
    assert len(event.request_id) == limits["request_id"]
    assert len(event.resource_type) == limits["resource_type"]
    assert len(event.query_string) == limits["query_string"]


@pytest.mark.django_db
def test_record_never_raises_and_preserves_open_transaction(user, monkeypatch):
    """A failing DB insert must neither raise nor poison the caller's transaction."""

    def boom(*args, **kwargs):
        raise ProgrammingError("simulated insert failure")

    with transaction.atomic():
        monkeypatch.setattr(AuditEvent.objects, "bulk_create", boom)
        service.record(Action.READ, resource_type="thing")
        monkeypatch.undo()
        # Transaction still usable: this query would raise TransactionManagementError
        # if the savepoint hadn't isolated the failure.
        assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_stream_line_emitted():
    """The stream logger is propagate=False, so attach a handler directly."""
    import json
    import logging

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture(level=logging.INFO)
    service.stream_logger.addHandler(handler)
    try:
        service.record(Action.CANARY, resource_type="canary")
    finally:
        service.stream_logger.removeHandler(handler)

    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["action"] == Action.CANARY
    assert payload["event_uuid"]
