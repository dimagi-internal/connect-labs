"""Coverage for per-request cost telemetry.

The shape being pinned is the one the 2026-07-29 incident needed and didn't
have: a request that fans out to a remote API must produce ONE structured line
naming the fan-out, instead of thousands of anonymous httpx INFO lines.

A clean request must stay completely silent — this runs on every request, so
"costs nothing when nothing is wrong" is a correctness property, not a nicety.
"""

import json
import logging
import time
from unittest.mock import MagicMock

import pytest

from connect_labs.utils import request_telemetry
from connect_labs.utils.request_telemetry import (
    RequestStats,
    RequestTelemetryMiddleware,
    current_stats,
    record_outbound_call,
)


@pytest.fixture(autouse=True)
def caplog(caplog):
    """Capture the telemetry stream, which deliberately does not propagate.

    The logger is configured with propagate=False so these lines stay out of the
    human-readable root handler and Sentry breadcrumbs. caplog attaches to the
    ROOT logger, so without this it silently captures nothing and every
    assertion trivially "passes" by seeing zero lines.
    """
    telemetry_logger = logging.getLogger("connect_labs.telemetry.request")
    telemetry_logger.addHandler(caplog.handler)
    telemetry_logger.setLevel(logging.WARNING)
    yield caplog
    telemetry_logger.removeHandler(caplog.handler)


@pytest.fixture
def request_obj():
    req = MagicMock()
    req.method = "GET"
    req.path = "/audit/review/"
    req.user.is_authenticated = True
    req.user.username = "auditor@example.com"
    return req


def _middleware(get_response):
    return RequestTelemetryMiddleware(get_response)


def _lines(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == "connect_labs.telemetry.request"]


class TestSilenceWhenHealthy:
    def test_fast_clean_request_logs_nothing(self, request_obj, caplog):
        caplog.set_level("WARNING")
        mw = _middleware(lambda r: "response")

        assert mw(request_obj) == "response"
        assert _lines(caplog) == []

    def test_counters_do_not_leak_between_requests(self, request_obj, caplog):
        """Stats are per-request; a fan-out in one must not implicate the next."""

        def noisy(r):
            for _ in range(50):
                record_outbound_call("connect.dimagi.com")
            return "ok"

        caplog.set_level("WARNING")
        _middleware(noisy)(request_obj)
        first = _lines(caplog)
        assert len(first) == 1 and first[0]["outbound_calls"] == 50

        caplog.clear()
        _middleware(lambda r: "ok")(request_obj)
        assert _lines(caplog) == []

    def test_no_stats_outside_a_request(self):
        """Celery tasks and shell sessions have no request context to charge."""
        assert current_stats() is None
        record_outbound_call("connect.dimagi.com")  # must not raise


class TestFanoutDetection:
    def test_outbound_fanout_is_reported_as_one_line(self, request_obj, caplog):
        """The 2026-07-29 signature: ~139 sequential calls to one host."""

        def fanning_out(r):
            for _ in range(139):
                record_outbound_call("connect.dimagi.com")
            return "ok"

        caplog.set_level("WARNING")
        _middleware(fanning_out)(request_obj)

        (line,) = _lines(caplog)
        assert line["event"] == "slow_request"
        assert "outbound_fanout" in line["reason"]
        assert line["outbound_calls"] == 139
        assert line["outbound_by_host"] == {"connect.dimagi.com": 139}
        assert line["path"] == "/audit/review/"
        assert line["username"] == "auditor@example.com"

    def test_below_threshold_stays_silent(self, request_obj, caplog):
        def modest(r):
            for _ in range(request_telemetry.OUTBOUND_CALL_LIMIT - 1):
                record_outbound_call("connect.dimagi.com")
            return "ok"

        caplog.set_level("WARNING")
        _middleware(modest)(request_obj)
        assert _lines(caplog) == []

    def test_slow_request_is_reported(self, request_obj, caplog, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)
        caplog.set_level("WARNING")

        _middleware(lambda r: "ok")(request_obj)

        (line,) = _lines(caplog)
        assert "slow" in line["reason"]
        assert line["duration_ms"] >= 0


class TestRobustness:
    def test_telemetry_never_swallows_the_response(self, request_obj):
        sentinel = object()
        assert _middleware(lambda r: sentinel)(request_obj) is sentinel

    def test_view_exception_propagates_and_still_resets_state(self, request_obj):
        def boom(r):
            raise ValueError("view exploded")

        with pytest.raises(ValueError, match="view exploded"):
            _middleware(boom)(request_obj)

        # The contextvar must be reset even on the error path, or the next
        # request on this thread inherits a stale counter.
        assert current_stats() is None

    def test_logging_failure_cannot_break_the_request(self, request_obj, monkeypatch):
        """Telemetry is never allowed to take down the thing it measures."""

        def explode(*a, **kw):
            raise RuntimeError("logging is broken")

        monkeypatch.setattr(RequestTelemetryMiddleware, "_maybe_log", explode)
        assert _middleware(lambda r: "ok")(request_obj) == "ok"

    def test_anonymous_user_logs_null_username(self, caplog, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)
        req = MagicMock()
        req.method = "GET"
        req.path = "/health/"
        req.user.is_authenticated = False

        caplog.set_level("WARNING")
        _middleware(lambda r: "ok")(req)

        (line,) = _lines(caplog)
        assert line["username"] is None

    def test_query_string_is_never_logged(self, caplog, monkeypatch):
        """Labs URLs carry record and opportunity ids; this is not the audit trail."""
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)
        req = MagicMock()
        req.method = "GET"
        req.path = "/audit/review/"
        req.META = {"QUERY_STRING": "id=10688&opportunity_id=385"}
        req.user.is_authenticated = False

        caplog.set_level("WARNING")
        _middleware(lambda r: "ok")(req)

        (line,) = _lines(caplog)
        assert "10688" not in json.dumps(line)


