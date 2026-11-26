from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from connect_labs.connect_web.models import PoolWorker, WorkerSession
from connect_labs.workers.models import ConnectWorker

pytestmark = pytest.mark.django_db


def _raw(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def test_pool_secrets_are_encrypted_at_rest(pool_worker):
    pool_worker.pin = "4321"
    pool_worker.save()
    code, pin = _raw("SELECT backup_code, pin FROM connect_web_poolworker WHERE id = %s", [pool_worker.pk])
    assert "123456" not in code and code.startswith("fernet1:")
    assert "4321" not in pin
    fresh = PoolWorker.objects.get(pk=pool_worker.pk)
    assert fresh.backup_code == "123456" and fresh.pin == "4321"


def test_sign_in_secrets_are_encrypted_at_rest(pool_worker):
    pool_worker.personalid_password = "pw-secret"
    pool_worker.access_token = "access-secret"
    pool_worker.set_hq_token("www.commcarehq.org/nets", "hq-secret", timezone.now() + timedelta(minutes=10))
    pool_worker.save()
    row = _raw(
        "SELECT personalid_password, access_token, hq_tokens FROM connect_web_poolworker WHERE id = %s",
        [pool_worker.pk],
    )
    for raw, secret in zip(row, ("pw-secret", "access-secret", "hq-secret")):
        assert secret not in raw and raw.startswith("fernet1:")
    fresh = PoolWorker.objects.get(pk=pool_worker.pk)
    assert fresh.access_token == "access-secret"
    assert fresh.get_hq_tokens()["www.commcarehq.org/nets"]["token"] == "hq-secret"


def test_reprs_and_strs_carry_no_secrets(pool_worker, staff):
    pool_worker.pin = "4321"
    pool_worker.personalid_password = "pw-secret"
    pool_worker.access_token = "access-secret"
    pool_worker.set_hq_token("h/d", "hq-secret", timezone.now())
    session = WorkerSession(user=staff, pool_worker=pool_worker)
    for text in (repr(pool_worker), str(pool_worker), repr(session), str(session)):
        for secret in ("123456", "4321", "pw-secret", "access-secret", "hq-secret"):
            assert secret not in text


def test_one_open_session_per_worker_and_per_user(pool_worker, staff, other_staff):
    WorkerSession.objects.create(user=staff, pool_worker=pool_worker)
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkerSession.objects.create(user=other_staff, pool_worker=pool_worker)
    second = PoolWorker.objects.create(phone_number="+74260000002", display_name="Two", backup_code="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkerSession.objects.create(user=staff, pool_worker=second)
    # An ended session doesn't count.
    WorkerSession.objects.filter(user=staff).update(ended_at=timezone.now())
    WorkerSession.objects.create(user=other_staff, pool_worker=pool_worker)


def test_device_id_is_stable_and_unique(pool_worker):
    device = pool_worker.device_id
    pool_worker.display_name = "Renamed"
    pool_worker.save()
    assert PoolWorker.objects.get(pk=pool_worker.pk).device_id == device
    other = PoolWorker.objects.create(phone_number="+74260000002", display_name="Two", backup_code="x")
    assert other.device_id != device


def test_connect_worker_is_found_by_personalid_username(pool_worker):
    assert pool_worker.connect_worker is None
    record = ConnectWorker.objects.create(username="pooluserone")
    pool_worker.personalid_username = "PoolUserOne"
    assert pool_worker.connect_worker == record
