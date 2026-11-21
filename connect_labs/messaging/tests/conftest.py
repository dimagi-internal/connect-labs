from __future__ import annotations

import httpx
import pytest

from connect_labs.messaging.tests.fake_connectid import CLIENT_ID, SECRET, FakeConnectID

PROGRAM = 501


@pytest.fixture
def fake_connectid(monkeypatch, settings):
    """A fake connect-id behind labs' PersonalID client (and the userinfo check)."""
    fake = FakeConnectID()
    monkeypatch.setattr("connect_labs.messaging.transport.personalid.HTTP_TRANSPORT", httpx.MockTransport(fake.handle))
    settings.PERSONALID_SERVER_URL = "https://connectid.test"
    settings.PERSONALID_MESSAGING_CLIENT_ID = CLIENT_ID
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = SECRET
    return fake


@pytest.fixture
def messaging_on(settings, db):
    """Messaging on, globally and for PROGRAM."""
    from connect_labs.messaging import config
    from connect_labs.scope_config.models import ScopeConfig

    settings.MESSAGING_ENABLED = True
    ScopeConfig.objects.update_or_create(
        scope_type="program",
        scope_key=str(PROGRAM),
        namespace=config.SETTINGS,
        defaults={"data": {"enabled": True, "channel_name": "Bednet Program"}},
    )
    return PROGRAM


@pytest.fixture
def run_now(monkeypatch):
    """Run queued work at once, the way a Celery worker would after the commit."""
    from connect_labs.messaging import tasks

    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    monkeypatch.setattr(
        tasks.deliver_message, "delay", lambda message_id: tasks.deliver_message.apply(args=[message_id])
    )
    monkeypatch.setattr(
        tasks.deliver_message,
        "apply_async",
        lambda args, eta=None, countdown=None: None,  # held back (quiet hours): not sent now
    )
    monkeypatch.setattr(
        tasks.process_transport_event, "delay", lambda event_id: tasks.process_transport_event(event_id)
    )
    return tasks


@pytest.fixture
def worker(db):
    from connect_labs.workers.models import ConnectWorker

    return ConnectWorker.objects.create(username="amina123", name="Amina Bello", timezone="Africa/Lagos")
