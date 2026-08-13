"""Tests for post_with_retry (connect_labs.labs.ai_review_agents.base).

Shared by the MUAC OverZoom / MUAC Match / Scale Validation agents, all of
which call the same classifier gateway and previously treated a single 429
(rate limited / cold start) as a terminal error with no retry.
"""
from unittest.mock import MagicMock, call

import pytest

from connect_labs.labs.ai_review_agents import base
from connect_labs.labs.ai_review_agents.base import post_with_retry


def _response(status_code, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}  # a real dict -- .get("Retry-After") must return None, not a Mock
    return r


def test_returns_immediately_on_non_429():
    client = MagicMock()
    client.post.return_value = _response(200)

    result = post_with_retry(client, "http://x/classify", json={"a": 1}, backoff_seconds=0)

    assert result.status_code == 200
    client.post.assert_called_once_with("http://x/classify", json={"a": 1})


def test_retries_on_429_then_succeeds():
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(429), _response(200)]

    result = post_with_retry(client, "http://x/classify", json={"a": 1}, max_retries=3, backoff_seconds=0)

    assert result.status_code == 200
    assert client.post.call_count == 3


def test_returns_final_429_after_exhausting_retries():
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(429), _response(429)]

    result = post_with_retry(client, "http://x/classify", json={"a": 1}, max_retries=2, backoff_seconds=0)

    assert result.status_code == 429
    assert client.post.call_count == 3  # initial attempt + 2 retries


def test_every_call_uses_the_same_url_and_payload():
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(200)]

    post_with_retry(client, "http://x/interpret", json={"task": "muac_match"}, backoff_seconds=0)

    assert client.post.call_args_list == [
        call("http://x/interpret", json={"task": "muac_match"}),
        call("http://x/interpret", json={"task": "muac_match"}),
    ]


def test_backoff_grows_linearly_across_attempts(monkeypatch):
    """Pins the advertised schedule (backoff_seconds * (attempt + 1)) -- every
    existing test passes backoff_seconds=0, which would pass unchanged even if
    this formula silently became a constant or exponential."""
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 1.0)  # no jitter for this assertion
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(429), _response(429), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, max_retries=3, backoff_seconds=2.0)

    assert sleeps == [2.0, 4.0, 6.0]


def test_jitter_scales_the_computed_backoff(monkeypatch):
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 0.5)
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=2.0)

    assert sleeps == [1.0]  # 2.0 * 0.5


def test_honors_retry_after_header_over_computed_backoff(monkeypatch):
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 1.0)
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429, headers={"Retry-After": "5"}), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=2.0)

    assert sleeps == [5.0]  # honors the header instead of the 2.0 default


def test_falls_back_to_computed_backoff_on_unparseable_retry_after(monkeypatch):
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 1.0)
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429, headers={"Retry-After": "not-a-number"}), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=2.0)

    assert sleeps == [2.0]


def test_falls_back_to_computed_backoff_on_negative_retry_after(monkeypatch):
    """float("-1") parses without raising, but time.sleep(-1) raises
    ValueError -- a malformed/hostile header must never reach time.sleep()
    unvalidated."""
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 1.0)
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429, headers={"Retry-After": "-1"}), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=2.0)

    assert sleeps == [2.0]


def test_falls_back_to_computed_backoff_on_nan_retry_after(monkeypatch):
    """float("nan") parses without raising, but time.sleep(nan) raises
    ValueError."""
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 1.0)
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429, headers={"Retry-After": "nan"}), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=2.0)

    assert sleeps == [2.0]


def test_falls_back_to_computed_backoff_on_infinite_retry_after(monkeypatch):
    """float("inf") parses without raising, and time.sleep(inf) wouldn't
    raise either -- it would just block forever. Must fall back instead."""
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 1.0)
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post.side_effect = [_response(429, headers={"Retry-After": "inf"}), _response(200)]

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=2.0)

    assert sleeps == [2.0]


