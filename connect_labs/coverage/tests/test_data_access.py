"""Tests for CoverageDataAccess.get_opportunity_metadata()'s CCHQ oauth gate.

Exists because a silent CCHQ token refresh mutates the SESSION, not any
cached attribute on this object — get_opportunity_metadata() captures
commcare_access_token/commcare_oauth once in __init__, so a refresh that
happens INSIDE this method must re-read the session afterward, or every
downstream fetch_delivery_units_from_commcare() call would keep sending the
stale, now-invalid pre-refresh token even though the session holds a good one.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from connect_labs.coverage.data_access import CoverageDataAccess


def _fake_request(commcare_oauth, labs_oauth=None):
    request = MagicMock()
    request.session = {
        "labs_oauth": labs_oauth or {"access_token": "connect-token"},
        "commcare_oauth": commcare_oauth,
    }
    request.labs_context = {"opportunity_id": 1234}
    return request


def _opp_response():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"deliver_app": {"cc_domain": "connect-chc-ng-isodaf"}}
    return resp


class TestGetOpportunityMetadataCchqGate:
    def test_refresh_updates_cached_token_for_downstream_calls(self):
        """A refresh that happens during the gate check must be picked up by
        this object's own commcare_access_token — not just the session — so a
        subsequent fetch_delivery_units_from_commcare() on the SAME instance
        sends the fresh token, not the stale pre-refresh one."""
        request = _fake_request({"access_token": "stale", "refresh_token": "r", "expires_at": 0})
        data_access = CoverageDataAccess(request)
        assert data_access.commcare_access_token == "stale"

        def fake_is_active(req):
            # Simulate a successful silent refresh landing in the session.
            req.session["commcare_oauth"] = {
                "access_token": "fresh",
                "refresh_token": "r2",
                "expires_at": 9999999999,
            }
            return True

        with (
            patch("httpx.get", return_value=_opp_response()),
            patch("connect_labs.coverage.data_access.is_cchq_oauth_active", side_effect=fake_is_active),
        ):
            data_access.get_opportunity_metadata()

        assert data_access.commcare_access_token == "fresh"

    def test_raises_when_cchq_oauth_inactive(self):
        request = _fake_request({"access_token": "stale", "expires_at": 0})
        data_access = CoverageDataAccess(request)

        with (
            patch("httpx.get", return_value=_opp_response()),
            patch("connect_labs.coverage.data_access.is_cchq_oauth_active", return_value=False),
        ):
            with pytest.raises(ValueError, match="CommCare OAuth not configured or expired"):
                data_access.get_opportunity_metadata()

    def test_succeeds_when_cchq_already_active(self):
        request = _fake_request({"access_token": "good", "expires_at": 9999999999})
        data_access = CoverageDataAccess(request)

        with (
            patch("httpx.get", return_value=_opp_response()),
            patch("connect_labs.coverage.data_access.is_cchq_oauth_active", return_value=True),
        ):
            opp_data = data_access.get_opportunity_metadata()

        assert opp_data["deliver_app"]["cc_domain"] == "connect-chc-ng-isodaf"
