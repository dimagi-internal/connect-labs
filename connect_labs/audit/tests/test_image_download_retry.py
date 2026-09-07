"""Unit tests for AuditDataAccess.download_image_from_connect retry behavior.

The image proxy used to fail on the first transient upstream hiccup (returning a
bare 404 to the browser). These tests pin the bounded retry-with-backoff:
transient failures (connection errors / 5xx) are retried; 4xx fails fast.
"""

from unittest.mock import Mock

import httpx
import pytest

from connect_labs.audit.data_access import AuditDataAccess


def _make_data_access():
    """A bare instance with just the attributes download_image_from_connect touches."""
    da = object.__new__(AuditDataAccess)
    da.http_client = Mock()
    da.production_url = "https://connect.example"
    return da


def _ok_response(content=b"JPEGDATA"):
    resp = Mock()
    resp.content = content
    resp.raise_for_status = Mock(return_value=None)
    return resp


def _status_error(code):
    request = httpx.Request("GET", "https://connect.example/img")
    response = httpx.Response(code, request=request)
    err = httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)
    resp = Mock()
    resp.raise_for_status = Mock(side_effect=err)
    return resp


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("connect_labs.audit.data_access.time.sleep", lambda *_a, **_k: None)


def test_success_first_try_no_retry():
    da = _make_data_access()
    da.http_client.get.return_value = _ok_response(b"IMG")
    assert da.download_image_from_connect("blob1", 1) == b"IMG"
    assert da.http_client.get.call_count == 1


def test_retries_transient_connection_error_then_succeeds():
    da = _make_data_access()
    da.http_client.get.side_effect = [
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
        _ok_response(b"IMG"),
    ]
    assert da.download_image_from_connect("blob1", 1) == b"IMG"
    assert da.http_client.get.call_count == 3


def test_retries_5xx_then_succeeds():
    da = _make_data_access()
    da.http_client.get.side_effect = [_status_error(503), _ok_response(b"IMG")]
    assert da.download_image_from_connect("blob1", 1) == b"IMG"
    assert da.http_client.get.call_count == 2


def test_4xx_fails_fast_without_retry():
    da = _make_data_access()
    da.http_client.get.return_value = _status_error(404)
    with pytest.raises(ValueError, match="HTTP 404"):
        da.download_image_from_connect("blob1", 1)
    assert da.http_client.get.call_count == 1  # no retry on client error


def test_gives_up_after_max_attempts_on_persistent_transient_failure():
    da = _make_data_access()
    da.http_client.get.side_effect = httpx.ConnectError("boom")
    with pytest.raises(ValueError, match="connection error"):
        da.download_image_from_connect("blob1", 1)
    assert da.http_client.get.call_count == AuditDataAccess.IMAGE_DOWNLOAD_MAX_ATTEMPTS


class TestBackoffIsBilledToTelemetry:
    """#1435: the backoff sleep is real time on the request thread, and it was
    landing in ``self_ms`` -- the residual -- where it read as our own CPU on the
    very endpoint whose residual was under investigation.

    Note ``_no_sleep`` above patches the sleep out, so these assert the WIRING
    (that a wait is recorded per retry, and how many), not the wall-clock value.
    """

    def test_each_retry_records_one_wait(self):
        from connect_labs.utils import request_telemetry
        from connect_labs.utils.request_telemetry import RequestStats

        da = _make_data_access()
        da.http_client.get.side_effect = [
            httpx.ConnectError("boom"),
            httpx.ConnectError("boom"),
            _ok_response(b"IMG"),
        ]
        token = request_telemetry._stats.set(RequestStats())
        try:
            assert da.download_image_from_connect("blob1", 1) == b"IMG"
            stats = request_telemetry.current_stats()
            # Three attempts, two gaps between them.
            assert stats.retry_waits == 2
            assert da.http_client.get.call_count == stats.retry_waits + 1
        finally:
            request_telemetry._stats.reset(token)

    def test_a_first_try_success_records_no_wait(self):
        from connect_labs.utils import request_telemetry
        from connect_labs.utils.request_telemetry import RequestStats

        da = _make_data_access()
        da.http_client.get.return_value = _ok_response(b"IMG")
        token = request_telemetry._stats.set(RequestStats())
        try:
            da.download_image_from_connect("blob1", 1)
            assert request_telemetry.current_stats().retry_waits == 0
        finally:
            request_telemetry._stats.reset(token)

    def test_a_4xx_fails_fast_and_records_no_wait(self):
        """The fast-fail path must not be billed a wait it never took."""
        from connect_labs.utils import request_telemetry
        from connect_labs.utils.request_telemetry import RequestStats

        da = _make_data_access()
        da.http_client.get.return_value = _status_error(404)
        token = request_telemetry._stats.set(RequestStats())
        try:
            with pytest.raises(ValueError):
                da.download_image_from_connect("blob1", 1)
            assert request_telemetry.current_stats().retry_waits == 0
        finally:
            request_telemetry._stats.reset(token)

    def test_download_still_works_outside_a_request_context(self):
        """Celery and management commands call this with no stats context."""
        da = _make_data_access()
        da.http_client.get.side_effect = [httpx.ConnectError("boom"), _ok_response(b"IMG")]
        assert da.download_image_from_connect("blob1", 1) == b"IMG"
