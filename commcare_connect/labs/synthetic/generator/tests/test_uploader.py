import pytest
from django.test import override_settings

from commcare_connect.labs.synthetic.generator.uploader import UploadResult, upload_and_register
from commcare_connect.labs.synthetic.models import SyntheticOpportunity


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
