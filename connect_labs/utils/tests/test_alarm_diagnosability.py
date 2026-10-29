"""The signals that make the next labs alarm one query instead of an investigation.

Each class pins one gap a real incident ended on ("cause unknown, the data was not
there"):

* memory on heavy requests (#1575, 2026-09-09: "peak RSS, not elapsed time");
* streaming bodies measured to the END, so a killed stream is visible (#1575);
* the unbiased sample is not scoped to one path that had no traffic (2026-09-02);
* one requester's share of the tier (five incidents, Sep 1 -> Oct 29 / #2769), with
  staff named and everyone else hashed.
"""

import asyncio
import json
import logging
from unittest.mock import MagicMock

import pytest
from django.http import StreamingHttpResponse

from connect_labs.utils import request_telemetry, requester_concentration
from connect_labs.utils.request_telemetry import RequestTelemetryMiddleware, StreamTrace

TELEMETRY = "connect_labs.telemetry.request"
EXTRA = "connect_labs.telemetry.metrics.extra"
REQUESTERS = "connect_labs.telemetry.metrics.requesters"


@pytest.fixture
def cap(caplog):
    loggers = [logging.getLogger(n) for n in (TELEMETRY, "connect_labs.telemetry.metrics")]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(caplog.handler)
        lg.setLevel(logging.INFO)
    caplog.set_level(logging.INFO)

    def lines(name):
        return [json.loads(r.message) for r in caplog.records if r.name == name]

    yield lines
    for lg, lvl in zip(loggers, previous):
        lg.removeHandler(caplog.handler)
        lg.setLevel(lvl)


def _req(path="/labs/workflow/7/", route="labs/workflow/<int:workflow_id>/", user=None, ua="Mozilla/5.0"):
    r = MagicMock()
    r.method, r.path = "GET", path
    r.resolver_match.route = route
    r.META = {"HTTP_USER_AGENT": ua}
    if user is None:
        user = MagicMock(is_authenticated=False)
    r.user = user
    return r


def _user(username, email, is_staff=False):
    return MagicMock(is_authenticated=True, username=username, email=email, is_staff=is_staff)


class TestHeavyRequestsCarryMemory:
    def test_slow_line_has_rss_fields(self, cap, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)
        RequestTelemetryMiddleware(lambda r: "ok")(_req())
        (line,) = cap(TELEMETRY)
        for key in ("rss_kb", "rss_delta_kb", "maxrss_kb", "maxrss_rise_kb"):
            assert key in line
        # ru_maxrss exists on every POSIX host the suite runs on.
        assert isinstance(line["maxrss_kb"], int)
        assert line["streaming"] is False

    def test_memory_metric_is_route_only_and_only_for_threshold_lines(self, cap, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)
        RequestTelemetryMiddleware(lambda r: "ok")(_req(path="/labs/workflow/4242/"))
        lines = cap(EXTRA)
        assert lines, "a slow request must emit the heavy-request memory metric"
        (line,) = lines
        (directive,) = line["_aws"]["CloudWatchMetrics"]
        assert directive["Namespace"] == "Labs/Requests"
        assert directive["Dimensions"] == [["route"]]
        assert set(line) - {"_aws"} <= {"route", *request_telemetry.HEAVY_MEMORY_METRICS}
        assert "4242" not in json.dumps(line)

    def test_a_sampled_fast_request_does_not_mint_memory_metrics(self, cap, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)
        monkeypatch.setattr(request_telemetry, "SAMPLE_PATH_PREFIXES", ())
        RequestTelemetryMiddleware(lambda r: "ok")(_req())
        assert cap(TELEMETRY)[0]["reason"] == "sample"
        assert cap(EXTRA) == []

    def test_memory_probe_tolerates_missing_proc(self, monkeypatch):
        monkeypatch.setattr(request_telemetry, "current_rss_kb", lambda: None)
        probe = request_telemetry.MemoryProbe()
        fields = probe.fields()
        assert fields["rss_kb"] is None and fields["rss_delta_kb"] is None


def _streaming(parts, is_async):
    if is_async:

        async def gen():
            for p in parts:
                yield p

        return StreamingHttpResponse(gen(), content_type="text/event-stream")
    return StreamingHttpResponse(iter(parts), content_type="text/event-stream")


def _drain(response):
    if response.is_async:

        async def consume():
            return [p async for p in response.streaming_content]

        return asyncio.run(consume())
    return list(response.streaming_content)