def test_logs_a_warning_on_each_retry(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(200)]
    logger = MagicMock()

    post_with_retry(client, "http://x/classify", json={}, backoff_seconds=0, logger=logger)

    logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Per-call timing / outcome record.
#
# This is the only place classifier latency is captured anywhere. Without these
# lines, "why was that run slow?" can only be answered by dividing total
# wall-clock by an ASSUMED pool width, because no per-call duration is recorded.
# ---------------------------------------------------------------------------


def _classifier_lines(logger):
    """The rendered [classifier] records emitted to a mock logger."""
    return [c.args[0] % c.args[1:] for c in logger.info.call_args_list if c.args and "[classifier]" in str(c.args[0])]


def test_logs_one_timing_record_per_successful_call():
    client = MagicMock()
    client.post.return_value = _response(200)
    logger = MagicMock()

    post_with_retry(client, "http://x/predict", json={}, logger=logger, agent_id="scale_validation")

    lines = _classifier_lines(logger)
    assert len(lines) == 1, lines
    assert "agent=scale_validation" in lines[0]
    assert "endpoint=/predict" in lines[0]
    assert "outcome=ok" in lines[0]
    assert "status=200" in lines[0]
    assert "attempts=1" in lines[0]
    assert "elapsed_ms=" in lines[0]


def test_non_2xx_is_recorded_as_a_gateway_error_not_ok():
    """raise_for_status() happens in the caller, so a 500 reaches the caller as
    a normal return -- the timing record must still name it a failure."""
    client = MagicMock()
    client.post.return_value = _response(500)
    logger = MagicMock()

    post_with_retry(client, "http://x/predict", json={}, logger=logger, agent_id="muac_match")

    (line,) = _classifier_lines(logger)
    assert "outcome=gateway_error" in line
    assert "status=500" in line


def test_timeout_is_recorded_and_reraised():
    """The dominant production failure. It must be named a timeout (not lumped
    in with connect failures) and must still propagate to the agent's own
    except block, which is what turns it into a ReviewResult."""
    import httpx

    client = MagicMock()
    client.post.side_effect = httpx.ReadTimeout("The read operation timed out")
    logger = MagicMock()

    with pytest.raises(httpx.ReadTimeout):
        post_with_retry(client, "http://x/predict", json={}, logger=logger, agent_id="scale_validation")

    (line,) = _classifier_lines(logger)
    assert "outcome=timeout" in line
    assert "detail=ReadTimeout" in line
    assert "elapsed_ms=" in line


def test_connect_failure_is_recorded_as_unreachable_not_timeout():
    import httpx

    client = MagicMock()
    client.post.side_effect = httpx.ConnectError("nope")
    logger = MagicMock()

    with pytest.raises(httpx.ConnectError):
        post_with_retry(client, "http://x/predict", json={}, logger=logger, agent_id="scale_validation")

    (line,) = _classifier_lines(logger)
    assert "outcome=unreachable" in line


def test_exhausted_rate_limit_is_recorded_once_with_the_attempt_count(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    client = MagicMock()
    client.post.side_effect = [_response(429), _response(429), _response(429)]
    logger = MagicMock()

    post_with_retry(client, "http://x/predict", json={}, max_retries=2, backoff_seconds=0, logger=logger)

    (line,) = _classifier_lines(logger)
    assert "outcome=rate_limited" in line
    assert "attempts=3" in line


def test_timing_record_is_skipped_when_no_logger_is_passed():
    """post_with_retry is called with logger=None in some paths -- the record
    must not become a required argument."""
    client = MagicMock()
    client.post.return_value = _response(200)

    assert post_with_retry(client, "http://x/predict", json={}).status_code == 200


def test_is_timeout_recognises_the_wrapped_socket_message():
    """The production logs show 'The read operation timed out' surfacing from
    the socket layer, which is not always an httpx.TimeoutException."""
    assert base._is_timeout(OSError("The read operation timed out")) is True
    assert base._is_timeout(OSError("connection refused")) is False
