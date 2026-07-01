import pytest
from rest_framework.test import APIClient, APIRequestFactory

from connect_labs.users.models import User
from connect_labs.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def media_storage(settings, tmpdir):
    settings.MEDIA_ROOT = tmpdir.strpath


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
