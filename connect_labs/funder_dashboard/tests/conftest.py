"""Override autouse fixtures from root conftest.

Proxy model tests are pure Python and don't need a database.
"""
import pytest


@pytest.fixture(autouse=True)
def media_storage():
    pass
