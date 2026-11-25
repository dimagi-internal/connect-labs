import io

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from connect_labs.connect_web.admin import PoolWorkerForm
from connect_labs.connect_web.models import PoolWorker

pytestmark = pytest.mark.django_db


def test_add_worker_reads_secrets_from_env(monkeypatch):
    monkeypatch.setenv("CONNECT_WEB_BACKUP_CODE", "246810")
    monkeypatch.setenv("CONNECT_WEB_PIN", "1357")
    out = io.StringIO()
    call_command("connect_web_add_worker", "--phone", "+74260000009", "--name", "Nine", stdout=out)
    worker = PoolWorker.objects.get(phone_number="+74260000009")
    assert worker.backup_code == "246810" and worker.pin == "1357"
    assert "246810" not in out.getvalue()


def test_add_worker_reads_secrets_from_stdin(monkeypatch):
    monkeypatch.delenv("CONNECT_WEB_BACKUP_CODE", raising=False)
    monkeypatch.delenv("CONNECT_WEB_PIN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("111222\n3344\n"))
    call_command("connect_web_add_worker", "--phone", "+74260000010", "--name", "Ten", "--stdin", stdout=io.StringIO())
    worker = PoolWorker.objects.get(phone_number="+74260000010")
    assert worker.backup_code == "111222" and worker.pin == "3344"


def test_add_worker_updates_and_unblocks(monkeypatch, pool_worker):
    pool_worker.sign_in_blocked_reason = "PersonalID rejected the backup code"
    pool_worker.save()
    monkeypatch.setenv("CONNECT_WEB_BACKUP_CODE", "999000")
    call_command(
        "connect_web_add_worker", "--phone", pool_worker.phone_number, "--name", "Renamed", stdout=io.StringIO()
    )
    pool_worker.refresh_from_db()
    assert pool_worker.display_name == "Renamed" and pool_worker.backup_code == "999000"
    assert pool_worker.sign_in_blocked_reason == ""


def test_add_worker_needs_a_code(monkeypatch):
    monkeypatch.delenv("CONNECT_WEB_BACKUP_CODE", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(CommandError):
        call_command("connect_web_add_worker", "--phone", "+74260000011", "--name", "X", "--stdin")


def test_admin_form_is_write_only_for_secrets(pool_worker):
    form = PoolWorkerForm(instance=pool_worker)
    html = form.as_p()
    assert "123456" not in html
    form = PoolWorkerForm(
        instance=pool_worker,
        data={"display_name": "W", "phone_number": pool_worker.phone_number, "active": "on", "new_backup_code": ""},
    )
    assert form.is_valid(), form.errors
    form.save()
    pool_worker.refresh_from_db()
    assert pool_worker.backup_code == "123456"  # blank keeps the saved code


def test_admin_form_new_worker_needs_a_code():
    form = PoolWorkerForm(data={"display_name": "W", "phone_number": "+74260000012", "active": "on"})
    assert not form.is_valid() and "new_backup_code" in form.errors


def test_admin_change_page_renders_without_secrets(client, pool_worker):
    from connect_labs.users.models import User

    admin = User.objects.create(username="admin", email="admin@dimagi.com", is_staff=True, is_superuser=True)
    client.force_login(admin)
    response = client.get(reverse("admin:connect_web_poolworker_change", args=[pool_worker.pk]))
    assert response.status_code == 200
    assert "123456" not in response.content.decode()


POOL = [
    {"phone": "+74260000201", "name": "Pool A", "backup_code": "123456", "pin": "1111"},
    {"phone": "+74260000202", "name": "Pool B", "backup_code": "654321"},
]


def test_import_pool_upserts_without_printing_secrets(monkeypatch):
    import json

    monkeypatch.setenv("CONNECT_WEB_POOL_JSON", json.dumps(POOL))
    out = io.StringIO()
    call_command("connect_web_import_pool", stdout=out)
    assert "2 registered, 0 updated" in out.getvalue()
    for secret in ("123456", "654321", "1111"):
        assert secret not in out.getvalue()
    a = PoolWorker.objects.get(phone_number="+74260000201")
    assert a.backup_code == "123456" and a.pin == "1111" and a.active

    a.sign_in_blocked_reason = "PersonalID rejected the backup code"
    a.save()
    rotated = [dict(POOL[0], backup_code="777888"), POOL[1]]
    monkeypatch.setenv("CONNECT_WEB_POOL_JSON", json.dumps(rotated))
    out = io.StringIO()
    call_command("connect_web_import_pool", stdout=out)
    assert "0 registered, 2 updated" in out.getvalue()
    a.refresh_from_db()
    assert a.backup_code == "777888" and a.sign_in_blocked_reason == ""


def test_import_pool_keeps_a_block_when_the_code_did_not_change(monkeypatch):
    import json

    monkeypatch.setenv("CONNECT_WEB_POOL_JSON", json.dumps(POOL))
    call_command("connect_web_import_pool", stdout=io.StringIO())
    PoolWorker.objects.filter(phone_number="+74260000201").update(sign_in_blocked_reason="locked")
    call_command("connect_web_import_pool", stdout=io.StringIO())
    assert PoolWorker.objects.get(phone_number="+74260000201").sign_in_blocked_reason == "locked"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '{"phone": "+1"}',
        '[{"phone": "+74260000201", "name": "A"}]',
        '[{"phone": "742", "name": "A", "backup_code": "SECRETX"}]',
    ],
)
def test_import_pool_refuses_bad_input_without_echoing_it(monkeypatch, raw):
    monkeypatch.setenv("CONNECT_WEB_POOL_JSON", raw)
    with pytest.raises(CommandError) as exc:
        call_command("connect_web_import_pool", stdout=io.StringIO())
    assert "SECRETX" not in str(exc.value)
    assert not PoolWorker.objects.exists()
