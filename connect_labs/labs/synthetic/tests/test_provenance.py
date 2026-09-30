"""An opp's data counts as generated only while it serves the folder the generator wrote."""

import pytest

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import (
    all_generated,
    generated_opportunity_ids,
    is_generated,
    mark_generated,
)
from connect_labs.labs.synthetic.provisioning import register_labs_only_opp


def _opp(opportunity_id, folder="gen-folder", labs_only=True):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opportunity_id, gdrive_folder_id=folder, labs_only=labs_only, enabled=True
    )


@pytest.mark.django_db
def test_an_opp_is_not_generated_until_the_generator_marks_it():
    _opp(10501)
    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=10501))

    mark_generated(10501, "gen-folder")

    assert is_generated(SyntheticOpportunity.objects.get(opportunity_id=10501))


@pytest.mark.django_db
def test_repointing_at_another_folder_unmarks_it():
    _opp(10502)
    mark_generated(10502, "gen-folder")

    register_labs_only_opp(opportunity_id=10502, label="repointed", gdrive_folder_id="a-prod-dump")

    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=10502))


@pytest.mark.django_db
def test_a_real_backed_row_is_never_generated():
    _opp(501, labs_only=False)
    mark_generated(501, "gen-folder")

    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=501))


@pytest.mark.django_db
def test_all_generated_needs_every_id():
    _opp(10503)
    _opp(10504)
    mark_generated(10503, "gen-folder")

    assert generated_opportunity_ids([10503, 10504, "junk"]) == {10503}
    assert all_generated([10503])
    assert not all_generated([10503, 10504])
    assert not all_generated([10503, 999999])
    assert not all_generated([])
