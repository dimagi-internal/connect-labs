from __future__ import annotations

import pytest

from connect_labs.messaging.canopy_bridge.tests.fake_canopy import BASE, SITE, FakeCanopy, new_key_pem
from connect_labs.messaging.tests.conftest import (  # noqa: F401 -- shared fixtures
    fake_connectid,
    handle_now,
    messaging_on,
    run_now,
    worker,
)

SITE_KEY = new_key_pem()


@pytest.fixture
def site_configured(settings, monkeypatch):
    """Labs' canopy site is configured (its one key, its name, canopy) and labs has learnt the
    canopy workspace it is registered in."""
    from connect_labs.messaging.canopy_bridge import conf

    settings.CANOPY_SIGNING_KEY = SITE_KEY
    settings.CANOPY_APP_NAME = SITE
    settings.CANOPY_BASE_URL = BASE
    monkeypatch.setattr(conf, "workspace", lambda: "connect")
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
    """Messaging on, labs' canopy site configured, a fake canopy behind it."""
    return fake_canopy
