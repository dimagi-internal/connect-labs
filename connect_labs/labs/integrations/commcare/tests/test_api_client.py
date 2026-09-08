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

import contextlib
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.utils import timezone

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


class TestFetchCasesRaiseOnHttpError:
    """raise_on_http_error=True (used by the analysis-pipeline WA fetcher, NOT
    the campaign tool's worker roster) turns a silent partial/empty result
    into a real exception — the diagnostic needed when a non-auth HTTP error
    (not 401/403, so not caught by the auth-retry path above) is what's
    actually producing "0 work areas" with no visible error anywhere."""

    def test_non_auth_http_error_raises_when_opted_in(self):
        client = _client()
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 404
        error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=error_response
        )

        with (
            patch("httpx.get", return_value=error_response),
            patch.object(client, "check_token_valid", return_value=True),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                client.fetch_cases(case_type="work-area", raise_on_http_error=True)

    def test_request_error_raises_when_opted_in(self):
        client = _client()

        with (
            patch("httpx.get", side_effect=httpx.ConnectError("connection refused")),
            patch.object(client, "check_token_valid", return_value=True),
        ):
            with pytest.raises(httpx.ConnectError):
                client.fetch_cases(case_type="work-area", raise_on_http_error=True)

    def test_auth_error_still_raises_cchqautherror_when_opted_in(self):
        """raise_on_http_error doesn't change 401/403 handling — that path
        already raises CCHQAuthError regardless of this flag."""
        client = _client()

        with (
            patch("httpx.get", return_value=_auth_error_response(401)),
            patch.object(client, "check_token_valid", return_value=True),
            patch.object(client, "_refresh_token", return_value=False),
        ):
            with pytest.raises(CCHQAuthError):
                client.fetch_cases(case_type="work-area", raise_on_http_error=True)

    def test_success_unaffected_by_flag(self):
        client = _client()

        with (
            patch("httpx.get", return_value=_ok_response([{"case_id": "a"}])),
            patch.object(client, "check_token_valid", return_value=True),
        ):
            cases = client.fetch_cases(case_type="work-area", raise_on_http_error=True)

        assert cases == [{"case_id": "a"}]


class TestRefreshTokenLocking:
    """_refresh_token() serializes per-user via a redis lock — see its
    docstring. A single page load fires several requests (the auth-status
    check, the pipeline SSE stream) that can each decide the CCHQ token
    looks expired at the same moment; without serialization they race to
    redeem the SAME refresh_token, and CCHQ rejects the loser since refresh
    tokens are single-use. Reproduced live as a user needing to click
    "Authorize CommCare HQ" multiple times before it stuck.
    """

    def _client_with_user(self, refresh_token="old-refresh"):
        request = MagicMock()
        request.user.id = 42
        request.session = {"commcare_oauth": {"access_token": "expired", "refresh_token": refresh_token}}
        return CommCareDataAccess(request, domain="connect-chc-ng-isodaf")

    def test_loser_reuses_winners_fresh_token_without_a_second_exchange(self):
        """While this call waited for the lock, a concurrent request already
        refreshed the session — re-reading it should short-circuit before any
        network exchange, not race a second redemption of the same
        (already-consumed) refresh_token."""
        client = self._client_with_user()

        def fake_lock(key, **kwargs):
            # Simulate the OTHER request's successful refresh landing in the
            # session while this caller held/waited on the lock.
            client.request.session["commcare_oauth"] = {
                "access_token": "winners-token",
                "refresh_token": "winners-refresh",
                "expires_at": timezone.now().timestamp() + 3600,
            }
            return contextlib.nullcontext(True)

        with (
            patch("connect_labs.labs.integrations.commcare.api_client.try_redis_lock", side_effect=fake_lock),
            patch.object(client, "_exchange_refresh_token") as mock_exchange,
        ):
            assert client._refresh_token() is True

        mock_exchange.assert_not_called()
        assert client.access_token == "winners-token"

    def test_acquires_lock_and_exchanges_when_no_fresh_token_appeared(self):
        client = self._client_with_user()

        with (
            patch(
                "connect_labs.labs.integrations.commcare.api_client.try_redis_lock",
                return_value=contextlib.nullcontext(True),
            ),
            patch.object(client, "_exchange_refresh_token", return_value=True) as mock_exchange,
        ):
            assert client._refresh_token() is True

        mock_exchange.assert_called_once_with("old-refresh")

    def test_lock_busy_and_no_fresh_token_fails_without_exchanging(self):
        """A concurrent refresh is already in flight and hasn't finished by
        the time our wait times out — don't ALSO race an exchange of the
        same refresh_token; report failure and let the caller retry."""
        client = self._client_with_user()

        with (
            patch(
                "connect_labs.labs.integrations.commcare.api_client.try_redis_lock",
                return_value=contextlib.nullcontext(False),
            ),
            patch.object(client, "_exchange_refresh_token") as mock_exchange,
        ):
            assert client._refresh_token() is False

        mock_exchange.assert_not_called()

    def test_broken_lock_backend_degrades_to_unlocked_exchange(self):
        """A redis blip or other lock-machinery failure must not turn a
        graceful 'please reauthorize' outcome into an unhandled 500 — fall
        back to the old unlocked behavior instead."""
        client = self._client_with_user()

        with (
            patch(
                "connect_labs.labs.integrations.commcare.api_client.try_redis_lock",
                side_effect=RuntimeError("redis is down"),
            ),
            patch.object(client, "_exchange_refresh_token", return_value=True) as mock_exchange,
        ):
            assert client._refresh_token() is True

        mock_exchange.assert_called_once_with("old-refresh")

    def test_no_request_context_fails_without_locking(self):
        client = CommCareDataAccess(None, domain="connect-chc-ng-isodaf", cchq_access_token=None)
        client.request = None
        client.commcare_oauth = {"refresh_token": "whatever"}

        with patch("connect_labs.labs.integrations.commcare.api_client.try_redis_lock") as mock_lock:
            assert client._refresh_token() is False

        mock_lock.assert_not_called()
