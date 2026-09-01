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
from unittest.mock import MagicMock, patch

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


class TestHttpxInstrumentationIsDefaultOn:
    """Every outbound call counts, not only the one client that opted in.

    connect-labs#1298: ``/labs/callback/`` was logged at 4-16.5s with
    ``outbound_calls: 0``, ``outbound_ms: 0`` and ``self_ms`` ~99% of duration, which
    reads as "our own CPU" and sent the investigation hunting for a hot loop. The view
    provably makes four outbound calls; none were counted, because the hooks were
    opt-in and only ``LabsRecordAPIClient`` opted in. ``self_ms`` is a RESIDUAL
    (duration - outbound - db), so everything unmeasured lands in it wearing the label
    of our own code.

    The fix is that instrumentation is no longer something a call site has to know
    about. These tests pin that.
    """

    @pytest.fixture(autouse=True)
    def _installed(self):
        request_telemetry.install_httpx_instrumentation()

    def _hooks_of(self, client):
        return client.event_hooks["request"], client.event_hooks["response"]

    def test_a_plain_client_is_instrumented(self):
        """This is the shape ``httpx.get`` / ``httpx.post`` build internally.

        The module-level helpers construct a bare ``httpx.Client`` per call, which is
        exactly what the OAuth callback uses for all four of its requests.
        """
        import httpx

        req, resp = self._hooks_of(httpx.Client())
        assert request_telemetry._on_request in req
        assert request_telemetry._on_response in resp

    def test_an_async_client_gets_awaitable_hooks(self):
        """AsyncClient ``await``s its hooks; a sync function there would raise."""
        import inspect

        import httpx

        req, resp = self._hooks_of(httpx.AsyncClient())
        assert all(inspect.iscoroutinefunction(h) for h in req)
        assert all(inspect.iscoroutinefunction(h) for h in resp)

    def test_caller_supplied_hooks_are_kept(self):
        import httpx

        mine = MagicMock()
        req, resp = self._hooks_of(httpx.Client(event_hooks={"request": [mine], "response": [mine]}))
        assert mine in req and mine in resp
        assert request_telemetry._on_request in req
        assert request_telemetry._on_response in resp

    def test_an_explicitly_opted_in_client_is_not_counted_twice(self):
        """``LabsRecordAPIClient`` still passes ``httpx_event_hooks()`` by hand."""
        import httpx

        req, resp = self._hooks_of(httpx.Client(event_hooks=request_telemetry.httpx_event_hooks()))
        assert req.count(request_telemetry._on_request) == 1
        assert resp.count(request_telemetry._on_response) == 1

    def test_install_is_idempotent(self):
        """Middleware is constructed once per process, but never rely on that."""
        import httpx

        for _ in range(3):
            request_telemetry.install_httpx_instrumentation()

        req, resp = self._hooks_of(httpx.Client())
        assert req.count(request_telemetry._on_request) == 1
        assert resp.count(request_telemetry._on_response) == 1

    def test_an_uninstrumented_client_now_lands_in_outbound_not_self(self):
        """End-to-end: the number #1298 needed. No opt-in anywhere in this test."""
        import httpx

        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

        token = request_telemetry._stats.set(RequestStats())
        try:
            with httpx.Client(transport=transport) as client:
                client.get("https://connect.dimagi.com/export/opp_org_program_list/")

            stats = current_stats()
            assert stats.outbound_calls == 1
            assert stats.outbound_by_host == {"connect.dimagi.com": 1}
        finally:
            request_telemetry._stats.reset(token)

    def test_middleware_construction_installs_it(self):
        """The install point: once per worker, before any request is served."""
        import httpx

        _middleware(lambda r: "ok")
        req, _ = self._hooks_of(httpx.Client())
        assert request_telemetry._on_request in req

    def test_the_1298_line_now_attributes_the_wait_to_outbound(self, request_obj, caplog):
        """The regression, stated as the log line an operator actually reads.

        Before: ``outbound_calls: 0``, ``outbound_ms: 0``, ``self_ms`` ~= duration —
        which says "our own CPU" about a request that was waiting on Connect.
        """
        import httpx

        def handler(request):
            time.sleep(0.05)
            return httpx.Response(200, json={})

        def view_that_calls_connect(_request):
            # No event_hooks anywhere: the shape of every call in oauth_views.py.
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                client.get("https://connect.dimagi.com/export/opp_org_program_list/")
            return "ok"

        caplog.set_level("WARNING")
        with patch.object(request_telemetry, "SLOW_REQUEST_MS", 1):
            _middleware(view_that_calls_connect)(request_obj)

        (line,) = _lines(caplog)
        assert line["outbound_calls"] == 1
        assert line["outbound_by_host"] == {"connect.dimagi.com": 1}
        assert line["outbound_ms"] >= 50, "the wait must land in outbound_ms"
        assert line["self_ms"] < line["outbound_ms"], "and must NOT be relabelled as our own CPU"


