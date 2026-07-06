"""Tests for CommCareDataAccess.fetch_cases' auth-error handling.

These exist because fetch_cases silently swallowed 401/403 responses and
returned whatever cases had been accumulated so far (even an empty list) as
if the fetch had succeeded — the exact bug CCHQAuthError exists to prevent
(see its docstring: "V1/V2 used to silently return 0 forms when CCHQ
rejected the call, leaving users to wonder why their dashboards were
empty"). fetch_forms/iter_forms already had the retry-then-raise fix;
fetch_cases had been missed. Reproduced live: a program-owned Ward Progress
Tracker workflow showed 0 work areas for every opportunity, with no error
anywhere in the pipeline metadata, even immediately after re-authorizing
CommCare HQ and forcing a cache-bypassing refresh.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from connect_labs.labs.integrations.commcare.api_client import CCHQAuthError, CommCareDataAccess


def _fake_request():
    request = MagicMock()
    request.session = {"commcare_oauth": {"access_token": "initial-token"}}
    return request


def _ok_response(cases, next_url=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"cases": cases, "next": next_url}
    return resp


def _auth_error_response(status_code):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    return resp


def _client():
    return CommCareDataAccess(_fake_request(), domain="connect-chc-ng-isodaf")


class TestFetchCasesAuthHandling:
    def test_retries_once_after_401_then_succeeds(self):
        client = _client()
        responses = [_auth_error_response(401), _ok_response([{"case_id": "a"}])]

        with (
            patch("httpx.get", side_effect=responses),
            patch.object(client, "check_token_valid", return_value=True),
            patch.object(client, "_refresh_token", return_value=True) as mock_refresh,
        ):
            cases = client.fetch_cases(case_type="work-area")

        assert cases == [{"case_id": "a"}]
        mock_refresh.assert_called_once()

    def test_raises_cchqautherror_when_retry_also_fails(self):
        """The exact scenario that silently returned an empty list before this
        fix: token refresh either fails outright, or the retried request is
        STILL rejected. Either way the caller must see CCHQAuthError, never a
        quiet empty success."""
        client = _client()
        responses = [_auth_error_response(401), _auth_error_response(403)]

        with (
            patch("httpx.get", side_effect=responses),
            patch.object(client, "check_token_valid", return_value=True),
            patch.object(client, "_refresh_token", return_value=True),
        ):
            with pytest.raises(CCHQAuthError) as excinfo:
                client.fetch_cases(case_type="work-area")

        assert excinfo.value.status_code == 403
        assert excinfo.value.domain == "connect-chc-ng-isodaf"

    def test_raises_cchqautherror_when_refresh_itself_fails(self):
        client = _client()

        with (
            patch("httpx.get", return_value=_auth_error_response(401)),
            patch.object(client, "check_token_valid", return_value=True),
            patch.object(client, "_refresh_token", return_value=False) as mock_refresh,
        ):
            with pytest.raises(CCHQAuthError):
                client.fetch_cases(case_type="work-area")

        mock_refresh.assert_called_once()

    def test_does_not_retry_more_than_once(self):
        """A second consecutive 401 (even after a successful refresh) must
        raise, not loop forever or retry indefinitely."""
        client = _client()
        responses = [_auth_error_response(401), _auth_error_response(401)]

        with (
            patch("httpx.get", side_effect=responses),
            patch.object(client, "check_token_valid", return_value=True),
            patch.object(client, "_refresh_token", return_value=True) as mock_refresh,
        ):
            with pytest.raises(CCHQAuthError):
                client.fetch_cases(case_type="work-area")

        mock_refresh.assert_called_once()

    def test_non_auth_http_error_still_returns_partial_results(self):
        """Unchanged legacy behavior for non-auth failures (e.g. transient
        5xx) — only 401/403 gets the loud-failure treatment."""
        client = _client()
        first_page_ok = _ok_response([{"case_id": "a"}], next_url="https://x/next")
        second_page_error = MagicMock(spec=httpx.Response)
        second_page_error.status_code = 500
        second_page_error.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 error", request=MagicMock(), response=second_page_error
        )

        with (
            patch("httpx.get", side_effect=[first_page_ok, second_page_error]),
            patch.object(client, "check_token_valid", return_value=True),
            patch.object(client, "_validate_pagination_url", return_value=True),
        ):
            cases = client.fetch_cases(case_type="work-area")

        assert cases == [{"case_id": "a"}]

    def test_request_error_still_returns_partial_results(self):
        client = _client()

        with (
            patch("httpx.get", side_effect=httpx.ConnectError("connection refused")),
            patch.object(client, "check_token_valid", return_value=True),
        ):
            cases = client.fetch_cases(case_type="work-area")

        assert cases == []
