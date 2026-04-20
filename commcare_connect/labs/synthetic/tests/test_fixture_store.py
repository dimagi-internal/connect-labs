import json

import pytest

from commcare_connect.labs.synthetic.fixture_store import ENDPOINT_FILES, FixtureStore


class FakeDrive:
    def __init__(self, folders: dict[str, dict[str, bytes]]):
        """folders: {folder_id: {filename: raw_bytes}}"""
        self._folders = folders
        self.list_calls = 0
        self.download_calls = 0

    def list_folder(self, folder_id: str) -> dict[str, str]:
        self.list_calls += 1
        files = self._folders.get(folder_id, {})
        return {name: f"{folder_id}/{name}" for name in files}

    def download_file(self, file_id: str) -> bytes:
        self.download_calls += 1
        folder_id, name = file_id.split("/", 1)
        return self._folders[folder_id][name]


def _store_with(opp_id, folder_id, files):
    drive = FakeDrive({folder_id: files})
    folder_lookup = {opp_id: folder_id}
    return FixtureStore(drive=drive, folder_lookup=folder_lookup.get), drive


def test_loads_list_endpoint():
    store, _ = _store_with(42, "folder-a", {"user_visits.json": json.dumps([{"id": 1}, {"id": 2}]).encode()})
    assert store.load_endpoint(42, "user_visits") == [{"id": 1}, {"id": 2}]


def test_loads_opportunity_detail_as_dict():
    store, _ = _store_with(42, "folder-a", {"opportunity.json": json.dumps({"id": 42, "name": "demo"}).encode()})
    assert store.load_endpoint(42, "") == {"id": 42, "name": "demo"}


def test_missing_file_returns_empty_list(caplog):
    store, _ = _store_with(42, "folder-a", {})
    assert store.load_endpoint(42, "user_visits") == []
    assert "missing fixture file" in caplog.text.lower()


def test_unknown_endpoint_returns_empty_list(caplog):
    store, _ = _store_with(42, "folder-a", {"user_visits.json": b"[]"})
    assert store.load_endpoint(42, "bogus") == []
    assert "unknown endpoint" in caplog.text.lower()


def test_cache_avoids_repeat_downloads():
    store, drive = _store_with(42, "folder-a", {"user_visits.json": b"[]"})
    store.load_endpoint(42, "user_visits")
    store.load_endpoint(42, "user_visits")
    assert drive.download_calls == 1


def test_reload_purges_cache():
    store, drive = _store_with(42, "folder-a", {"user_visits.json": b"[]"})
    store.load_endpoint(42, "user_visits")
    store.reload(42)
    store.load_endpoint(42, "user_visits")
    assert drive.download_calls == 2


def test_missing_folder_lookup_raises():
    store = FixtureStore(drive=FakeDrive({}), folder_lookup=lambda _: None)
    with pytest.raises(ValueError):
        store.load_endpoint(42, "user_visits")


def test_endpoint_files_covers_all_supported_endpoints():
    assert ENDPOINT_FILES == {
        "": "opportunity.json",
        "user_visits": "user_visits.json",
        "user_data": "user_data.json",
        "completed_works": "completed_works.json",
        "completed_module": "completed_module.json",
    }