class TestBodyDownloadIsNotOurCpu:
    """#1386: the response hook fires at HEADERS, so the body read fell outside it.

    Same class of error as #1298 one level down — that was about which CALLS are
    measured, this is about how much of each call. It mattered because the audit
    image proxy exists to move JPEG bytes: it billed 11,482 s of transfer to
    ``self_ms`` over 7 days and ranked as the top CPU consumer on the web tier,
    ahead of ``bulk-data``, for a view that does no image work at all.
    """

    @staticmethod
    def _slow_body_transport(delay: float, payload: bytes = b"\xff\xd8" + b"x" * 4096):
        """A transport whose HEADERS are instant and whose BODY takes ``delay``.

        This is the shape that matters and the one ``MockTransport`` cannot make:
        it hands back fully-materialised content, so its body read is free and the
        bug is invisible through it.
        """
        import httpx

        class _Stream(httpx.SyncByteStream):
            def __iter__(self):
                time.sleep(delay)
                yield payload

            def close(self) -> None:
                pass

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(200, stream=_Stream(), headers={"content-type": "image/jpeg"})

        return _Transport()

    def test_body_transfer_is_billed_to_outbound_not_self(self):
        import httpx

        request_telemetry.install_httpx_instrumentation()
        token = request_telemetry._stats.set(RequestStats())
        try:
            with httpx.Client(transport=self._slow_body_transport(0.05)) as client:
                response = client.get("https://connect.dimagi.com/export/opportunity/1/image/")
            assert response.content  # the body really was read inside send()

            stats = current_stats()
            # The headers were instant; every measurable millisecond is the body.
            assert stats.outbound_ms >= 50, "body download must land in outbound_ms"
            # And it is still ONE call: timing moved, counting did not.
            assert stats.outbound_calls == 1
            assert stats.outbound_by_host == {"connect.dimagi.com": 1}
        finally:
            request_telemetry._stats.reset(token)

    def test_the_image_proxy_line_no_longer_reads_as_our_cpu(self, request_obj, caplog):
        """The regression stated as the log line an operator ranks endpoints by."""
        import httpx

        def view_that_proxies_an_image(_request):
            with httpx.Client(transport=self._slow_body_transport(0.05)) as client:
                client.get("https://connect.dimagi.com/export/opportunity/1/image/")
            return "ok"

        caplog.set_level("WARNING")
        with patch.object(request_telemetry, "SLOW_REQUEST_MS", 0):
            _middleware(view_that_proxies_an_image)(request_obj)

        (line,) = _lines(caplog)
        assert line["outbound_ms"] >= 50, "the transfer must be attributed upstream"
        assert line["self_ms"] < line["outbound_ms"], "and must NOT be relabelled as our own CPU"

    def test_streaming_calls_are_left_alone(self):
        """``stream=True`` returns at headers by design — the caller reads the body
        on its own time, so billing that span here would be the mirror-image error.
        """
        import httpx

        request_telemetry.install_httpx_instrumentation()
        token = request_telemetry._stats.set(RequestStats())
        try:
            with httpx.Client(transport=self._slow_body_transport(0.05)) as client:
                with client.stream("GET", "https://connect.dimagi.com/export/opportunity/1/image/") as response:
                    stats_before_read = current_stats().outbound_ms
                    response.read()

            # The body was read by the CALLER, after send() returned, so it is not
            # billed to this call's outbound time.
            assert stats_before_read < 50
            assert current_stats().outbound_calls == 1
        finally:
            request_telemetry._stats.reset(token)

    def test_instrumenting_send_is_idempotent(self):
        """Installed once per worker, but called from a middleware constructor that
        a test suite builds many times — a second wrap would double-count the body.
        """
        import httpx

        request_telemetry.install_httpx_instrumentation()
        request_telemetry.install_httpx_instrumentation()
        request_telemetry.install_httpx_instrumentation()

        token = request_telemetry._stats.set(RequestStats())
        try:
            with httpx.Client(transport=self._slow_body_transport(0.05)) as client:
                client.get("https://connect.dimagi.com/export/opportunity/1/image/")
            # ~50ms of body, billed once. Two wraps would report ~100ms.
            assert 50 <= current_stats().outbound_ms < 100
        finally:
            request_telemetry._stats.reset(token)

    def test_an_unstamped_response_is_billed_to_nobody(self):
        """No headers timestamp means the span is unknown — bill it to nobody rather
        than to the wrong bucket.

        Reachable in real life from a client constructed before instrumentation was
        installed, or from a test double standing in for a response. Asserted against
        ``_body_ms`` directly: patching ``_on_response`` on the module cannot produce
        this state, because a client binds the hook function into its own event_hooks
        list at construction and keeps that reference.
        """

        class Unstamped:
            pass

        assert request_telemetry._body_ms(Unstamped()) == 0.0

        # And the same guard on the shape that actually poisons arithmetic: an
        # attribute that exists but is not a timestamp.
        bogus = Unstamped()
        bogus._labs_telemetry_headers_done = "not-a-float"
        assert request_telemetry._body_ms(bogus) == 0.0


