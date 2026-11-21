import pytest
from django.db import IntegrityError, connection

from connect_labs.workers.models import (
    ConnectWorker,
    StaffNote,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
)

pytestmark = pytest.mark.django_db


def test_canopy_identity_is_the_personalid_username():
    worker = ConnectWorker.objects.create(username="amina.k")
    assert worker.canopy_sub == "personalid:amina.k"
    assert worker.record_uri == "labs-worker://amina.k"
    assert ConnectWorker.username_from_sub("personalid:amina.k") == "amina.k"
    assert ConnectWorker.username_from_sub("labs:amina.k") is None
    assert ConnectWorker.username_from_sub("personalid:") is None


def test_one_membership_per_opportunity():
    worker = ConnectWorker.objects.create(username="w1")
    WorkerMembership.objects.create(worker=worker, opportunity_id=7)
    with pytest.raises(IntegrityError):
        WorkerMembership.objects.create(worker=worker, opportunity_id=7)


def test_events_are_idempotent_by_dedupe_key():
    worker = ConnectWorker.objects.create(username="w2")
    WorkerEvent.objects.create(worker=worker, kind="invited", source="connect", dedupe_key="connect:7:w2:invited")
    with pytest.raises(IntegrityError):
        WorkerEvent.objects.create(worker=worker, kind="invited", source="connect", dedupe_key="connect:7:w2:invited")


def test_personal_fields_are_encrypted_at_rest(settings):
    worker = ConnectWorker.objects.create(username="w3", phone="+254700000001")
    note = StaffNote.objects.create(worker=worker, body="Asked for help with the scale", author="sup@example.org")
    with connection.cursor() as cur:
        cur.execute("SELECT phone FROM workers_connectworker WHERE id = %s", [worker.id])
        stored_phone = cur.fetchone()[0]
        cur.execute("SELECT body FROM workers_staffnote WHERE id = %s", [note.id])
        stored_body = cur.fetchone()[0]
    if getattr(settings, "LABS_FIELD_ENCRYPTION_KEYS", None):
        assert "+254700000001" not in stored_phone
        assert "scale" not in stored_body
    worker.refresh_from_db()
    note.refresh_from_db()
    assert worker.phone == "+254700000001"
    assert note.body == "Asked for help with the scale"


def test_facts_supersede_rather_than_edit():
    worker = ConnectWorker.objects.create(username="w4")
    first = WorkerFact.objects.create(
        worker=worker, kind="language", statement="Prefers Swahili", asserted_by_kind="worker"
    )
    second = WorkerFact.objects.create(
        worker=worker,
        kind="language",
        statement="Prefers English for app terms",
        asserted_by_kind="worker",
        supersedes=first,
    )
    first.status = "superseded"
    first.save()
    assert list(worker.facts.filter(status="active")) == [second]
