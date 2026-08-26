import pytest
from django.core.cache import cache
from rest_framework.test import APIClient, APIRequestFactory

from connect_labs.users.models import User
from connect_labs.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def media_storage(settings, tmpdir):
    settings.MEDIA_ROOT = tmpdir.strpath


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
