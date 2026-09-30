"""The local supply dev seed invents its programme, so the programme's opp is generated."""

import io

import pytest

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated
from connect_labs.supply_chain.management.commands.supply_dev_seed import PROGRAMME_ID, Command


@pytest.mark.django_db
def test_the_dev_seed_programme_is_generated():
    command = Command(stdout=io.StringIO())
    command._synthetic_programme()
    assert is_generated(SyntheticOpportunity.objects.get(opportunity_id=PROGRAMME_ID))
