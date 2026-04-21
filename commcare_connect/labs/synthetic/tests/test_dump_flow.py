import json

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from commcare_connect.labs.tests.test_settings import LABS_SETTINGS


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="pw")


@pytest.fixture
def authed_client_dump(client, user, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent-abc"
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {"opportunities": [{"id": 42, "name": "Demo A"}, {"id": 43, "name": "Demo B"}]},
    }
    session["labs_context"] = {"opportunity_id": 42}
    session.save()
    return client


@pytest.fixture
def authed_client_no_context(client, user, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent-abc"
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {"opportunities": [{"id": 42, "name": "Demo A"}, {"id": 43, "name": "Demo B"}]},
    }
    # Deliberately no labs_context key.
    session.save()
    return client


class FakeDrive:
    def __init__(self):
        self.folder_calls = []
        self.uploads = []

    def create_folder(self, name, parent_id):
        self.folder_calls.append((name, parent_id))
        return "new-folder-id"

    def upload_file(self, folder_id, filename, content):
        self.uploads.append((folder_id, filename, content))
        return f"file-{filename}"


def _fake_fetch(data_by_key):
    """Build a replacement for `dump._fetch_endpoint` that looks up by endpoint key."""

    def inner(base_url, opp_id, key, access_token):
        return data_by_key[key]

    return inner


def _collect_events(resp):
    """Parse SSE body into a list of JSON event dicts."""
    events = []
    for chunk in resp.streaming_content:
        for line in chunk.decode().splitlines():
            line = line.strip()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_end_to_end(authed_client_dump, monkeypatch):
    from commcare_connect.labs.synthetic import dump

    fake_drive = FakeDrive()
    monkeypatch.setattr(dump, "DriveClient", lambda: fake_drive)
    monkeypatch.setattr(
        dump,
        "_fetch_endpoint",
        _fake_fetch(
            {
                "": {"id": 42, "name": "Demo A"},
                "user_visits": [{"id": 1}, {"id": 2}],
                "user_data": [{"username": "alice"}],
                "completed_works": [],
                "completed_module": [],
            }
        ),
    )

    resp = authed_client_dump.get(reverse("labs:synthetic:dump_stream"))
    assert resp.status_code == 200
    events = _collect_events(resp)

    kinds = [e.get("data", {}).get("event") for e in events if e.get("data")]
    assert kinds.count("folder") == 1
    assert kinds.count("fetching") == 5
    assert kinds.count("uploaded") == 5
    assert kinds.count("done") == 1

    names = [name for _, name, _ in fake_drive.uploads]
    assert names == [
        "opportunity.json",
        "user_visits.json",
        "user_data.json",
        "completed_works.json",
        "completed_module.json",
    ]

    done = [e for e in events if e.get("data", {}).get("event") == "done"][0]
    assert done["data"]["folder_id"] == "new-folder-id"


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_requires_context_opp(authed_client_no_context, monkeypatch):
    from commcare_connect.labs.synthetic import dump

    monkeypatch.setattr(dump, "DriveClient", lambda: FakeDrive())

    resp = authed_client_no_context.get(reverse("labs:synthetic:dump_stream"))
    events = _collect_events(resp)

    assert any(e.get("error") for e in events), events


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_requires_access_to_context_opp(client, user, settings, monkeypatch):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent-abc"
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {"opportunities": [{"id": 42, "name": "Demo A"}]},
    }
    session["labs_context"] = {"opportunity_id": 99}  # not accessible
    session.save()

    from commcare_connect.labs.synthetic import dump

    monkeypatch.setattr(dump, "DriveClient", lambda: FakeDrive())

    resp = client.get(reverse("labs:synthetic:dump_stream"))
    events = _collect_events(resp)

    assert any("PermissionDenied" in (e.get("error") or "") for e in events), events


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_surfaces_drive_error(authed_client_dump, monkeypatch):
    from commcare_connect.labs.synthetic import dump
    from commcare_connect.labs.synthetic.gdrive import DriveAPIError

    class FailingDrive(FakeDrive):
        def upload_file(self, *a, **kw):
            raise DriveAPIError("quota exceeded")

    monkeypatch.setattr(dump, "DriveClient", lambda: FailingDrive())
    monkeypatch.setattr(
        dump,
        "_fetch_endpoint",
        _fake_fetch({"": {}, "user_visits": [], "user_data": [], "completed_works": [], "completed_module": []}),
    )

    resp = authed_client_dump.get(reverse("labs:synthetic:dump_stream"))
    events = _collect_events(resp)

    assert any("quota exceeded" in (e.get("error") or "") for e in events), events


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_missing_parent_folder_env(authed_client_dump, monkeypatch, settings):
    from commcare_connect.labs.synthetic import dump

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = ""
    monkeypatch.setattr(dump, "DriveClient", lambda: FakeDrive())

    resp = authed_client_dump.get(reverse("labs:synthetic:dump_stream"))
    events = _collect_events(resp)

    assert any("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID" in (e.get("error") or "") for e in events), events


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_surfaces_export_error(authed_client_dump, monkeypatch):
    """If prod Connect raises during a fetch, the dump stream emits the error."""
    import httpx

    from commcare_connect.labs.synthetic import dump

    def failing_fetch(*a, **kw):
        raise httpx.HTTPStatusError("upstream 500 from Connect", request=None, response=None)

    monkeypatch.setattr(dump, "DriveClient", lambda: FakeDrive())
    monkeypatch.setattr(dump, "_fetch_endpoint", failing_fetch)

    resp = authed_client_dump.get(reverse("labs:synthetic:dump_stream"))
    events = _collect_events(resp)

    assert any("HTTPStatusError" in (e.get("error") or "") for e in events), events
    assert any("upstream 500 from Connect" in (e.get("error") or "") for e in events), events


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_dump_stream_uploads_detail_endpoint_as_bare_dict(authed_client_dump, monkeypatch):
    """The `/export/opportunity/<id>/` endpoint returns a bare dict (no `results`
    wrapper). The dump must pass that dict through to upload without trying to
    paginate it as a list."""
    from commcare_connect.labs.synthetic import dump

    fake_drive = FakeDrive()
    monkeypatch.setattr(dump, "DriveClient", lambda: fake_drive)
    monkeypatch.setattr(
        dump,
        "_fetch_endpoint",
        _fake_fetch(
            {
                "": {"id": 42, "name": "Demo A", "organization": "march-demo"},
                "user_visits": [],
                "user_data": [],
                "completed_works": [],
                "completed_module": [],
            }
        ),
    )

    resp = authed_client_dump.get(reverse("labs:synthetic:dump_stream"))
    assert resp.status_code == 200
    # Force generator consumption
    list(_collect_events(resp))

    opp_upload = next(u for u in fake_drive.uploads if u[1] == "opportunity.json")
    uploaded_json = json.loads(opp_upload[2])
    assert isinstance(uploaded_json, dict)
    assert uploaded_json == {"id": 42, "name": "Demo A", "organization": "march-demo"}
