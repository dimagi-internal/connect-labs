"""Tests for workflow_auth_status_api.

Covers the silent-refresh, token-alive, and domain-access probes that
together kill the CCHQ re-auth loop.

Two probes feed the auth-status response:

* verify_token_alive() pings /api/v0.5/identity/ — domain-less. Answers
  "is the OAuth token accepted by HQ at all".
* verify_hq_access() pings /a/{domain}/api/form/v1/ — same endpoint
  pipelines use. Answers "can this token read forms in this domain".

The combination produces a structured `reason` so the runner UI can
distinguish "re-auth WILL fix it" (token_expired) from "re-auth WON'T
fix it; user needs HQ admin help" (no_domain_access).
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
        """Expired Connect token + working refresh_token -> active=true after silent refresh."""
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
        """Expired Connect token + failing refresh -> active=false."""
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
        """No access_token + no refresh_token -> never crashes, returns inactive."""
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
    """The gate distinguishes 'token dead' (re-auth fixes) from 'no domain access' (re-auth won't fix).

    Replaces the older PR #104 'skip the probe when timestamp valid'
    workaround. PR #104 was solving the right problem (false-negative
    loop for users without domain membership) but via a workaround. The
    root cause is that the previous probe used /api/application/v1
    which requires app-builder scope LLO accounts often lack. Switching
    to /api/form/v1 (the SAME endpoint pipelines use) and adding
    /api/v0.5/identity/ as a separate token-alive probe produces
    accurate, actionable status — no need to skip the probe.
    """

    def test_token_alive_and_domain_access_returns_active(self, dimagi_user, rf):
        """Both probes pass -> active=true, no reason field."""
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "?opportunity_id=765", session)

        with patch(
            "commcare_connect.labs.analysis.data_access.fetch_opportunity_metadata",
            return_value={"cc_domain": "ccc-mbw-production"},
        ), patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            mock_client = MagicMock()
            mock_client.verify_token_alive.return_value = True
            mock_client.verify_hq_access.return_value = True
            MockCDA.return_value = mock_client

            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)

        import json

        body = json.loads(response.content)
        assert body["commcare_hq"]["active"] is True
        assert "reason" not in body["commcare_hq"]

    def test_token_dead_returns_token_expired_reason(self, dimagi_user, rf):
        """verify_token_alive=False -> reason=token_expired (re-auth WILL fix).

        Should short-circuit and NOT call verify_hq_access — no point pinging
        the domain endpoint when the token is already known dead.
        """
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "?opportunity_id=765", session)

        with patch(
            "commcare_connect.labs.analysis.data_access.fetch_opportunity_metadata",
            return_value={"cc_domain": "ccc-mbw-production"},
        ), patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            mock_client = MagicMock()
            mock_client.verify_token_alive.return_value = False
            MockCDA.return_value = mock_client

            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)
            mock_client.verify_hq_access.assert_not_called()

        import json

        body = json.loads(response.content)
        assert body["commcare_hq"]["active"] is False
        assert body["commcare_hq"]["reason"] == "token_expired"

    def test_token_alive_but_no_domain_access(self, dimagi_user, rf):
        """Token alive, verify_hq_access=False -> reason=no_domain_access (re-auth WON'T fix).

        This is the loop-killing case. Previously the user kept getting an
        Authorize button that did nothing because their account lacks domain
        membership. Now the UI knows to surface "contact HQ admin" instead.
        """
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "?opportunity_id=765", session)

        with patch(
            "commcare_connect.labs.analysis.data_access.fetch_opportunity_metadata",
            return_value={"cc_domain": "ccc-mbw-production"},
        ), patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            mock_client = MagicMock()
            mock_client.verify_token_alive.return_value = True
            mock_client.verify_hq_access.return_value = False
            MockCDA.return_value = mock_client

            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)

        import json

        body = json.loads(response.content)
        assert body["commcare_hq"]["active"] is False
        assert body["commcare_hq"]["reason"] == "no_domain_access"
        assert body["commcare_hq"]["domain"] == "ccc-mbw-production"
        assert "form-read access" in body["commcare_hq"]["message"]

    def test_no_opportunity_id_only_does_token_alive(self, dimagi_user, rf):
        """Without opportunity_id, only check token-alive — no domain ping (no domain to ping)."""
        session = {
            "labs_oauth": {"access_token": "lt", "expires_at": 1e12},
            "commcare_oauth": {"access_token": "ct", "expires_at": 1e12},
            "ocs_oauth": {"access_token": "ot", "expires_at": 1e12},
        }
        request = _make_request(rf, dimagi_user, "", session)

        with patch("commcare_connect.labs.integrations.commcare.api_client.CommCareDataAccess") as MockCDA:
            mock_client = MagicMock()
            mock_client.verify_token_alive.return_value = True
            MockCDA.return_value = mock_client

            from commcare_connect.workflow.views import workflow_auth_status_api

            response = workflow_auth_status_api(request)
            mock_client.verify_token_alive.assert_called_once()
            mock_client.verify_hq_access.assert_not_called()

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
