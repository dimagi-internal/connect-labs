"""Test isolation for the marketplace's process caches.

`queries` caches delivery and workspace resolution for a minute, because one
page render asks for them four times over and each one aggregates the whole
pulse spine. That is right in production and wrong in a test suite: a test that
seeds a PulseEvent would read the previous test's answer, and a cache that
makes tests pass or fail depending on their order is worse than a slow page.

So it is dropped before every test in this app. Production keeps the cache; the
importer already invalidates it explicitly, as it does the partner-name one.
"""

import pytest


@pytest.fixture(autouse=True)
def _drop_marketplace_caches():
    from connect_labs.marketplace import queries
    from connect_labs.pulse.partner_names import invalidate as invalidate_partner_names

    queries.invalidate()
    invalidate_partner_names()
    yield
    queries.invalidate()
    invalidate_partner_names()
