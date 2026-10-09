import datetime

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient, APIRequestFactory

from connect_labs.testing.db_template import django_db_setup_with_template
from connect_labs.users.models import User
from connect_labs.users.tests.factories import UserFactory

# Override pytest-django's own django_db_setup: under xdist, migrate once into a
# template database and clone it per worker instead of migrating N times.
# See connect_labs/testing/db_template.py for why.
django_db_setup = pytest.fixture(scope="session")(django_db_setup_with_template)


@pytest.fixture(autouse=True)
def media_storage(settings, tmpdir):
    settings.MEDIA_ROOT = tmpdir.strpath


_COLLECTED_ON = datetime.date.today()


@pytest.fixture(autouse=True)
def _today_is_today(request):
    """Keep a test module's import-time `TODAY` in step with the code's own today.

    Many modules write `TODAY = date.today()` (or `timezone.localdate()`) at import,
    which is collection time, while the code under test asks for today when the test
    runs. A run that crosses midnight then builds fixtures for one day and asserts on
    another: 77 sachets over 4 days became "15 a day" against an expected 19, and
    connect-labs#2340 and #2341 both went red on it at 00:02 UTC. Shifting by the
    days elapsed since collection keeps each module's own clock (localdate vs today).
    """
    module = request.module
    collected = getattr(module, "TODAY", None)
    elapsed = datetime.date.today() - _COLLECTED_ON
    if not elapsed or type(collected) is not datetime.date:
        yield
        return
    module.TODAY = collected + elapsed
    yield
    module.TODAY = collected


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Give every test an empty cache.

    Caches that outlive a single request are the point of a cache, and under
    locmem they also outlive a single TEST — so state leaks in test-declaration
    order and produces failures that vanish when the test is run alone. That is
    exactly what happened when the synthetic FixtureStore gained a shared tier:
    21 export-API tests began failing together, because several of them serve
    fixtures for the same opp id and were suddenly seeing each other's data.
    Clear between tests so a cached value can never be an invisible input.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def api_rf() -> APIRequestFactory:
    """APIRequestFactory instance"""
    return APIRequestFactory()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db) -> User:
    return UserFactory()
