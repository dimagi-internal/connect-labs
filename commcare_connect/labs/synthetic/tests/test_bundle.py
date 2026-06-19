from commcare_connect.labs.synthetic.bundle import (
    GDriveBundleStore,
    LocalBundleStore,
    make_bundle_store,
    read_bundle,
    scrub_opportunity,
    write_bundle,
)


class _FakeDrive:
    """In-memory Drive: folders + multipart files, mirroring DriveClient's surface."""

    def __init__(self):
        self.folders = {}  # folder_id -> (name, parent_id)
        self.files = {}  # file_id -> (parent_folder_id, name, content_bytes)
        self._n = 0

    def create_folder(self, name, parent_id):
        self._n += 1
        fid = f"folder{self._n}"
        self.folders[fid] = (name, parent_id)
        return fid

    def upload_file(self, folder_id, filename, content):
        self._n += 1
        xid = f"file{self._n}"
        self.files[xid] = (folder_id, filename, content)
        return xid

    def list_folder(self, folder_id):
        out = {}
        for fid, (name, parent) in self.folders.items():
            if parent == folder_id:
                out[name] = fid
        for xid, (parent, name, _content) in self.files.items():
            if parent == folder_id:
                out[name] = xid
        return out

    def download_file(self, file_id):
        return self.files[file_id][2]


def test_scrub_drops_row_level_lists():
    detail = {"id": 523, "name": "KMC", "payment_units": [{"id": 1}], "deliver_units": [{"id": 2}], "flws": [{"x": 1}]}
    scrubbed = scrub_opportunity(detail)
    assert scrubbed["name"] == "KMC"
    assert "flws" not in scrubbed
    # payment_units / deliver_units are program config and may be kept:
    assert "payment_units" in scrubbed


def test_write_then_read_roundtrip(tmp_path):
    bundle = write_bundle(
        tmp_path,
        523,
        manifest_yaml="opportunity_id: 10000\n",
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "KMC"},
    )
    loaded = read_bundle(bundle)
    assert loaded.source_opp_id == 523
    assert loaded.app_structure["deliver_app"] == {"modules": []}
    assert "opportunity_id: 10000" in loaded.manifest_yaml


def test_source_opp_id_comes_from_opportunity_not_dir_or_manifest(tmp_path):
    # Bundle written under dir "999" with a manifest opportunity_id of 10000, but
    # the opportunity detail id is 523 — source_opp_id must follow opportunity["id"].
    d = write_bundle(
        tmp_path,
        999,
        manifest_yaml="opportunity_id: 10000\n",
        app_structure={"deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "K"},
    )
    assert read_bundle(d).source_opp_id == 523


def test_make_bundle_store_local_roundtrip(tmp_path):
    store = make_bundle_store(str(tmp_path))
    assert isinstance(store, LocalBundleStore)
    handle = store.write(
        523,
        manifest_yaml="opportunity_id: 10000\n",
        app_structure={"deliver_app": {"modules": []}},
        opportunity={"id": 523},
    )
    loaded = store.read(handle)
    assert loaded.source_opp_id == 523
    assert loaded.app_structure["deliver_app"] == {"modules": []}
    assert store.list_handles() == [handle]


def test_gdrive_bundle_store_roundtrip():
    drive = _FakeDrive()
    root = drive.create_folder("run", "parent")
    store = GDriveBundleStore(drive, root)
    handle = store.write(
        523,
        manifest_yaml="opportunity_id: 10000\n",
        app_structure={"deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "KMC", "flws": [{"x": 1}]},
    )
    loaded = store.read(handle)
    assert loaded.source_opp_id == 523
    assert loaded.manifest_yaml.strip() == "opportunity_id: 10000"
    assert loaded.app_structure["deliver_app"] == {"modules": []}
    # PII scrubbed on write even through Drive:
    assert "flws" not in loaded.opportunity
    # list_handles -> one subfolder, readable back:
    assert [store.read(h).source_opp_id for h in store.list_handles()] == [523]


def test_make_bundle_store_gdrive_prefix():
    drive = _FakeDrive()
    root = drive.create_folder("run", "parent")
    store = make_bundle_store(f"gdrive:{root}", drive=drive)
    assert isinstance(store, GDriveBundleStore)
    assert store.root_folder_id == root


def test_make_bundle_store_gdrive_requires_drive():
    import pytest

    with pytest.raises(ValueError):
        make_bundle_store("gdrive:abc")  # no drive client