class TestStreamsAreMeasuredToTheEnd:
    @pytest.mark.parametrize("is_async", [False, True])
    def test_body_is_unchanged_and_end_is_recorded(self, cap, monkeypatch, is_async):
        monkeypatch.setattr(request_telemetry, "SLOW_REQUEST_MS", 0)
        parts = [b"data: 1\n\n", b"data: 2\n\n", b"data: 3\n\n"]
        resp = RequestTelemetryMiddleware(lambda r: _streaming(parts, is_async))(
            _req(
                path="/labs/workflow/api/9/pipeline-data/stream/",
                route="labs/workflow/api/<int:definition_id>/pipeline-data/stream/",
            )
        )
        assert resp.is_async is is_async
        assert _drain(resp) == parts

        ends = [ln for ln in cap(TELEMETRY) if ln["event"] == "stream_end"]
        (end,) = ends
        assert end["outcome"] == "complete"
        assert end["chunks"] == 3 and end["bytes"] == sum(map(len, parts))
        assert end["route"] == "labs/workflow/api/<int:definition_id>/pipeline-data/stream/"
        assert "rss_peak_kb" in end and "maxrss_rise_kb" in end

        metrics = [m for ln in cap(EXTRA) for m in ln["_aws"]["CloudWatchMetrics"] if m["Namespace"] == "Labs/Streams"]
        names = {x["Name"] for m in metrics for x in m["Metrics"]}
        assert {"StreamStarted", "StreamFinished", "StreamDuration"} <= names
        assert all(m["Dimensions"] == [["route"]] for m in metrics)

    def test_started_without_finished_is_what_a_killed_stream_looks_like(self, cap):
        RequestTelemetryMiddleware(lambda r: _streaming([b"x"], False))(_req())
        names = [x["Name"] for ln in cap(EXTRA) for m in ln["_aws"]["CloudWatchMetrics"] for x in m["Metrics"]]
        assert names == ["StreamStarted"]  # the body never ran

    @pytest.mark.django_db
    def test_a_closed_stream_is_logged_even_when_fast(self, cap):
        resp = RequestTelemetryMiddleware(lambda r: _streaming([b"a", b"b"], False))(_req())
        it = iter(resp)
        next(it)
        resp.close()  # what the handler does when the client goes away
        (end,) = (ln for ln in cap(TELEMETRY) if ln["event"] == "stream_end")
        assert end["outcome"] == "closed"

    def test_fast_complete_stream_writes_no_line(self, cap):
        resp = RequestTelemetryMiddleware(lambda r: _streaming([b"a"], False))(_req())
        _drain(resp)
        assert [ln for ln in cap(TELEMETRY) if ln.get("event") == "stream_end"] == []


class TestPipelineStreamTrace:
    def test_start_steps_end_carry_counts_and_memory_only(self, cap):
        trace = StreamTrace("pipeline_stream", definition_id=5476)
        trace.step(rows=608, pipeline_id=5108, target=1, targets=2, from_cache=True, opp_name="not-an-int")
        trace.step(rows=2189, pipeline_id=5108, target=2, targets=2, from_cache=False)
        trace.end()
        lines = cap(TELEMETRY)
        assert [ln["phase"] for ln in lines] == ["start", "step", "step", "end"]
        assert len({ln["stream_id"] for ln in lines}) == 1
        assert lines[-1]["total_rows"] == 608 + 2189
        assert lines[-1]["outcome"] == "complete"
        assert all("rss_kb" in ln and ln["definition_id"] == 5476 for ln in lines)
        assert "opp_name" not in lines[1]  # non-numeric fields are dropped


class TestSampleIsNotOnePath:
    def test_empty_prefix_samples_everything_but_excludes(self, cap, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)
        monkeypatch.setattr(request_telemetry, "SAMPLE_PATH_PREFIXES", ())
        monkeypatch.setattr(request_telemetry, "SAMPLE_EXCLUDE_PREFIXES", ("/health/",))
        for path in ("/labs/workflow/1/", "/audit/api/2/bulk-data/", "/health/"):
            RequestTelemetryMiddleware(lambda r: "ok")(_req(path=path))
        assert [ln["path"] for ln in cap(TELEMETRY)] == ["/labs/workflow/1/", "/audit/api/2/bulk-data/"]

    def test_unresolved_paths_leave_the_sample(self, cap, monkeypatch):
        monkeypatch.setattr(request_telemetry, "SAMPLE_RATE", 1.0)
        monkeypatch.setattr(request_telemetry, "SAMPLE_PATH_PREFIXES", ())
        scanner = _req(path="/wp-admin/setup.php")
        scanner.resolver_match = None
        RequestTelemetryMiddleware(lambda r: "ok")(scanner)
        assert cap(TELEMETRY) == []

    def test_comma_separated_prefixes(self):
        assert request_telemetry._prefixes(" /audit/, /labs/workflow/ ,") == ("/audit/", "/labs/workflow/")
        assert request_telemetry._prefixes("") == ()


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(requester_concentration, "ENABLED", True)
    monkeypatch.setattr(requester_concentration, "WINDOW_S", 3600)
    monkeypatch.setattr(requester_concentration, "_window", requester_concentration._Window())

    def flush():
        monkeypatch.setattr(requester_concentration, "WINDOW_S", 0)
        requester_concentration.observe(_req(), "labs/overview/")

    return flush