class TestHttpxIntegration:
    def test_event_hook_counts_a_response(self):
        hooks = request_telemetry.httpx_event_hooks()
        (on_response,) = hooks["response"]

        response = MagicMock()
        response.request.url.host = "connect.dimagi.com"

        token = request_telemetry._stats.set(RequestStats())
        try:
            on_response(response)
            assert current_stats().outbound_calls == 1
            assert current_stats().outbound_by_host["connect.dimagi.com"] == 1
        finally:
            request_telemetry._stats.reset(token)

    def test_event_hook_survives_a_malformed_response(self):
        """A telemetry hook must never break the HTTP call it is observing."""
        hooks = request_telemetry.httpx_event_hooks()
        (on_response,) = hooks["response"]

        class Broken:
            @property
            def request(self):
                raise AttributeError("no request")

        token = request_telemetry._stats.set(RequestStats())
        try:
            on_response(Broken())  # must not raise
            assert current_stats().outbound_calls == 0
        finally:
            request_telemetry._stats.reset(token)


class TestDurationAttribution:
    """The 2026-08-11 signature: slow with only 2 calls and ~16 queries.

    A count-only line cannot say whether such a request was waiting on Connect
    or burning local CPU, and that ambiguity is what dead-ended the review.
    These pin the split, not just the totals.

    Note what is deliberately NOT added: outbound_ms is not its own trigger. A
    request that waits 14s on Connect has a 14s wall-clock duration and already
    trips ``slow``; a second threshold on the same seconds would only double-report
    it. The timing changes what a logged line can TELL you, not which lines log.
    """

    @pytest.fixture
    def always_log(self, monkeypatch):
        """Force the slow threshold: these assert on the payload, not the trigger."""
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)

    def test_time_waiting_on_an_upstream_is_attributed_to_it(self, request_obj, caplog, always_log):
        def slow_upstream(r):
            record_outbound_call("connect.dimagi.com", elapsed_ms=8000)
            record_outbound_call("connect.dimagi.com", elapsed_ms=6000)
            return "ok"

        caplog.set_level("WARNING")
        _middleware(slow_upstream)(request_obj)

        (line,) = _lines(caplog)
        assert line["outbound_calls"] == 2
        assert line["outbound_ms"] == 14000
        # Two calls is far below the fan-out limit, so the ONLY thing that can
        # explain this request is the time -- which is the point.
        assert "outbound_fanout" not in line["reason"]

    def test_local_cpu_is_not_blamed_on_the_upstream(self, request_obj, caplog, always_log):
        """A slow request making one fast call must show the time as self_ms.

        This is the discrimination the review actually needed: same small call
        count as a Connect stall, but the seconds belong to us.
        """

        def local_burn(r):
            record_outbound_call("connect.dimagi.com", elapsed_ms=1)
            time.sleep(0.05)
            return "ok"

        caplog.set_level("WARNING")
        _middleware(local_burn)(request_obj)

        (line,) = _lines(caplog)
        assert line["outbound_ms"] == 1
        assert line["duration_ms"] >= 50
        # Nearly all of it is ours, and the split says so rather than leaving a
        # reader to guess from a call count.
        assert line["self_ms"] >= 40
        assert line["self_ms"] == line["duration_ms"] - line["outbound_ms"] - line["db_ms"]

    def test_self_ms_never_goes_negative(self, request_obj, caplog, always_log):
        """Overlapping waits must not produce a nonsense negative remainder."""

        def overreporting(r):
            record_outbound_call("connect.dimagi.com", elapsed_ms=999_999)
            return "ok"

        caplog.set_level("WARNING")
        _middleware(overreporting)(request_obj)

        (line,) = _lines(caplog)
        assert line["self_ms"] == 0

    def test_request_hook_stamps_a_start_time(self):
        hooks = request_telemetry.httpx_event_hooks()
        (on_request,) = hooks["request"]
        (on_response,) = hooks["response"]

        class Req:
            pass

        req = Req()
        req.url = MagicMock()
        req.url.host = "connect.dimagi.com"

        token = request_telemetry._stats.set(RequestStats())
        try:
            on_request(req)
            response = MagicMock()
            response.request = req
            on_response(response)
            assert current_stats().outbound_calls == 1
            assert current_stats().outbound_ms > 0
        finally:
            request_telemetry._stats.reset(token)

    def test_unstamped_request_still_counts_the_call(self):
        """Timing is best-effort; the COUNT is the load-bearing signal."""
        hooks = request_telemetry.httpx_event_hooks()
        (on_response,) = hooks["response"]

        response = MagicMock()  # .request has no start stamp
        response.request.url.host = "connect.dimagi.com"

        token = request_telemetry._stats.set(RequestStats())
        try:
            on_response(response)
            assert current_stats().outbound_calls == 1
            assert current_stats().outbound_ms == 0.0
        finally:
            request_telemetry._stats.reset(token)
