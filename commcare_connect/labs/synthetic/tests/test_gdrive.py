import json

import pytest

from commcare_connect.labs.synthetic import gdrive


class FakeCredentials:
    def __init__(self):
        self.token = "fake-access-token"

    def refresh(self, _request):
        pass


@pytest.fixture
def fake_creds(monkeypatch):
    monkeypatch.setattr(gdrive, "_load_credentials", lambda: FakeCredentials())


def test_list_folder_returns_name_to_id_map(httpx_mock, fake_creds):
    url = (
        "https://www.googleapis.com/drive/v3/files"
        "?q=%27folder-abc%27+in+parents+and+trashed+%3D+false"
        "&fields=files%28id%2Cname%29&pageSize=1000"
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
        url="https://www.googleapis.com/drive/v3/files/f1?alt=media",
        content=payload,
    )

    client = gdrive.DriveClient()
    assert client.download_file("f1") == payload


def test_list_folder_sends_bearer_token(httpx_mock, fake_creds):
    url = (
        "https://www.googleapis.com/drive/v3/files"
        "?q=%27f%27+in+parents+and+trashed+%3D+false"
        "&fields=files%28id%2Cname%29&pageSize=1000"
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


def test_create_folder_posts_mimetype_folder(httpx_mock, fake_creds):
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/drive/v3/files",
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
    httpx_mock.add_response(
        method="POST",
        url="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        json={"id": "file-new"},
    )

    client = gdrive.DriveClient()
    content = b'[{"id": 1}]'
    file_id = client.upload_file("folder-abc", "user_visits.json", content)

    assert file_id == "file-new"
    request = httpx_mock.get_request()
    ct = request.headers["Content-Type"]
    assert ct.startswith("multipart/related; boundary=")
    raw = request.content
    # Metadata part references folder and filename.
    assert b'"name": "user_visits.json"' in raw
    assert b'"parents": ["folder-abc"]' in raw
    # File bytes are embedded verbatim.
    assert content in raw
