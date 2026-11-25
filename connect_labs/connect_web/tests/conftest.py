import pytest
from cryptography.fernet import Fernet
from django.core.cache import cache

from connect_labs.connect_web import http
from connect_labs.connect_web.models import PoolWorker
from connect_labs.users.models import User

from . import fakes


@pytest.fixture(autouse=True)
def _connect_web_settings(settings):
    settings.PERSONALID_SERVER_URL = fakes.PERSONALID
    settings.CONNECT_PRODUCTION_URL = fakes.CONNECT
    settings.CONNECT_WEB_PERSONALID_CLIENT_ID = "pid-client"
    settings.CONNECT_WEB_HQ_CLIENT_ID = "hq-client"
    settings.CONNECT_WEB_DEFAULT_HQ_URL = fakes.HQ
    settings.CONNECT_WEB_HQ_HOSTS = ["www.commcarehq.org"]
    settings.CONNECT_WEB_ALLOWED_EMAILS = []
    settings.CONNECT_WEB_IDLE_TAKEOVER_MINUTES = 60
    settings.LABS_FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    settings.CONNECT_WEB_FORMPLAYER_URL = fakes.FORMPLAYER
    settings.CONNECT_WEB_FORMPLAYER_KEY = fakes.fake_formplayer.KEY
    settings.CONNECT_WEB_MEDIA_MAX_BYTES = 4 * 1048576 - 1024
    # Build ids and app targets are cached; no test may see another's.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def servers(monkeypatch):
    """Every Connect Web HTTP call goes to the fakes; a test can never reach a real server."""
    fake = fakes.FakeServers()
    monkeypatch.setattr(http, "HTTP_TRANSPORT", fake.transport())
    monkeypatch.setattr(http, "_BACKOFF_SECONDS", 0)
    return fake


@pytest.fixture
def pool_worker(db):
    return PoolWorker.objects.create(phone_number="+74260000001", display_name="Test Worker 1", backup_code="123456")


@pytest.fixture
def staff(db):
    return User.objects.create(username="staffer", email="staffer@dimagi.com")


@pytest.fixture
def other_staff(db):
    return User.objects.create(username="colleague", email="colleague@dimagi.com", name="Col League")


@pytest.fixture
def outsider(db):
    return User.objects.create(username="outsider", email="outsider@example.com")
