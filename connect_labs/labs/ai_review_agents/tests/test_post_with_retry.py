"""Tests for post_with_retry (connect_labs.labs.ai_review_agents.base).

Shared by the MUAC OverZoom / MUAC Match / Scale Validation agents, all of
which call the same classifier gateway and previously treated a single 429
(rate limited / cold start) as a terminal error with no retry.
"""
from unittest.mock import MagicMock, call

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
