from unittest.mock import Mock

import httpx
import pytest
from django.test import override_settings

from connect_labs.labs.integrations.duplicate_detection.api_client import (
    DuplicateDetectionClient,
    DuplicateDetectionError,
)


def _client_with_mock_http(response=None, side_effect=None):
    client = DuplicateDetectionClient()
    mock_http = Mock()
    if side_effect is not None:
        mock_http.post.side_effect = side_effect
    else:
        mock_http.post.return_value = response
    client._client = mock_http
    return client


def _ok_response(payload):
    resp = Mock()
    resp.status_code = 200
    resp.raise_for_status = Mock(return_value=None)
    resp.json = Mock(return_value=payload)
    return resp


@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_detect_duplicates_posts_images_and_returns_groups():
    client = _client_with_mock_http(_ok_response({"groups": [["a", "b"]]}))
    result = client.detect_duplicates([{"id": "a", "url": "https://x/a"}, {"id": "b", "url": "https://x/b"}])
    assert result == {"groups": [["a", "b"]]}
    client.http_client.post.assert_called_once_with(
        "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/detect_duplicates",
        json={"images": [{"id": "a", "url": "https://x/a"}, {"id": "b", "url": "https://x/b"}]},
    )


@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_detect_duplicates_empty_images_short_circuits_without_a_call():
    client = _client_with_mock_http(_ok_response({"groups": []}))
    result = client.detect_duplicates([])
    assert result == {"groups": []}
    client.http_client.post.assert_not_called()


@override_settings(SCALE_VALIDATION_API_KEY="")
def test_detect_duplicates_raises_when_api_key_missing():
    client = DuplicateDetectionClient()
    with pytest.raises(DuplicateDetectionError, match="SCALE_VALIDATION_API_KEY"):
        client.detect_duplicates([{"id": "a", "url": "https://x/a"}])


@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_detect_duplicates_raises_on_rate_limit():
    resp = Mock()
    resp.status_code = 429
    client = _client_with_mock_http(resp)
    with pytest.raises(DuplicateDetectionError, match="Rate limited"):
        client.detect_duplicates([{"id": "a", "url": "https://x/a"}])


@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_detect_duplicates_raises_duplicate_detection_error_on_http_error():
    request = httpx.Request("POST", "https://x/detect_duplicates")
    response = httpx.Response(500, request=request, json={"details": "boom"})
    err = httpx.HTTPStatusError("500", request=request, response=response)
    client = _client_with_mock_http(side_effect=err)
    with pytest.raises(DuplicateDetectionError, match="boom"):
        client.detect_duplicates([{"id": "a", "url": "https://x/a"}])


@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_detect_duplicates_raises_duplicate_detection_error_on_connection_error():
    client = _client_with_mock_http(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(DuplicateDetectionError, match="Connection error"):
        client.detect_duplicates([{"id": "a", "url": "https://x/a"}])
