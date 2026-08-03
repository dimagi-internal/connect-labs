"""Unit tests for AuditDataAccess.get_attachment_signed_url.

Mirrors the bare-instance style of test_image_download_retry.py. The real
/export/opportunity/<id>/attachment_signed_url/ endpoint (commcare-connect
PR #1415) isn't deployed to prod yet, so these mock the HTTP layer entirely.
"""

from unittest.mock import Mock

import httpx

from connect_labs.audit.data_access import AuditDataAccess


def _make_data_access():
    da = object.__new__(AuditDataAccess)
    da.http_client = Mock()
    da.production_url = "https://connect.example"
    return da


def test_returns_signed_url_on_success():
    da = _make_data_access()
    resp = Mock()
    resp.raise_for_status = Mock(return_value=None)
    resp.json = Mock(return_value={"attachment_signed_url": "https://signed.example/blob1"})
    da.http_client.get.return_value = resp

    result = da.get_attachment_signed_url("blob1", 1973)

    assert result == "https://signed.example/blob1"
    da.http_client.get.assert_called_once_with(
        "https://connect.example/export/opportunity/1973/attachment_signed_url/",
        params={"blob_id": "blob1"},
    )


def test_returns_none_on_http_status_error():
    da = _make_data_access()
    request = httpx.Request("GET", "https://connect.example/export/opportunity/1973/attachment_signed_url/")
    response = httpx.Response(404, request=request)
    resp = Mock()
    resp.raise_for_status = Mock(side_effect=httpx.HTTPStatusError("404", request=request, response=response))
    da.http_client.get.return_value = resp

    assert da.get_attachment_signed_url("blob1", 1973) is None


def test_returns_none_on_connection_error():
    da = _make_data_access()
    da.http_client.get.side_effect = httpx.ConnectError("boom")

    assert da.get_attachment_signed_url("blob1", 1973) is None
