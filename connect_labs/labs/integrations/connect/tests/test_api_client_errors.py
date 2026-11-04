"""Tests for LabsRecordAPIClient error capture.

These tests exist because debugging write failures from MCP clients (e.g. ACE)
was impossible: the client wrapper logged upstream detail server-side and
re-raised a generic message, which the MCP transport then collapsed into
"Internal error" with no detail. Capturing status_code + body on LabsAPIError
is the first half of the fix; surfacing them in the JSON-RPC envelope is the
second half (see test_transport_internal_error_envelope).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from connect_labs.labs.integrations.connect.api_client import LabsAPIError, LabsRecordAPIClient


def _mock_response(status_code: int, text: str) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"{status_code} error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.mark.parametrize("status_code,body", [(400, "bad payload"), (403, "forbidden"), (500, "boom")])
def test_create_record_captures_status_and_body_on_http_error(status_code, body):
    client = LabsRecordAPIClient(access_token="t", program_id=22)
    with patch.object(client.http_client, "post", return_value=_mock_response(status_code, body)):
        with pytest.raises(LabsAPIError) as excinfo:
            client.create_record(experiment="22", type="solicitation", data={"k": "v"})

    err = excinfo.value
    assert err.status_code == status_code
    assert err.body == body


def test_create_record_truncates_oversized_body():
    """Bodies larger than 2000 chars should be truncated to keep error envelopes small."""
    huge_body = "x" * 5000
    client = LabsRecordAPIClient(access_token="t", program_id=22)
    with patch.object(client.http_client, "post", return_value=_mock_response(500, huge_body)):
        with pytest.raises(LabsAPIError) as excinfo:
            client.create_record(experiment="22", type="solicitation", data={"k": "v"})

    assert len(excinfo.value.body) == 2000


def test_create_record_network_error_carries_no_status_or_body():
    """Pure network errors (no HTTP response) must not crash the new error capture."""
    client = LabsRecordAPIClient(access_token="t", program_id=22)
    with patch.object(client.http_client, "post", side_effect=httpx.ConnectError("network down")):
        with pytest.raises(LabsAPIError) as excinfo:
            client.create_record(experiment="22", type="solicitation", data={"k": "v"})

    assert excinfo.value.status_code is None
    assert excinfo.value.body is None


class TestAReadOfOneRecord:
    CLIENT_LOG = "connect_labs.labs.integrations.connect.api_client"

    def _errors(self, caplog):
        return [r for r in caplog.records if r.name == self.CLIENT_LOG and r.levelname == "ERROR"]

    def _read(self, client, status_code):
        with patch.object(client.http_client, "get", return_value=_mock_response(status_code, "no")):
            with pytest.raises(LabsAPIError) as excinfo:
                client.get_record_by_id(27307, experiment="workflow", type="workflow_definition")
        assert excinfo.value.status_code == status_code

    def test_not_found_is_an_answer_not_an_error(self, caplog):
        """A follower reads its template in the template's scope, then as a public record.
        Everyone outside that scope logged a traceback on every poll of every report."""
        with caplog.at_level("INFO", logger=self.CLIENT_LOG):
            self._read(LabsRecordAPIClient(access_token="t", opportunity_id=22), 404)
        assert self._errors(caplog) == []
        assert any("27307" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("status_code", [403, 500])
    def test_anything_else_is_still_an_error(self, caplog, status_code):
        self._read(LabsRecordAPIClient(access_token="t", opportunity_id=22), status_code)
        assert self._errors(caplog)
