"""Tests for post_with_retry (connect_labs.labs.ai_review_agents.base).

Shared by the MUAC OverZoom / MUAC Match / Scale Validation agents, all of
which call the same classifier gateway and previously treated a single 429
(rate limited / cold start) as a terminal error with no retry.
"""
from unittest.mock import MagicMock, call

from connect_labs.labs.ai_review_agents.base import post_with_retry


def _response(status_code):
    r = MagicMock()
    r.status_code = status_code
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
