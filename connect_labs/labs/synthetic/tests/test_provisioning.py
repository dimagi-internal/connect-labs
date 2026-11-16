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


# ─── id reservation: concurrent clone jobs ─────────────────────────────────


def test_reserved_ids_are_never_handed_out_twice():
    from connect_labs.labs.synthetic.models import LabsOnlyIdReservation
    from connect_labs.labs.synthetic.provisioning import reserve_labs_only_id

    program_a = allocate_shared_program_id()
    program_b = allocate_shared_program_id()  # a second job starting before the first registers anything
    opp_a = reserve_labs_only_id(above=program_a)
    opp_b = reserve_labs_only_id(above=program_b)
    ids = [program_a, program_b, opp_a, opp_b]
    assert len(set(ids)) == 4
    assert opp_a > program_a and opp_b > program_b
    assert set(LabsOnlyIdReservation.objects.values_list("value", flat=True)) == set(ids)


def test_program_id_of_a_job_that_registered_nothing_is_not_reissued():
    first = allocate_shared_program_id()  # its clone job fails before registering any opp
    assert allocate_shared_program_id() > first


def test_reserve_moves_on_when_another_job_took_the_id(monkeypatch):
    from connect_labs.labs.synthetic.models import LabsOnlyIdReservation, SyntheticOpportunity
    from connect_labs.labs.synthetic.provisioning import reserve_labs_only_id

    # Another job reserved 10_000 between our read and our insert.
    LabsOnlyIdReservation.objects.create(value=10_000, kind=LabsOnlyIdReservation.KIND_OPPORTUNITY)
    reads = iter([10_000, 10_001])
    monkeypatch.setattr(SyntheticOpportunity, "next_labs_only_opp_id", classmethod(lambda cls: next(reads)))
    assert reserve_labs_only_id() == 10_001


def test_register_without_id_reserves_one_above_the_program():
    program_id = allocate_shared_program_id()
    a = register_labs_only_opp(label="a", gdrive_folder_id="fa", program_id=program_id)
    b = register_labs_only_opp(label="b", gdrive_folder_id="fb", program_id=program_id)
    assert program_id < a.opportunity_id < b.opportunity_id
