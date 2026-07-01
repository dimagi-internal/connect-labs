import pytest

from connect_labs.labs.synthetic.provisioning import allocate_shared_program_id, register_labs_only_opp

pytestmark = pytest.mark.django_db


def test_register_allocates_id_and_sets_labs_only():
    row = register_labs_only_opp(label="x", gdrive_folder_id="folderA", program_id=10000)
    assert row.opportunity_id >= 10000
    assert row.labs_only is True
    assert row.gdrive_folder_id == "folderA"


def test_register_is_idempotent_and_non_clobbering():
    row = register_labs_only_opp(label="x", gdrive_folder_id="folderA", program_id=10000)
    # Re-register WITHOUT folder/program — must not wipe them.
    again = register_labs_only_opp(opportunity_id=row.opportunity_id, label="x2")
    again.refresh_from_db()
    assert again.gdrive_folder_id == "folderA"
    assert again.program_id == 10000
    assert again.label == "x2"


def test_allocate_shared_program_id_in_reserved_range():
    pid = allocate_shared_program_id()
    assert pid >= 10000
