from __future__ import annotations

import pytest

from connect_labs.messaging.canopy_bridge.tests.fake_canopy import BASE, FakeCanopy, new_key_pem
from connect_labs.messaging.tests.conftest import (  # noqa: F401 -- shared fixtures
    fake_connectid,
    handle_now,
    messaging_on,
    run_now,
    worker,
)

SITE_KEY = new_key_pem()


@pytest.fixture
def site_configured(settings):
    """The connect-messaging site's identity is set (its key, name, workspace, canopy)."""
    settings.CANOPY_MESSAGING_SIGNING_KEY = SITE_KEY
    settings.CANOPY_MESSAGING_SITE_NAME = "connect-messaging"
    settings.CANOPY_MESSAGING_WORKSPACE = "dimagi"
    settings.CANOPY_BASE_URL = BASE
    return settings


@pytest.fixture
def fake_canopy(monkeypatch, site_configured):
    """A fake canopy behind every call the bridge makes: no network."""
    from connect_labs.messaging.canopy_bridge import sdk

    fake = FakeCanopy(SITE_KEY)
    monkeypatch.setattr(sdk, "_http_post", fake.http_post)
    return fake


@pytest.fixture
def bridge_on(fake_canopy, messaging_on):  # noqa: F811 -- the shared fixture
    """Messaging on, the connect-messaging site configured, a fake canopy behind it."""
    return fake_canopy
