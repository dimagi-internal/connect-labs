"""The photo-verification demo invents its audit sessions, so its opps count as generated."""

import pytest

from connect_labs.audit.photo_verification_demo import C3HD_OPP_ID, EHA_OPP_ID, seed_demo
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated


@pytest.mark.django_db
def test_the_seeded_demo_opps_are_generated():
    seed_demo()
    for opp_id in (EHA_OPP_ID, C3HD_OPP_ID):
        assert is_generated(SyntheticOpportunity.objects.get(opportunity_id=opp_id))


@pytest.mark.django_db
def test_a_demo_opp_already_pointed_at_a_folder_is_not_marked():
    """The seeder only knows what IT wrote; a fixture folder somebody registered is not that."""
    SyntheticOpportunity.objects.create(opportunity_id=EHA_OPP_ID, gdrive_folder_id="a-dump", labs_only=True)
    seed_demo()
    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=EHA_OPP_ID))
    assert is_generated(SyntheticOpportunity.objects.get(opportunity_id=C3HD_OPP_ID))
