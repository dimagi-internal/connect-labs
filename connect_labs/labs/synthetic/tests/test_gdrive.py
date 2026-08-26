import json

import pytest

from connect_labs.labs.synthetic import gdrive


class FakeCredentials:
    def __init__(self, valid=True):
        self.token = "fake-access-token"
        self.valid = valid
        self.refresh_count = 0

    def refresh(self, _request):
        self.refresh_count += 1
        self.valid = True


@pytest.fixture
def fake_creds(monkeypatch):
    monkeypatch.setattr(gdrive, "_load_credentials", lambda: FakeCredentials())


def test_list_folder_returns_name_to_id_map(httpx_mock, fake_creds):
    url = (
        "https://www.googleapis.com/drive/v3/files"
        "?q=%27folder-abc%27+in+parents+and+trashed+%3D+false"
        "&fields=files%28id%2Cname%29&pageSize=1000"
        "&includeItemsFromAllDrives=true&corpora=allDrives&supportsAllDrives=true"
    )
    httpx_mock.add_response(
        url=url,
        json={
            "files": [
                {"id": "f1", "name": "user_visits.json"},
                {"id": "f2", "name": "opportunity.json"},
            ]
        },
    )

    client = gdrive.DriveClient()
    result = client.list_folder("folder-abc")

    assert result == {"user_visits.json": "f1", "opportunity.json": "f2"}


def test_download_file_returns_bytes(httpx_mock, fake_creds):
    payload = json.dumps([{"id": 1}]).encode()
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?alt=media&supportsAllDrives=true",
        content=payload,
    )

    client = gdrive.DriveClient()
    assert client.download_file("f1") == payload


def test_list_folder_sends_bearer_token(httpx_mock, fake_creds):
    url = (
        "https://www.googleapis.com/drive/v3/files"
        "?q=%27f%27+in+parents+and+trashed+%3D+false"
        "&fields=files%28id%2Cname%29&pageSize=1000"
        "&includeItemsFromAllDrives=true&corpora=allDrives&supportsAllDrives=true"
    )
    httpx_mock.add_response(
        url=url,
        json={"files": []},
    )

    client = gdrive.DriveClient()
    client.list_folder("f")

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer fake-access-token"


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.setattr(gdrive, "_load_credentials", lambda: None)

    with pytest.raises(gdrive.DriveAuthError):
        gdrive.DriveClient()


def test_credentials_refresh_only_when_expired(httpx_mock, monkeypatch):
    """Token refresh hits Google's OAuth endpoint; refresh lazily so a
    multi-op dump doesn't pay that cost per Drive call."""
    creds = FakeCredentials(valid=True)
    monkeypatch.setattr(gdrive, "_load_credentials", lambda: creds)

    # Seed two list_folder responses.
    for _ in range(2):
        httpx_mock.add_response(
            method="GET",
            url="https://www.googleapis.com/drive/v3/files"
            "?q=%27f%27+in+parents+and+trashed+%3D+false"
            "&fields=files%28id%2Cname%29&pageSize=1000"
            "&includeItemsFromAllDrives=true&corpora=allDrives&supportsAllDrives=true",
            json={"files": []},
        )

    client = gdrive.DriveClient()
    client.list_folder("f")
    client.list_folder("f")
    assert creds.refresh_count == 0, "should not refresh while token is valid"

    # Simulate expiry; next call should refresh exactly once.
    creds.valid = False
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/drive/v3/files"
        "?q=%27f%27+in+parents+and+trashed+%3D+false"
        "&fields=files%28id%2Cname%29&pageSize=1000"
        "&includeItemsFromAllDrives=true&corpora=allDrives&supportsAllDrives=true",
        json={"files": []},
    )
    client.list_folder("f")
    assert creds.refresh_count == 1


def test_create_folder_posts_mimetype_folder(httpx_mock, fake_creds):
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/drive/v3/files?supportsAllDrives=true",
        json={"id": "folder-new"},
    )

    client = gdrive.DriveClient()
    folder_id = client.create_folder("opp-42-demo", parent_id="parent-abc")

    assert folder_id == "folder-new"
    request = httpx_mock.get_request()
    body = json.loads(request.content)
    assert body["name"] == "opp-42-demo"
    assert body["mimeType"] == "application/vnd.google-apps.folder"
    assert body["parents"] == ["parent-abc"]


def test_upload_file_multipart_body(httpx_mock, fake_creds):
    content = b'[{"id": 1}]'
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
        json={"id": "file-new"},
    )
    # upload_file verifies stored size after upload.
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/drive/v3/files/file-new?fields=size&supportsAllDrives=true",
        json={"size": str(len(content))},
    )

    client = gdrive.DriveClient()
    file_id = client.upload_file("folder-abc", "user_visits.json", content)

    assert file_id == "file-new"
    requests = httpx_mock.get_requests()
    # POST = upload, GET = size verify
    post = [r for r in requests if r.method == "POST"][0]
    ct = post.headers["Content-Type"]
    assert ct.startswith("multipart/related; boundary=")
    raw = post.content
    # Metadata part references folder and filename.
    assert b'"name": "user_visits.json"' in raw
    assert b'"parents": ["folder-abc"]' in raw
    # File bytes are embedded verbatim.
    assert content in raw


