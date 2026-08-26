import json

from connect_labs.labs.synthetic.fixture_store import ENDPOINT_FILES, FixtureStore


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


def test_missing_folder_lookup_returns_empty(caplog):
    store = FixtureStore(drive=FakeDrive({}), folder_lookup=lambda _: None)
    assert store.load_endpoint(42, "user_visits") == []
    assert "no gdrive folder" in caplog.text.lower()


def test_endpoint_files_covers_all_supported_endpoints():
    assert ENDPOINT_FILES == {
        "": "opportunity.json",
        "user_visits": "user_visits.json",
        "user_data": "user_data.json",
        "completed_works": "completed_works.json",
        "completed_module": "completed_module.json",
        "app_structure": "app_structure.json",
        "payment": "payment.json",
        "invoice": "invoice.json",
        "assessment": "assessment.json",
    }


def test_drive_api_error_on_list_returns_empty(caplog):
    class FailingDrive:
        def list_folder(self, _):
            from connect_labs.labs.synthetic.gdrive import DriveAPIError

            raise DriveAPIError("boom")

        def download_file(self, _):
            raise AssertionError("should not be called")

    store = FixtureStore(drive=FailingDrive(), folder_lookup=lambda _: "folder-a")
    assert store.load_endpoint(42, "user_visits") == []
    assert "list_folder failed" in caplog.text.lower()


def test_drive_api_error_on_download_returns_empty(caplog):
    class FailingDrive:
        def list_folder(self, _):
            return {"user_visits.json": "file-xyz"}

        def download_file(self, _):
            from connect_labs.labs.synthetic.gdrive import DriveAPIError

            raise DriveAPIError("boom")

    store = FixtureStore(drive=FailingDrive(), folder_lookup=lambda _: "folder-a")
    assert store.load_endpoint(42, "user_visits") == []
    assert "download_file failed" in caplog.text.lower()


def test_malformed_json_returns_empty(caplog):
    """Operator edits in Drive can leave a file with broken JSON. Don't 500
    the caller — degrade to empty with a warning and cache the result so we
    don't re-download on every read."""
    store, drive = _store_with(42, "folder-a", {"user_visits.json": b"{not valid json"})

    assert store.load_endpoint(42, "user_visits") == []
    assert "malformed json" in caplog.text.lower()

    # Second call hits the cached [] without re-downloading.
    assert store.load_endpoint(42, "user_visits") == []
    assert drive.download_calls == 1


# ---------------------------------------------------------------------------
# Shared (L2) cache — the cost properties, not just the behaviour
# ---------------------------------------------------------------------------


def test_second_process_does_not_refetch_from_drive():
    """The regression that motivated the shared tier.

    Prod serves on six independent processes (WEB_CONCURRENCY=3 x 2 tasks). With
    only a per-instance dict, a warm entry was reachable about one load in six,
    so a multi-opp workflow run page re-paid its whole Drive fan-out on nearly
    every load — 12-16s of it. A second store standing in for a second process
    must serve from the shared tier and touch Drive zero times.
    """
    files = {"user_visits.json": json.dumps([{"id": 1}]).encode()}
    first, first_drive = _store_with(42, "folder-a", files)
    assert first.load_endpoint(42, "user_visits") == [{"id": 1}]
    assert (first_drive.list_calls, first_drive.download_calls) == (1, 1)

    second, second_drive = _store_with(42, "folder-a", files)
    assert second.load_endpoint(42, "user_visits") == [{"id": 1}]
    assert (second_drive.list_calls, second_drive.download_calls) == (0, 0)


def test_reload_invalidates_other_processes_too():
    """`reload` used to clear only the dict of whichever process served it,
    leaving the other five on the stale fixture — so it worked roughly one time
    in six. The generation bump is what makes it global."""
    files = {"user_visits.json": json.dumps([{"v": 1}]).encode()}
    first, _ = _store_with(42, "folder-a", files)
    second, second_drive = _store_with(42, "folder-a", files)
    first.load_endpoint(42, "user_visits")

    first.reload(42)

    # The second process never called reload, but must not serve the retired entry.
    assert second.load_endpoint(42, "user_visits") == [{"v": 1}]
    assert second_drive.download_calls == 1


def test_oversized_payload_is_not_pushed_through_redis():
    """A big opp's user_visits.json runs to many MB. Caching that shared would
    trade one slow dependency for another, so above the byte gate L1 still
    caches it and L2 declines — the second process re-fetches rather than
    dragging megabytes through Redis on every miss."""
    from connect_labs.labs.synthetic import fixture_store as fs

    big = json.dumps([{"pad": "x" * (fs.SHARED_CACHE_MAX_BYTES + 1024)}]).encode()
    assert len(big) > fs.SHARED_CACHE_MAX_BYTES

    first, _ = _store_with(42, "folder-a", {"user_visits.json": big})
    first.load_endpoint(42, "user_visits")

    second, second_drive = _store_with(42, "folder-a", {"user_visits.json": big})
    second.load_endpoint(42, "user_visits")
    assert second_drive.download_calls == 1


def test_unavailable_shared_cache_degrades_to_local_only(monkeypatch):
    """Redis being down must cost speed, never correctness: every shared access
    is wrapped, so the store falls back to exactly its old L1-only behaviour."""
    from connect_labs.labs.synthetic import fixture_store as fs

    def boom(*_a, **_k):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(fs.cache, "get", boom)
    monkeypatch.setattr(fs.cache, "set", boom)

    store, drive = _store_with(42, "folder-a", {"user_visits.json": b"[]"})
    assert store.load_endpoint(42, "user_visits") == []
    assert store.load_endpoint(42, "user_visits") == []
    assert drive.download_calls == 1
