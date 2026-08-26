"""``fetch_user_organization_data`` caches per token, so one expensive upstream call serves many callers.

``/export/opp_org_program_list/`` is not a cheap lookup on production Connect: it
annotates every opportunity the caller can see with ``Count("uservisit")``. Measured
from the labs web logs over 14 days, that costs 0.28-0.37s for an account with almost
no org membership and 4.7-15.0s for broad Dimagi staff access -- it scales with how
much the caller can see, which is why it reads as "random" slowness.

Labs was asking for it 182-455 times a day against 1-7 logins (connect-labs#1298).
The helper had no cache of its own; two of its seven call sites had each bolted one
on locally after independently hitting the same wall (``audit/link_helpers.py`` in
#1175, ``mcp/tools/synthetic.py`` in #1194/#1212). These tests pin the cache to the
helper instead, where every caller gets it.

What must NOT be cached is a failure. ``mcp/tools/synthetic.py`` distinguishes "we
could not ask" from "you may not" precisely because collapsing the two turned a
network blip into a confident PERMISSION_DENIED (#1195); caching the ``None`` would
make that blip stick for the whole TTL.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from connect_labs.labs.integrations.connect import oauth
from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

_ORG_DATA = {
    "organizations": [{"id": "dimagi", "slug": "dimagi"}],
    "programs": [],
    "opportunities": [{"id": 2154, "visit_count": 23308}],
}

# LocMem rather than the configured Redis: the real backend is built with
# IGNORE_EXCEPTIONS=True, so with Redis down every set() silently no-ops and these
# assertions would fail for a reason that has nothing to do with the code.
locmem = override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})


@pytest.fixture(autouse=True)
def _isolated_cache():
    with locmem:
        cache.clear()
        yield
        cache.clear()


def _ok_response(payload=None):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = _ORG_DATA if payload is None else payload
    return response


class TestOrgDataCaching:
    def test_second_call_with_same_token_does_not_refetch(self):
        with patch.object(oauth.httpx, "get", return_value=_ok_response()) as get:
            first = fetch_user_organization_data("token-abc")
            second = fetch_user_organization_data("token-abc")

        assert first == _ORG_DATA
        assert second == _ORG_DATA
        assert get.call_count == 1, "the second call should have been served from cache"

    def test_different_tokens_do_not_share_an_entry(self):
        """Two users are two org trees. Sharing one entry would leak another user's access."""
        other = {"organizations": [], "programs": [], "opportunities": []}
        with patch.object(oauth.httpx, "get", side_effect=[_ok_response(), _ok_response(other)]) as get:
            first = fetch_user_organization_data("token-abc")
            second = fetch_user_organization_data("token-xyz")

        assert first == _ORG_DATA
        assert second == other
        assert get.call_count == 2

    def test_failure_is_not_cached(self):
        """A blip must not stick. Caching None would deny access for the whole TTL (#1195)."""
        with patch.object(oauth.httpx, "get", side_effect=[Exception("connection reset"), _ok_response()]) as get:
            failed = fetch_user_organization_data("token-abc")
            retried = fetch_user_organization_data("token-abc")

        assert failed is None
        assert retried == _ORG_DATA, "the next call must retry, not replay the failure"
        assert get.call_count == 2

    def test_force_refresh_bypasses_the_cache_and_repopulates_it(self):
        """The 'refresh org data' view exists to defeat staleness; a cache must not defeat it."""
        fresh = {"organizations": [], "programs": [], "opportunities": [{"id": 9999}]}
        with patch.object(oauth.httpx, "get", side_effect=[_ok_response(), _ok_response(fresh)]) as get:
            fetch_user_organization_data("token-abc")
            refreshed = fetch_user_organization_data("token-abc", force_refresh=True)
            after = fetch_user_organization_data("token-abc")

        assert refreshed == fresh
        assert after == fresh, "force_refresh should leave the NEW payload cached, not the old one"
        assert get.call_count == 2

    def test_cache_key_does_not_contain_the_raw_token(self):
        """The key lands in Redis, which is not a credential store."""
        token = "super-secret-access-token"
        key = oauth._org_data_cache_key(token)
        assert token not in key
