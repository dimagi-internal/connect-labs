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
