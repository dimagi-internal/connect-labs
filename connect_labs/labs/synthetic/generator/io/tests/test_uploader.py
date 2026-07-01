import json

import pytest
from django.test import override_settings

from connect_labs.labs.synthetic.generator.io.uploader import UploadResult, upload_and_register, upload_fixtures
from connect_labs.labs.synthetic.models import SyntheticOpportunity


class _FakeDrive:
    def __init__(self):
        self.created_folder = None
        self.uploads: list[tuple[str, str, bytes]] = []

    def create_folder(self, name, parent_id):
        self.created_folder = (name, parent_id)
        return f"folder-{name}"

    def upload_file(self, folder_id, filename, content):
        self.uploads.append((folder_id, filename, content))
        return f"file-{filename}"


@pytest.mark.django_db
@override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="parent-abc")
def test_upload_and_register_uploads_five_files_and_creates_row():
    drive = _FakeDrive()
    fixtures = {
        "opportunity": {"id": 1237, "name": "X"},
        "user_visits": [{"id": "v1"}],
        "user_data": [{"username": "asha"}],
        "completed_works": [],
        "completed_module": [],
    }
    result = upload_and_register(
        drive=drive,
        opportunity_id=1237,
        opportunity_name="X",
        fixtures=fixtures,
    )
    assert isinstance(result, UploadResult)
    assert result.folder_id.startswith("folder-")
    assert result.folder_url == f"https://drive.google.com/drive/folders/{result.folder_id}"
    filenames = sorted(name for _, name, _ in drive.uploads)
    assert filenames == sorted(
        [
            "opportunity.json",
            "user_visits.json",
            "user_data.json",
            "completed_works.json",
            "completed_module.json",
        ]
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=1237)
    assert row.enabled is True
    assert row.gdrive_folder_id == result.folder_id


@pytest.mark.django_db
@override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="parent-abc")
def test_upload_and_register_updates_existing_row():
    SyntheticOpportunity.objects.create(
        opportunity_id=1237,
        gdrive_folder_id="old-folder",
        enabled=False,
    )
    drive = _FakeDrive()
    fixtures = {
        "opportunity": {},
        "user_visits": [],
        "user_data": [],
        "completed_works": [],
        "completed_module": [],
    }
    upload_and_register(
        drive=drive,
        opportunity_id=1237,
        opportunity_name="X",
        fixtures=fixtures,
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=1237)
    assert row.gdrive_folder_id != "old-folder"
    assert row.enabled is True


@override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="")
def test_upload_and_register_requires_parent_folder_setting():
    drive = _FakeDrive()
    with pytest.raises(RuntimeError, match="LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID"):
        upload_and_register(
            drive=drive,
            opportunity_id=1,
            opportunity_name="X",
            fixtures={
                k: [] for k in ("opportunity", "user_visits", "user_data", "completed_works", "completed_module")
            },
        )


class _FakeDriveSimple:
    def __init__(self):
        self.uploaded = {}

    def create_folder(self, name, parent_id):
        return "folder123"

    def upload_file(self, folder_id, filename, content):
        self.uploaded[filename] = content


def test_upload_fixtures_writes_app_structure(settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    drive = _FakeDriveSimple()
    fixtures = {
        "opportunity": {},
        "user_visits": [],
        "user_data": [],
        "completed_works": [],
        "completed_module": [],
        "app_structure": {"learn_app": None, "deliver_app": {"modules": []}},
    }
    upload_fixtures(drive=drive, opportunity_id=10000, fixtures=fixtures)
    assert "app_structure.json" in drive.uploaded
    assert drive.uploaded["app_structure.json"] == json.dumps(fixtures["app_structure"]).encode()


def test_upload_fixtures_skips_app_structure_when_absent(settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    drive = _FakeDriveSimple()
    fixtures = {
        "opportunity": {},
        "user_visits": [],
        "user_data": [],
        "completed_works": [],
        "completed_module": [],
    }
    upload_fixtures(drive=drive, opportunity_id=10000, fixtures=fixtures)
    assert "app_structure.json" not in drive.uploaded
