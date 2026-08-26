"""``fetch_user_organization_data`` caches per token OWNER, so one expensive upstream call serves many callers.

``/export/opp_org_program_list/`` is not a cheap lookup on production Connect: it
annotates every opportunity the caller can see with ``Count("uservisit")``. Measured
from the labs web logs over 14 days, that costs 0.28-0.37s for an account with almost
no org membership and 4.7-15.0s for broad Dimagi staff access -- it scales with how
much the caller can see, which is why it reads as "random" slowness. Labs was asking
182-455 times a day against 1-7 logins (connect-labs#1298).

Keyed on the OWNER, not the credential. #1310 keyed on a hash of the access token,
which is safe by construction but gives up the case the issue was actually about:
every sign-in mints a new token, so login could never hit, and a silent
``refresh_connect_token`` rotation orphaned a perfectly good entry. Worse, the ~400
calls a day that dominate the volume warmed nothing for logins even though they carry
a DIFFERENT token for the SAME user.

The owner must be derived from the credential -- the username OAuth introspection
returned for this token, or the user whose ``UserConnectToken`` this is -- never from
an ambient ``request.user``. That is what keeps the key's scope identical to the
payload's authorization scope. Call sites that cannot prove that alignment (the CLI,
management commands, Celery) pass no owner and fall back to keying on the token,
where the worst case is a miss rather than one user seeing another's org tree.
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


class TestOwnerKeying:
    def test_a_new_login_token_hits_an_entry_warmed_by_the_same_owner(self):
        """The point of owner-keying, and the case #1310 could not serve.

        An MCP tool call at 09:10 carries a different token than the browser login at
        09:12. Same person, same org tree, and now one upstream call.
        """
        with patch.object(oauth.httpx, "get", return_value=_ok_response()) as get:
            fetch_user_organization_data("mcp-token", owner="nazier")
            at_login = fetch_user_organization_data("fresh-login-token", owner="nazier")

        assert at_login == _ORG_DATA
        assert get.call_count == 1, "the login should have been served from the warm entry"

    def test_a_rotated_token_still_hits(self):
        """refresh_connect_token rotates access_token; the org tree did not change."""
        with patch.object(oauth.httpx, "get", return_value=_ok_response()) as get:
            fetch_user_organization_data("token-v1", owner="nazier")
            fetch_user_organization_data("token-v2-after-refresh", owner="nazier")

        assert get.call_count == 1

    def test_different_owners_do_not_share_an_entry(self):
        """Two users are two org trees. Sharing one would leak another user's access."""
        other = {"organizations": [], "programs": [], "opportunities": []}
        with patch.object(oauth.httpx, "get", side_effect=[_ok_response(), _ok_response(other)]) as get:
            first = fetch_user_organization_data("token-a", owner="nazier")
            second = fetch_user_organization_data("token-b", owner="jonathan")

        assert first == _ORG_DATA
        assert second == other
        assert get.call_count == 2

    def test_without_an_owner_it_falls_back_to_the_token(self):
        """CLI, management commands and Celery cannot prove owner alignment.

        They keep the #1310 behaviour: keyed on the credential, so the worst case is a
        miss rather than one user being served another's org tree.
        """
        with patch.object(oauth.httpx, "get", return_value=_ok_response()) as get:
            fetch_user_organization_data("cli-token")
            fetch_user_organization_data("cli-token")
            assert get.call_count == 1

            fetch_user_organization_data("a-different-cli-token")
            assert get.call_count == 2

    def test_an_owner_entry_is_not_reachable_by_token_alone(self):
        """The two key spaces must not collide."""
        with patch.object(oauth.httpx, "get", return_value=_ok_response()) as get:
            fetch_user_organization_data("token-a", owner="nazier")
            fetch_user_organization_data("token-a")

        assert get.call_count == 2


class TestFreshnessGuarantees:
    def test_failure_is_not_cached(self):
        """A blip must not stick. Caching None would deny access for the whole TTL (#1195)."""
        with patch.object(oauth.httpx, "get", side_effect=[Exception("connection reset"), _ok_response()]) as get:
            failed = fetch_user_organization_data("token-abc", owner="nazier")
            retried = fetch_user_organization_data("token-abc", owner="nazier")

        assert failed is None
        assert retried == _ORG_DATA, "the next call must retry, not replay the failure"
        assert get.call_count == 2

    def test_force_refresh_repopulates_the_entry_the_next_login_will_read(self):
        """The refresh button is the escape hatch that makes a 60-minute TTL safe.

        It has to bust the OWNER entry, not just return fresh data to its own caller —
        otherwise the next login keeps reading the stale copy and the button is a lie.
        """
        fresh = {"organizations": [], "programs": [], "opportunities": [{"id": 9999}]}
        with patch.object(oauth.httpx, "get", side_effect=[_ok_response(), _ok_response(fresh)]) as get:
            fetch_user_organization_data("token-abc", owner="nazier")
            refreshed = fetch_user_organization_data("token-abc", owner="nazier", force_refresh=True)
            next_login = fetch_user_organization_data("a-brand-new-token", owner="nazier")

        assert refreshed == fresh
        assert next_login == fresh, "force_refresh must leave the NEW payload where login will find it"
        assert get.call_count == 2

    def test_ttl_is_an_hour(self):
        """Deliberate, not incidental — see the constant's comment for why it is safe."""
        assert oauth._ORG_DATA_CACHE_TTL == 3600


class TestCacheKeyHygiene:
    def test_the_key_never_contains_the_raw_token(self):
        """The key lands in Redis, which is not a credential store."""
        token = "super-secret-access-token"
        assert token not in oauth._org_data_cache_key(token)
        assert token not in oauth._org_data_cache_key(token, owner="nazier")

    def test_the_key_never_contains_the_raw_username(self):
        """Usernames here are often @dimagi.com addresses."""
        assert "nheynes@dimagi.com" not in oauth._org_data_cache_key("t", owner="nheynes@dimagi.com")
