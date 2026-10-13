"""The example organisations run their own procurement, and Dimagi plays no part (#2458).

THIS REPOSITORY IS PUBLIC. Everything seeded here is invented.
"""

from unittest.mock import patch

import pytest

from connect_labs.labs.access.scopes import Caller
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.demo import org_procurement
from connect_labs.supply_chain.models import Contract, Tender
from connect_labs.supply_chain.runner import PARTNER, PROGRAMME, running_org, standing

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]


@pytest.fixture(autouse=True)
def quiet_registry():
    """Registering a labs-only opp reloads its Drive fixtures; there are none here."""
    with patch("connect_labs.labs.synthetic.provisioning.invalidate_synthetic_caches"):
        yield


@pytest.mark.parametrize("key", ["oes", "llo"])
def test_each_organisation_is_the_buyer_and_its_admin_is_the_programme(key):
    admin = org_procurement.ensure_admin(org_procurement.SPECS[key])
    result = org_procurement.seed(key, admin=admin)

    program_id = result["program_id"]
    org = running_org(program_id)
    assert org.slug == f"labs-synthetic-demo-{key}" and org.slug == result["org_slug"]
    assert Tender.objects.filter(program_id=program_id).count() == 2
    assert set(Contract.objects.filter(program_id=program_id).values_list("buyer_org_id", flat=True)) == {org.pk}

    with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
        access = SupplyDataAccess(program_id=program_id, user=admin, caller=Caller(user=admin))
    assert standing(access).role == PROGRAMME

    from django.contrib.auth import get_user_model

    lead = get_user_model().objects.create(username="lead", email="lead@dimagi.com")
    with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
        dimagi = SupplyDataAccess(program_id=program_id, user=lead, caller=Caller(user=lead))
    with patch("connect_labs.supply_chain.identity._caller_organizations", return_value=[]):
        assert standing(dimagi).role == PARTNER


def test_seeding_twice_is_refused_rather_than_doubled():
    org_procurement.seed("llo")
    again = org_procurement.seed("llo")
    assert again["seeded"] is False


def test_a_label_registered_under_another_organisation_is_refused():
    from connect_labs.labs.synthetic.provisioning import register_labs_only_opp

    register_labs_only_opp(
        opportunity_id=10950, label=org_procurement.SPECS["oes"]["label"], org_name="Someone else",
        program_id=10950, gdrive_folder_id="",
    )  # fmt: skip
    with pytest.raises(ValueError, match="another organisation"):
        org_procurement.seed("oes")