def test_upload_file_raises_on_stored_size_mismatch(httpx_mock, fake_creds):
    """Drive occasionally returns 200 for a multipart upload but stores 0 bytes.
    Verify we detect that and raise rather than silently returning an empty file.
    """
    content = b'[{"id": 1, "name": "real data"}]'
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
        json={"id": "file-truncated"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/drive/v3/files/file-truncated?fields=size&supportsAllDrives=true",
        json={"size": "0"},
    )

    client = gdrive.DriveClient()
    with pytest.raises(gdrive.DriveAPIError, match="stored size mismatch"):
        client.upload_file("folder-abc", "user_visits.json", content)


def test_all_requests_include_shared_drive_params(httpx_mock, fake_creds):
    """All four Drive operations must include supportsAllDrives=true so the SA
    can operate on folders inside Shared Drives — the common Dimagi setup."""
    # Seed responses for each operation.
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/drive/v3/files"
        "?q=%27f%27+in+parents+and+trashed+%3D+false"
        "&fields=files%28id%2Cname%29&pageSize=1000"
        "&includeItemsFromAllDrives=true&corpora=allDrives&supportsAllDrives=true",
        json={"files": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/drive/v3/files/abc?alt=media&supportsAllDrives=true",
        content=b"{}",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/drive/v3/files?supportsAllDrives=true",
        json={"id": "new-folder"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
        json={"id": "new-file"},
    )
    # upload_file post-verification
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/drive/v3/files/new-file?fields=size&supportsAllDrives=true",
        json={"size": "2"},  # matches len(b"[]")
    )

    client = gdrive.DriveClient()
    client.list_folder("f")
    client.download_file("abc")
    client.create_folder("new", parent_id="p")
    client.upload_file("p", "x.json", b"[]")

    for req in httpx_mock.get_requests():
        assert "supportsAllDrives=true" in str(req.url), f"missing supportsAllDrives on {req.url}"


# ---------------------------------------------------------------------------
# Per-request telemetry
# ---------------------------------------------------------------------------


def test_drive_reads_are_counted_against_the_in_flight_request(httpx_mock, fake_creds):
    """Drive reads must show up as outbound calls.

    They did not before 2026-08-26: `DriveClient` uses the module-level
    `httpx.get`, which takes no `event_hooks`, so a workflow run page making 22
    Drive round-trips logged `outbound_calls: 0` and charged all ~15s to
    `self_ms`. The performance runbook reads a dominant `self_ms` as "the time is
    in our own Python" and says not to look at the dependency — so the telemetry
    did not merely miss the cause, it pointed away from it.
    """
    from connect_labs.utils import request_telemetry
    from connect_labs.utils.request_telemetry import RequestStats, current_stats

    httpx_mock.add_response(
        url=(
            "https://www.googleapis.com/drive/v3/files"
            "?q=%27folder-abc%27+in+parents+and+trashed+%3D+false"
            "&fields=files%28id%2Cname%29&pageSize=1000"
            "&includeItemsFromAllDrives=true&corpora=allDrives&supportsAllDrives=true"
        ),
        json={"files": [{"id": "file-1", "name": "user_data.json"}]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/file-1?alt=media&supportsAllDrives=true",
        content=b"[]",
    )

    token = request_telemetry._stats.set(RequestStats())
    try:
        client = gdrive.DriveClient()
        client.list_folder("folder-abc")
        client.download_file("file-1")

        stats = current_stats()
        assert stats.outbound_calls == 2
        assert stats.outbound_by_host["www.googleapis.com"] == 2
    finally:
        request_telemetry._stats.reset(token)


def test_drive_telemetry_never_breaks_the_call_it_measures(httpx_mock, fake_creds, monkeypatch):
    """Outside a request there are no stats to record against, and a broken
    recorder must not take the Drive read down with it.

    The invariant is unchanged; only its location moved. #1300 protected it in
    ``DriveClient._timed_get``, which counted each Drive read by hand. #1298 closed
    the same blind spot at the source for every httpx caller, so the counting -- and
    the guard around it -- now lives in the shared response hook. Break the recorder
    there and the Drive read must still return its bytes.
    """
    from connect_labs.utils import request_telemetry

    request_telemetry.install_httpx_instrumentation()
    monkeypatch.setattr(
        request_telemetry,
        "record_outbound_call",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("telemetry exploded")),
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/file-1?alt=media&supportsAllDrives=true",
        content=b"payload",
    )

    client = gdrive.DriveClient()
    assert client.download_file("file-1") == b"payload"