class TestCpuMsSeparatesBurningFromWaiting:
    """The discrimination ``self_ms`` alone cannot make.

    ``self_ms`` is what is left after the measured buckets, so "2.6 s unexplained"
    reads identically whether our Python burned 2.6 s of CPU or the request's thread
    sat descheduled for 2.6 s while three gunicorn workers shared one vCPU. Those two
    have opposite fixes, and #1386 stalled precisely here. ``cpu_ms`` is per-thread
    CPU time, so it answers WHICH.
    """

    @pytest.fixture
    def always_log(self, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)

    def test_burning_cpu_shows_up_as_cpu_ms(self, request_obj, caplog, always_log):
        def spin(r):
            deadline = time.perf_counter() + 0.15
            total = 0
            while time.perf_counter() < deadline:
                total += 1
            return "ok"

        caplog.set_level("WARNING")
        _middleware(spin)(request_obj)

        (line,) = _lines(caplog)
        # Busy-looping for 150ms must be billed as CPU, not merely as elapsed time.
        assert line["cpu_ms"] >= 50, line
        assert line["cpu_ms"] <= line["duration_ms"] + 5

    def test_waiting_is_not_billed_as_cpu(self, request_obj, caplog, always_log):
        """A sleeping request has the SAME self_ms shape as a spinning one.

        This is the whole point: without cpu_ms the two lines are indistinguishable,
        and the wrong one sends you to profile a view that is not running.
        """

        def wait(r):
            time.sleep(0.15)
            return "ok"

        caplog.set_level("WARNING")
        _middleware(wait)(request_obj)

        (line,) = _lines(caplog)
        assert line["duration_ms"] >= 140, line
        # Unattributed wall-clock ...
        assert line["self_ms"] >= 140, line
        # ... but almost no CPU. That gap is the signal.
        assert line["cpu_ms"] < 50, line


class TestUnbiasedSample:
    """Log a fixed fraction regardless of duration, so bands are comparable.

    The threshold-gated stream truncates the distribution differently at every load
    level, which makes any cross-band comparison drawn from it an artefact — real,
    plentiful data, uninterpretable for that question (#1386, twice in one day).
    """

    def test_sampling_is_off_by_default(self, request_obj, caplog):
        """The healthy path must stay silent: this runs on every request."""
        caplog.set_level("WARNING")
        _middleware(lambda r: "ok")(request_obj)
        assert _lines(caplog) == []

    def test_a_fast_clean_request_enters_the_sample(self, request_obj, caplog, monkeypatch):
        """The defining property. A fast request can NEVER appear in the gated
        stream, so if it cannot appear here either, the sample is not unbiased."""
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)

        caplog.set_level("WARNING")
        _middleware(lambda r: "ok")(request_obj)

        (line,) = _lines(caplog)
        assert line["sampled"] is True
        assert line["reason"] == "sample"
        assert line["duration_ms"] < request_telemetry.SLOW_REQUEST_MS

    def test_sampled_and_slow_is_still_findable_by_the_boolean(self, request_obj, caplog, monkeypatch):
        """The query-side trap.

        A sampled request that is also slow reads ``reason: "slow,sample"``, so
        ``filter reason = "sample"`` drops exactly the slow ones and re-introduces
        the bias in the query. ``sampled`` is the field that cannot be fooled.
        """
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)

        caplog.set_level("WARNING")
        _middleware(lambda r: "ok")(request_obj)

        (line,) = _lines(caplog)
        assert line["reason"] == "slow,sample"
        assert line["sampled"] is True

    def test_prefix_scopes_the_sample_to_one_endpoint(self, caplog, monkeypatch):
        """A census of the tier would be a request log; the ALB already is one."""
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)
        monkeypatch.setattr(request_telemetry, "SAMPLE_PATH_PREFIX", "/audit/image/")

        def req(path):
            r = MagicMock()
            r.method, r.path = "GET", path
            r.user.is_authenticated = False
            return r

        caplog.set_level("WARNING")
        _middleware(lambda r: "ok")(req("/audit/image/1/"))
        _middleware(lambda r: "ok")(req("/audit/review/"))

        lines = _lines(caplog)
        assert [line["path"] for line in lines] == ["/audit/image/1/"]

    def test_the_draw_cannot_break_the_request(self, monkeypatch):
        """It runs before the view, outside the middleware's try/finally."""
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)
        monkeypatch.setattr(request_telemetry, "SAMPLE_PATH_PREFIX", "/audit/")

        broken = MagicMock()
        broken.method = "GET"
        type(broken).path = property(lambda self: (_ for _ in ()).throw(RuntimeError("no path")))

        assert _middleware(lambda r: "ok")(broken) == "ok"
