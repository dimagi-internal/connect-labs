"""Tests for workflow_auth_status_api.

Covers the silent-refresh and real-CCHQ-ping behavior introduced to fix
the "click Authorize → returns to runner → still says HQ unauthorized"
loop. The 15-min CCHQ access token TTL means the framework gate must
attempt refresh before declaring a provider inactive, and must do a real
CCHQ ping (not just a timestamp check) when an opportunity_id is in
context, to catch scope-downgrade-on-refresh.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from commcare_connect.users.tests.factories import UserFactory


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def dimagi_user(db):
    user = UserFactory()
    user.email = "test@dimagi.com"
    user.save()
    return user


def _make_request(rf, dimagi_user, query: str, session: dict):
    """Build a GET request to the auth-status endpoint with a session."""
    request = rf.get(f"/labs/workflow/api/auth-status/{query}")
    request.user = dimagi_user
    request.session = session
    return request


class TestAuthStatusRefresh:
    """The framework gate should attempt a silent refresh before reporting inactive."""

    def test_connect_refresh_succeeds_returns_active(self, dimagi_user, rf):
        """Expired Connect token + working refresh_token → active=true after silent refresh."""
        session = {
            "labs_oauth": {
                "access_token": "old",
                "refresh_token": "rt",
                "expires_at": 0,  # expired
            },
            "commcare_oauth": {"access_token": "ok", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ok", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "", session)

        def fake_refresh(req):
            req.session["labs_oauth"] = {
                "access_token": "new",
                "refresh_token": "rt",
                "expires_at": 1e12,
            }
            return True

        with patch("commcare_connect.labs.integrations.connect.oauth.refresh_connect_token", side_effect=fake_refresh):
            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)

        import json

        body = json.loads(response.content)
        assert body["connect"]["active"] is True

    def test_connect_refresh_fails_returns_inactive(self, dimagi_user, rf):
        """Expired Connect token + failing refresh → active=false."""
        session = {
            "labs_oauth": {"access_token": "old", "refresh_token": "rt", "expires_at": 0},
            "commcare_oauth": {"access_token": "ok", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ok", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "", session)

        with patch("commcare_connect.labs.integrations.connect.oauth.refresh_connect_token", return_value=False):
            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)

        import json

        body = json.loads(response.content)
        assert body["connect"]["active"] is False

    def test_no_token_at_all_returns_inactive(self, dimagi_user, rf):
        """No access_token + no refresh_token → never crashes, returns inactive."""
        session = {
            "labs_oauth": {"access_token": "ok", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ok", "expires_at": 1e12},
            # ocs has no token at all
        }
        request = _make_request(rf, dimagi_user, "", session)

        from commcare_connect.workflow.views import workflow_auth_status_api

        response = workflow_auth_status_api(request)
        import json

        body = json.loads(response.content)
        assert body["ocs"]["active"] is False


class TestAuthStatusCCHQProbe:
    """When ?opportunity_id= is supplied, CCHQ ping only runs when token was inactive.

    The real ping catches scope-downgrade-on-refresh: token expired → refresh
    succeeded → but new token has reduced scope → verify_hq_access 403s → gate
    correctly reports inactive.

    When the token is already active (timestamp check passes), we skip the ping.
    A user who just completed a fresh OAuth authorization should always pass the
    gate, regardless of whether their account has CommCare domain membership.
    Domain-access failures surface later as pipeline errors, not auth-gate loops.
    """

    def test_expired_token_triggers_real_ping_on_scope_downgrade(self, dimagi_user, rf):
        """Expired CCHQ token + opportunity_id → ping runs; if scope-downgraded, reports inactive."""
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 0},  # expired
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "?opportunity_id=765", session)

        # Token expired → ping fires; simulate refresh succeeding but returning
        # a scope-downgraded token, so verify_hq_access still rejects.
        with patch(
            "commcare_connect.workflow.templates.mbw_monitoring.data_fetchers.fetch_opportunity_metadata",
            return_value={"cc_domain": "ccc-mbw-production"},
        ), patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            mock_client = MagicMock()
            mock_client.verify_hq_access.return_value = False
            MockCDA.return_value = mock_client

            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)
            mock_client.verify_hq_access.assert_called_once()

        import json

        body = json.loads(response.content)
        assert (
            body["commcare_hq"]["active"] is False
        ), "verify_hq_access returned False (scope downgrade) → gate must report inactive"

    def test_active_token_skips_ping_returns_active(self, dimagi_user, rf):
        """Fresh/active CCHQ token + opportunity_id → ping is skipped, timestamp trusted.

        Prevents a false-negative auth loop where a user with a valid OAuth token
        but without CommCare domain membership could never pass the gate.
        """
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 1e12},  # active
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "?opportunity_id=765", session)

        with patch(
            "commcare_connect.workflow.templates.mbw_monitoring.data_fetchers.fetch_opportunity_metadata",
            return_value={"cc_domain": "ccc-mbw-production"},
        ), patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)
            MockCDA.assert_not_called()

        import json

        body = json.loads(response.content)
        assert body["commcare_hq"]["active"] is True

    def test_no_opportunity_id_falls_back_to_timestamp_with_refresh(self, dimagi_user, rf):
        """Without opportunity_id, BE only does timestamp + refresh — no domain ping."""
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "", session)

        with patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)
            # Without opportunity_id and a non-expired token, we should NOT
            # construct CCHQ client (timestamp fast-path). Verifies we don't
            # spam CCHQ on every page load.
            MockCDA.assert_not_called()

        import json

        body = json.loads(response.content)
        assert body["commcare_hq"]["active"] is True


class TestRefreshHelpers:
    """Direct unit tests for the refresh helpers."""

    def test_refresh_connect_token_no_refresh_token(self, rf):
        from commcare_connect.labs.integrations.connect.oauth import refresh_connect_token

        request = rf.get("/")
        request.session = {"labs_oauth": {"access_token": "x"}}  # no refresh_token

        assert refresh_connect_token(request) is False

    def test_refresh_connect_token_success_persists_to_session(self, rf, settings):
        from commcare_connect.labs.integrations.connect.oauth import refresh_connect_token

        settings.CONNECT_OAUTH_CLIENT_ID = "cid"
        settings.CONNECT_OAUTH_CLIENT_SECRET = "csec"
        settings.CONNECT_PRODUCTION_URL = "https://example.test"

        request = rf.get("/")
        request.session = {
            "labs_oauth": {
                "access_token": "old",
                "refresh_token": "rt",
                "expires_at": 0,
                "user_profile": {"username": "u"},  # should be preserved
            }
        }

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "access_token": "new",
            "refresh_token": "rt2",
            "expires_in": 3600,
        }
        with patch("httpx.post", return_value=fake_response):
            ok = refresh_connect_token(request)

        assert ok is True
        new = request.session["labs_oauth"]
        assert new["access_token"] == "new"
        assert new["refresh_token"] == "rt2"
        assert new["user_profile"] == {"username": "u"}, "user_profile should be preserved"

    def test_refresh_connect_token_http_error_returns_false(self, rf, settings):
        from commcare_connect.labs.integrations.connect.oauth import refresh_connect_token

        settings.CONNECT_OAUTH_CLIENT_ID = "cid"
        settings.CONNECT_OAUTH_CLIENT_SECRET = "csec"
        settings.CONNECT_PRODUCTION_URL = "https://example.test"

        request = rf.get("/")
        request.session = {"labs_oauth": {"access_token": "old", "refresh_token": "rt", "expires_at": 0}}
        fake_response = MagicMock()
        fake_response.status_code = 400
        fake_response.text = "bad"
        with patch("httpx.post", return_value=fake_response):
            assert refresh_connect_token(request) is False
        # Session preserved on failure
        assert request.session["labs_oauth"]["access_token"] == "old"

    def test_ocs_refresh_token_success(self, rf, settings):
        from commcare_connect.labs.integrations.ocs.api_client import OCSDataAccess

        settings.OCS_OAUTH_CLIENT_ID = "cid"
        settings.OCS_OAUTH_CLIENT_SECRET = "csec"
        settings.OCS_URL = "https://example.test"

        request = rf.get("/")
        request.session = {
            "ocs_oauth": {
                "access_token": "old",
                "refresh_token": "rt",
                "expires_at": 0,
            }
        }

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "access_token": "new",
            "refresh_token": "rt2",
            "expires_in": 3600,
        }
        with patch("httpx.post", return_value=fake_response):
            client = OCSDataAccess(request)
            ok = client._refresh_token()

        assert ok is True
        assert request.session["ocs_oauth"]["access_token"] == "new"

    def test_ocs_check_token_valid_triggers_refresh(self, rf, settings):
        """check_token_valid() should auto-refresh on expiry."""
        from commcare_connect.labs.integrations.ocs.api_client import OCSDataAccess

        settings.OCS_OAUTH_CLIENT_ID = "cid"
        settings.OCS_OAUTH_CLIENT_SECRET = "csec"
        settings.OCS_URL = "https://example.test"

        request = rf.get("/")
        request.session = {
            "ocs_oauth": {
                "access_token": "old",
                "refresh_token": "rt",
                "expires_at": 0,  # expired
            }
        }

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "access_token": "new",
            "refresh_token": "rt2",
            "expires_in": 3600,
        }
        with patch("httpx.post", return_value=fake_response):
            client = OCSDataAccess(request)
            assert client.check_token_valid() is True
        # Session was updated by the refresh
        assert request.session["ocs_oauth"]["access_token"] == "new"