class TestRequesterConcentration:
    def test_one_staff_script_dominating_is_one_line(self, cap, window, monkeypatch):
        monkeypatch.setattr(requester_concentration, "LOG_MIN_REQUESTS", 5)
        ace = _user("ace", "ace@dimagi-ai.com")
        for _ in range(19):
            requester_concentration.observe(
                _req(user=ace, ua="python-httpx/0.27"), "labs/workflow/api/job/<str:task_id>/status.json"
            )
        requester_concentration.observe(_req(user=_user("kp", "kp@dimagi.com")), "labs/workflow/")
        window()

        (line,) = (ln for ln in cap(TELEMETRY) if ln["event"] == "top_requester")
        assert line["top_requester"] == "ace" and line["top_requester_kind"] == "staff"
        assert line["top_user_agent_class"] == "python"
        assert line["top_share_pct"] == 95.0 and line["requests"] == 20
        assert line["top_route"] == "labs/workflow/api/job/<str:task_id>/status.json"
        assert line["anonymous_requests"] == 1  # the flushing request

        (emf,) = cap(REQUESTERS)
        (directive,) = emf["_aws"]["CloudWatchMetrics"]
        assert directive["Dimensions"] == [[]]  # no identity dimension, ever
        assert set(emf) == {"_aws", *requester_concentration.METRIC_NAMES}
        assert emf["TopRequesterShare"] == 95.0

    def test_non_staff_is_a_stable_hash_never_the_username(self, cap, window, monkeypatch):
        monkeypatch.setattr(requester_concentration, "LOG_MIN_REQUESTS", 1)
        flw = _user("worker.maria", "maria@partner.org")
        for _ in range(3):
            requester_concentration.observe(_req(user=flw), "audit/api/<int:session_id>/bulk-data/")
        window()
        (line,) = (ln for ln in cap(TELEMETRY) if ln["event"] == "top_requester")
        assert line["top_requester_kind"] == "hashed"
        assert line["top_requester"].startswith("u:") and len(line["top_requester"]) == 14
        raw = json.dumps(cap(TELEMETRY)) + json.dumps(cap(REQUESTERS))
        assert "maria" not in raw and "partner.org" not in raw
        assert requester_concentration.requester_label(flw)[0] == line["top_requester"]

    def test_django_is_staff_counts_as_staff(self):
        assert requester_concentration.requester_label(_user("ops", "ops@example.org", is_staff=True)) == (
            "ops",
            "staff",
        )

    def test_health_checks_are_not_requesters(self, cap, window):
        for _ in range(50):
            requester_concentration.observe(_req(), "health/")
        window()
        assert cap(REQUESTERS) == []  # only the anonymous flusher: nothing to report

    def test_below_the_log_floor_only_the_metric(self, cap, window):
        requester_concentration.observe(_req(user=_user("ace", "ace@dimagi-ai.com")), "labs/overview/")
        window()
        assert cap(REQUESTERS) and not [ln for ln in cap(TELEMETRY) if ln["event"] == "top_requester"]

    @pytest.mark.parametrize(
        "ua,cls",
        [
            ("Mozilla/5.0 (Macintosh)", "browser"),
            ("curl/8.4.0", "curl"),
            ("python-requests/2.31", "python"),
            ("claude-code/2.0 mcp", "agent"),
            ("node-fetch", "node"),
            ("", "none"),
            ("Go-http-client/1.1", "other"),
        ],
    )
    def test_user_agent_class(self, ua, cls):
        assert requester_concentration.user_agent_class(ua) == cls

    def test_observe_never_raises(self):
        broken = MagicMock()
        type(broken).user = property(lambda self: (_ for _ in ()).throw(RuntimeError("x")))
        requester_concentration.observe(broken, "labs/overview/")
