"""A dump is only a profiling input: profiling it yields the same kind of bundle a live profile does."""

import json

import pytest

from connect_labs.labs.synthetic.bundle import LocalBundleStore, read_bundle
from connect_labs.labs.synthetic.clone_from_prod import profile_dump_to_bundle


class _DumpDrive:
    def __init__(self, files):
        self.files = {name: json.dumps(payload).encode() for name, payload in files.items()}

    def list_folder(self, folder_id):
        return {name: name for name in self.files}

    def download_file(self, file_id):
        return self.files[file_id]


def _visits():
    return [
        {
            "username": "real.worker.name",
            "visit_date": f"2026-01-{1 + 7 * i:02d}",
            "status": "approved",
            "entity_id": f"case-{e}",
            "form_json": {"form": {"weight": 1500.0 + 100 * i + 11 * e}},
        }
        for e in range(6)
        for i in range(3)
    ]


def test_profiling_a_dump_writes_a_bundle_and_no_rows(tmp_path):
    drive = _DumpDrive(
        {
            "opportunity.json": {"id": 874, "name": "KMC"},
            "user_visits.json": _visits(),
            "user_data.json": [],
        }
    )
    handle = profile_dump_to_bundle("dump-folder", drive=drive, store=LocalBundleStore(tmp_path), mirror=True)

    bundle = read_bundle(handle)
    assert bundle.source_opp_id == 874
    assert "real.worker.name" not in bundle.manifest_yaml  # owners are persona ids
    assert "case-0" not in bundle.manifest_yaml  # cases are not carried by id


def test_a_dump_without_an_opportunity_id_is_refused(tmp_path):
    drive = _DumpDrive({"user_visits.json": _visits()})
    with pytest.raises(ValueError):
        profile_dump_to_bundle("dump-folder", drive=drive, store=LocalBundleStore(tmp_path))
