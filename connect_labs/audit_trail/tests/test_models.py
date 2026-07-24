import pytest
from django.db import connection, transaction
from django.db.utils import InternalError

from connect_labs.audit_trail.models import Action, AuditEvent

# NOTE: these tests deliberately stay on the default (transaction-wrapped)
# django_db mode — transaction=True tests flush the DB at teardown, which
# destroys migration-seeded rows other apps' tests depend on. The trigger
# error is contained in a savepoint instead.


@pytest.mark.django_db
def test_append_only_trigger_blocks_update():
    event = AuditEvent.objects.create(action=Action.READ, resource_type="thing")
    with pytest.raises(InternalError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE labs_audit_event SET resource_type = 'tampered' WHERE id = %s", [event.id])


@pytest.mark.django_db
def test_append_only_trigger_blocks_delete():
    event = AuditEvent.objects.create(action=Action.READ, resource_type="thing")
    with pytest.raises(InternalError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM labs_audit_event WHERE id = %s", [event.id])
    assert AuditEvent.objects.filter(id=event.id).exists()


@pytest.mark.django_db
def test_to_log_dict_round_trips_json():
    import json

    event = AuditEvent.objects.create(
        action=Action.EXPORT,
        resource_type="user_visits",
        record_count=10,
        opportunity_id=765,
        metadata={"endpoint": "/export/opportunity/765/user_visits/"},
    )
    payload = json.loads(json.dumps(event.to_log_dict(), default=str))
    assert payload["action"] == "export"
    assert payload["opportunity_id"] == 765
    assert payload["metadata"]["endpoint"].endswith("user_visits/")
