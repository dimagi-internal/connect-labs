"""Override autouse fixtures from root conftest.

The proxy model tests are pure Python and don't need a database.
Override the autouse fixtures that trigger DB setup.
"""
import pytest


@pytest.fixture(autouse=True)
def media_storage():
    """Override root media_storage fixture — no DB needed for proxy model tests."""
    pass
