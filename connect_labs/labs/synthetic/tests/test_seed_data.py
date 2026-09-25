"""The seeder's facts come from Drive, never from this public repository."""

import json

import pytest

from connect_labs.labs.synthetic.seed_data import SeedDataError, load_seed_data


class FakeDrive:
    def __init__(self, files):
        self._files = files

    def list_folder(self, folder_id):
        return {name: f"id-{name}" for name in self._files}

    def download_file(self, file_id):
        return self._files[file_id.removeprefix("id-")]


def _valid():
    return {
        "orgs": [{"slug": "a-partner", "name": "A Partner", "connect_organization_id": 1}],
        "commodities": [{"slug": "a-product", "name": "A Product", "base_unit": "sachet"}],
        "rounds": [{"label": "A round", "lines": []}],
    }


def test_it_returns_the_parsed_document():
    drive = FakeDrive({"oes-demo.json": json.dumps(_valid()).encode()})
    assert load_seed_data("folder", client=drive)["orgs"][0]["slug"] == "a-partner"


def test_a_missing_file_names_the_folder_and_the_file():
    drive = FakeDrive({"something-else.json": b"{}"})
    with pytest.raises(SeedDataError) as caught:
        load_seed_data("folder-123", client=drive)
    assert "folder-123" in str(caught.value) and "oes-demo.json" in str(caught.value)


def test_unreadable_json_is_refused_rather_than_half_used():
    drive = FakeDrive({"oes-demo.json": b"{not json"})
    with pytest.raises(SeedDataError):
        load_seed_data("folder", client=drive)


@pytest.mark.parametrize("missing", ["orgs", "commodities", "rounds"])
def test_a_document_missing_a_required_section_is_refused(missing):
    doc = _valid()
    del doc[missing]
    drive = FakeDrive({"oes-demo.json": json.dumps(doc).encode()})
    with pytest.raises(SeedDataError) as caught:
        load_seed_data("folder", client=drive)
    assert missing in str(caught.value)


def test_an_org_without_its_connect_id_is_refused():
    doc = _valid()
    del doc["orgs"][0]["connect_organization_id"]
    drive = FakeDrive({"oes-demo.json": json.dumps(doc).encode()})
    with pytest.raises(SeedDataError) as caught:
        load_seed_data("folder", client=drive)
    assert "connect_organization_id" in str(caught.value)
