import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _fresh_targeting_cache():
    # The targeting views cache their answers (response_cache), and the test
    # cache is a per-process locmem: without this, one test's answer could
    # serve another test's request on edited data.
    cache.clear()
    yield
    cache.clear()
